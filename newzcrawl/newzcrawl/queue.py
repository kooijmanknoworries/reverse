"""Download queue engine.

Turns an :class:`~newzcrawl.nzb.NZB` into files on disk: fetches each
segment (article) over NNTP, decodes its yEnc payload, verifies the CRC,
and reassembles the segments into the original binary using the part
offsets carried in the yEnc 1.0.3 trailers (falling back to sequential
concatenation for single-part / 1.0.1 posts).

Design goals that mirror the original NewsLeecher queue:

* parallel article fetch/decode across a bounded thread pool;
* per-part retry with reconnection on transient failures;
* "incomplete" files are still written (with the good parts placed at
  their byte offsets) and the missing part numbers are reported, so a
  user can re-fetch just those;
* a part whose yEnc CRC does not match is treated as corrupt and left as
  a hole rather than corrupting the file.

The engine is transport-agnostic: it takes a ``connection_factory``
callable that returns a *connected* :class:`NNTPClient`, so the same
engine drives the real server and the in-process mock in tests.
"""

from __future__ import annotations

import logging
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Callable

from newzcrawl.nntp_client import NNTPClient, NNTPError
from newzcrawl.nzb import NZB, NZBItem, NZBSegment
from newzcrawl import yenc

log = logging.getLogger("newzcrawl.queue")

# NNTP codes that mean "this article is gone / never existed" and are
# therefore NOT worth retrying (reconnecting would not help).
_UNAVAILABLE_CODES = {420, 422, 423, 430}


# ---------------------------------------------------------------------------
# result containers
# ---------------------------------------------------------------------------

@dataclass
class PartResult:
    segment: NZBSegment
    ok: bool = False
    error: str = ""
    decoded: bytes | None = None
    info: yenc.YEncPartInfo | None = None
    crc_ok: bool | None = None


@dataclass
class ItemResult:
    item: NZBItem
    ok: bool = False                 # a file was produced
    complete: bool = False           # no holes and no failed parts
    output_path: str | None = None
    expected: int = 0
    good: int = 0
    corrupt: int = 0
    missing: int = 0
    size: int = 0
    error: str = ""
    parts: list[PartResult] = field(default_factory=list)
    holes: list[tuple[int, int]] = field(default_factory=list)

    @property
    def missing_parts(self) -> list[int]:
        out = []
        for p in self.parts:
            if not p.ok and p.segment.index is not None:
                out.append(p.segment.index)
        return sorted(out)


# ---------------------------------------------------------------------------
# engine
# ---------------------------------------------------------------------------

class DownloadQueue:
    """Download an :class:`NZB` to ``out_dir``.

    ``connection_factory`` must return a fresh, connected
    :class:`NNTPClient`. One connection is created per worker thread and
    reused for every part that worker handles; it is transparently
    re-created if a connection-level error is hit.
    """

    def __init__(self, connection_factory: Callable[[], NNTPClient],
                 out_dir: str | os.PathLike, *,
                 max_workers: int = 4, max_retries: int = 2,
                 verify_crc: bool = True, skip_existing: bool = True):
        self._factory = connection_factory
        self.out_dir = str(out_dir)
        self.max_workers = max(1, int(max_workers))
        self.max_retries = max(0, int(max_retries))
        self.verify_crc = verify_crc
        self.skip_existing = skip_existing
        self._tls = threading.local()
        os.makedirs(self.out_dir, exist_ok=True)

    # -- per-thread connection handling --------------------------------
    def _client(self) -> NNTPClient:
        c = getattr(self._tls, "client", None)
        if c is None:
            c = self._factory()
            self._tls.client = c
        return c

    def _reset_client(self) -> None:
        c = getattr(self._tls, "client", None)
        if c is not None:
            try:
                c.close()
            except Exception:
                pass
            self._tls.client = None

    def close(self) -> None:
        c = getattr(self._tls, "client", None)
        if c is not None:
            try:
                c.close()
            except Exception:
                pass
            self._tls.client = None

    # -- public API ----------------------------------------------------
    def download(self, nzb: NZB,
                 on_item_done: Callable[[ItemResult], None] | None = None
                 ) -> list[ItemResult]:
        """Download every item in ``nzb``. Returns one result per item."""
        results: list[ItemResult] = []
        for item in nzb.items:
            res = self._download_item(item, nzb)
            results.append(res)
            if on_item_done is not None:
                on_item_done(res)
        return results

    def download_item(self, item: NZBItem, nzb: NZB | None = None
                      ) -> ItemResult:
        return self._download_item(item, nzb)

    # -- per item ------------------------------------------------------
    def _download_item(self, item: NZBItem, nzb: NZB | None) -> ItemResult:
        res = ItemResult(item=item, expected=len(item.segments))
        segs = list(item.segments)
        if not segs:
            res.error = "no segments in item"
            return res

        out_path = self._safe_path(item.name)
        if self.skip_existing and self._is_complete_file(out_path, res, segs):
            res.ok = True
            res.complete = True
            res.output_path = out_path
            res.size = os.path.getsize(out_path)
            res.good = len(segs)
            res.error = "already downloaded"
            return res

        # fetch all parts in parallel (bounded pool, thread-local conns)
        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            futs = {pool.submit(self._fetch_part, item, seg): seg
                    for seg in segs}
            for fut in as_completed(futs):
                seg = futs[fut]
                try:
                    pr = fut.result()
                except Exception as e:  # defensive: a worker blew up
                    pr = PartResult(segment=seg, ok=False,
                                    error=f"worker error: {e!r}")
                res.parts.append(pr)
        res.parts.sort(key=lambda pr: (pr.segment.index is None,
                                       pr.segment.index or 0))

        # tally
        for pr in res.parts:
            if pr.ok:
                res.good += 1
            elif pr.error.startswith("crc") or pr.error.startswith("decode"):
                res.corrupt += 1
            else:
                res.missing += 1

        # reassemble
        data, size, holes, hole_msgs = self._reassemble(res)
        res.holes = holes
        res.error = "; ".join(hole_msgs[:8])
        if data is None:
            # nothing usable decoded at all
            res.ok = False
            res.error = res.error or "no parts could be decoded"
            return res

        res.output_path = out_path
        res.size = len(data)
        with open(out_path, "wb") as f:
            f.write(data)
        res.ok = True
        res.complete = not res.holes and res.corrupt == 0 and res.missing == 0
        return res

    # -- per part ------------------------------------------------------
    def _fetch_part(self, item: NZBItem, seg: NZBSegment) -> PartResult:
        pr = PartResult(segment=seg, info=yenc.YEncPartInfo())
        last_err = ""
        for attempt in range(self.max_retries + 1):
            try:
                art = self._client().fetch(seg.message_id,
                                           group=(item.groups[0]
                                                  if item.groups else None))
                if not art.has_yenc and not art.decoded:
                    # plain text / non-yEnc article: keep raw body
                    pr.decoded = art.body
                    pr.ok = True
                    pr.crc_ok = None
                    return pr
                info = yenc.parse_part_info(art.body)
                pr.info = info
                dec, crc = yenc.decode(art.body)
                pr.decoded = dec
                if self.verify_crc and crc is not None:
                    pr.crc_ok = yenc.check_crc(dec, crc)
                    if not pr.crc_ok:
                        pr.ok = False
                        pr.error = (f"crc mismatch (part {info.part}): "
                                    f"got {yenc._crc32(dec):08x} want {crc}")
                        return pr
                pr.crc_ok = pr.crc_ok or True
                pr.ok = True
                return pr
            except NNTPError as e:
                if e.code in _UNAVAILABLE_CODES:
                    pr.error = f"article unavailable (NNTP {e.code})"
                    return pr
                last_err = f"NNTP {e.code}: {e.message}"
                log.warning("part %s attempt %d failed (%s); will retry",
                            seg.message_id, attempt + 1, last_err)
                self._reset_client()
            except OSError as e:
                last_err = f"connection error: {e}"
                log.warning("part %s attempt %d: %s",
                            seg.message_id, attempt + 1, last_err)
                self._reset_client()
            except ValueError as e:
                # base85 / framing failure = genuinely corrupt article
                pr.error = f"decode: {e}"
                pr.decoded = None
                return pr
        pr.error = last_err or "unknown error"
        return pr

    # -- reassembly ----------------------------------------------------
    def _reassemble(self, res: ItemResult):
        """Place the good parts into the final file layout.

        Uses the yEnc 1.0.3 ``begin``/``end`` byte offsets when every
        good part carries them; otherwise concatenates in part order.
        Returns ``(data, size, holes, hole_msgs)`` where ``holes`` is the
        list of uncovered ``(start, end)`` byte ranges and ``data`` is
        ``None`` when nothing decoded.
        """
        good = [(p.info, p.decoded) for p in res.parts
                if p.ok and p.decoded is not None and p.info is not None]
        if not good:
            return None, 0, [], []
        with_off = [t for t in good
                    if t[0].begin is not None and t[0].end is not None]
        if with_off and len(with_off) == len(good):
            size = 0
            for info, _ in with_off:
                size = max(size, info.file_size or 0, info.end or 0)
            buf = bytearray(size)
            covered_spans: list[tuple[int, int]] = []
            hole_msgs: list[str] = []
            for info, dec in with_off:
                begin, end = info.begin, info.end
                span = end - begin
                if len(dec) != span:
                    hole_msgs.append(f"part {info.part}: size mismatch "
                                     f"({len(dec)} != {span})")
                    continue
                buf[begin:end] = dec
                covered_spans.append((begin, end))
            holes = self._find_holes(covered_spans, size)
            if holes:
                hole_msgs.append(
                    "uncovered: " + ", ".join(f"[{s}-{e})" for s, e in holes))
            return bytes(buf), size, holes, hole_msgs
        # sequential concatenation in part order (no offset info)
        def key(t):
            info = t[0]
            return (info.part is None, info.part or 0)
        ordered = sorted(good, key=key)
        data = b"".join(dec for _, dec in ordered)
        return data, len(data), [], []

    @staticmethod
    def _find_holes(spans: list[tuple[int, int]], size: int
                    ) -> list[tuple[int, int]]:
        """Return the maximal uncovered ranges in ``[0, size)`` given a
        set of covered ``(begin, end)`` byte spans (overlaps allowed)."""
        holes: list[tuple[int, int]] = []
        pos = 0
        for s, e in sorted(spans):
            if s > pos:
                holes.append((pos, s))
            if e > pos:
                pos = e
        if pos < size:
            holes.append((pos, size))
        return holes

    # -- helpers -------------------------------------------------------
    def _safe_path(self, name: str) -> str:
        base = os.path.basename(name) or "download"
        base = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", base).strip() or "download"
        return os.path.join(self.out_dir, base)

    def _is_complete_file(self, path: str, res: ItemResult, segs) -> bool:
        """True if an existing file already covers every expected byte."""
        if not os.path.exists(path):
            return False
        have = os.path.getsize(path)
        want = 0
        for s in segs:
            want += s.bytes
        # if sizes are unknown/zero we can't verify completeness
        if want == 0:
            return have > 0
        return have == want
