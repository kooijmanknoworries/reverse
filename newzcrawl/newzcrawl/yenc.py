"""yEnc v1.0.3 encoder/decoder.

yEnc is the standard binary encoding for Usenet articles. A yEnc body is
a sequence of base85 lines (first char = group length, ``0`` for a full
4-byte group; the last line of a part ends with ``Z``), optionally
preceded by a ``=y`` part header (``=y line=N``), and terminated by
``=yend crc2=xxxxxxxx`` carrying the reflected CRC-32 (poly 0x04C11DB7)
of the decoded data.

Python's ``base64.a85encode``/``a85decode`` (``adobe=False``) implement
exactly these conventions, so this module is a thin framing layer over
them.
"""

from __future__ import annotations

import base64
import re
import zlib
from dataclasses import dataclass

# base85 alphabet used by yEnc (the "z85-less" ASCII85 subset)
_B85 = set(b"0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
           b"!\"#$%&'()*+,./:;<=>?@[]^_")
# Matches a trailer with a whole-file crc2 (1.0.1) OR a per-part pcrc32
# (1.0.3); a real body always ends in one of these.
_YEND_RE = re.compile(
    rb"^=yend\s+(?:.*?crc2=(?P<crc2>[0-9a-fA-F]{8})|.*?pcrc32=(?P<pcrc>[0-9a-fA-F]{8}))\s*$",
    re.M | re.I)
_HEADER_RE = re.compile(rb"^=y(?:\s+line=\d+)?\s*")


def _crc32(data: bytes) -> int:
    """Reflected CRC-32 (poly 0x04C11DB7) as used by yEnc crc2."""
    return zlib.crc32(data) & 0xFFFFFFFF


def check_crc(data: bytes, crc2_hex: str) -> bool:
    """Verify decoded data against a yEnc crc2 value."""
    try:
        return _crc32(data) == (int(crc2_hex, 16) & 0xFFFFFFFF)
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# yEnc 1.0.3 multi-part metadata
# ---------------------------------------------------------------------------

@dataclass
class YEncPartInfo:
    """Metadata describing one yEnc article's place in a multi-part file.

    ``part``/``parts`` identify the article's position; ``file_size`` is
    the total decoded size (from ``=ybegin size=``); ``begin``/``end``
    are the byte offsets this article contributes (from ``=ypart``);
    ``crc`` is the per-part ``pcrc32`` (CRC of this part's bytes) and
    ``crc2`` is the whole-file CRC (on the final part). Any field may be
    ``None`` when the poster did not include it.
    """
    part: int | None = None
    parts: int | None = None
    file_size: int | None = None
    file_name: str | None = None
    begin: int | None = None
    end: int | None = None
    crc: str | None = None        # pcrc32: per-part CRC
    crc2: str | None = None       # whole-file CRC (final part)

    @property
    def is_final(self) -> bool:
        return self.part is not None and self.parts is not None \
            and self.part >= self.parts


_YBEGIN_RE = re.compile(
    rb"^=ybegin\s+(?P<kv>[\w=]+(?:=\S+)?(?:\s+[\w=]+=\S+)*)\s*$")
_YPART_RE = re.compile(
    rb"^=ypart\s+begin=(?P<begin>\d+)\s+end=(?P<end>\d+)\s*$")
_YEND_PCRC_RE = re.compile(
    rb"^=yend\s+.*?pcrc32=(?P<pcrc>[0-9a-fA-F]{8})\s*$", re.I)
_YEND_CRC2_ONLY_RE = re.compile(
    rb"^=yend\s+.*?crc2=(?P<crc2>[0-9a-fA-F]{8})(?!pcrc)\s*$", re.I)


def _parse_kv(raw: bytes) -> dict[str, str]:
    out: dict[str, str] = {}
    for m in re.finditer(rb"(\w+)=(\"[^\"]*\"|\S+)", raw):
        key = m.group(1).decode("ascii", "replace").lower()
        val = m.group(2).decode("ascii", "replace").strip('"')
        out[key] = val
    return out


def parse_part_info(body: bytes) -> YEncPartInfo:
    """Extract yEnc 1.0.3 multi-part metadata from an article body.

    Tolerant of absent fields and of the 1.0.1 single-part layout
    (``=y`` + ``=yend crc2=``) which yields a one-part file.
    """
    info = YEncPartInfo()
    for line in body.splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith(b"=ybegin"):
            kv = _parse_kv(s)
            if "part" in kv:
                info.part = int(kv["part"])
            if "size" in kv:
                info.file_size = int(kv["size"])
            if "name" in kv:
                info.file_name = kv["name"]
        elif s.startswith(b"=ypart"):
            m = _YPART_RE.match(s)
            if m:
                info.begin = int(m.group("begin"))
                info.end = int(m.group("end"))
        elif s.startswith(b"=yend"):
            m = _YEND_PCRC_RE.match(s)
            if m:
                info.crc = m.group("pcrc").decode("ascii").lower()
                kv = _parse_kv(s)
                if "part" in kv and info.part is None:
                    info.part = int(kv["part"])
                # the final part also carries the whole-file crc2
                m2 = re.search(rb"crc2=([0-9a-fA-F]{8})", s)
                if m2:
                    info.crc2 = m2.group(1).decode("ascii").lower()
                continue
            m = _YEND_CRC2_ONLY_RE.match(s)
            if m:
                # 1.0.1 single-part: the one crc2 covers the whole file
                info.crc = m.group("crc2").decode("ascii").lower()
                info.crc2 = info.crc
                if info.part is None:
                    info.part = 1
                    info.parts = 1
    if info.part is None:
        # 1.0.1 single-part: =y header, no part number anywhere
        if body.lstrip()[:2] == b"=y":
            info.part = 1
            info.parts = 1
    return info


def parse_part_from_subject(subject: str) -> tuple[int | None, int | None]:
    """Return (part, total) parsed from a ``(N/M)`` subject convention.

    Understands the common Usenet forms ``(182/348)``, ``(182 of 348)``
    and ``[182/348]``. Returns ``(None, None)`` when absent.
    """
    m = re.search(r"[\(\[]\s*(\d+)\s*(?:/|of|-)\s*(\d+)\s*[\)\]]", subject or "")
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.search(r"\bpart\s+(\d+)\s+of\s+(\d+)", subject or "", re.I)
    if m:
        return int(m.group(1)), int(m.group(2))
    return None, None


def _split_parts(body: bytes) -> list[bytes]:
    """Split a yEnc body into the base85 payload of each ``=y`` part.

    ``=`` is itself a valid base85 character, so data lines may begin
    with it. The disambiguator is the space: yEnc directives (``=y
    line=N``, ``=ybegin ...``, ``=ypart ...``, ``=yend ...``) contain a
    space, base85 data never does. A bare ``=y`` line (no space) always
    starts a new 1.0.1 part. Everything else is data.
    """
    parts: list[bytearray] = []
    cur: bytearray | None = None
    for raw in body.splitlines():
        s = raw.strip()
        if not s:
            continue
        if s == b"=y":
            if cur:
                parts.append(bytes(cur))
            cur = bytearray()
            continue
        if s.startswith(b"=") and b" " in s:
            if s.startswith(b"=yend"):
                break
            if s.startswith(b"=y "):
                # 1.0.1 header, possibly with inline data on the same line
                if cur:
                    parts.append(bytes(cur))
                m = _HEADER_RE.match(s)
                cur = bytearray(s[m.end():] if m else b"")
            # =ybegin / =ypart / anything else: framing, not data
            continue
        if cur is None:
            cur = bytearray()
        cur.extend(s)
    if cur:
        parts.append(bytes(cur))
    # NOTE: the per-part trailing 'Z' terminator is stripped by decode(),
    # not here — a group may legitimately END in the data character 'Z',
    # so only exactly one terminator must be removed per part.
    return parts


def decode(body: bytes) -> tuple[bytes, str | None]:
    """Decode a yEnc body (one or more ``=y`` parts).

    Returns ``(raw_bytes, crc_hex_or_None)`` where the CRC is the whole-
    file ``crc2`` for a single-part file or the per-part ``pcrc32`` for a
    1.0.3 part. Raises ValueError on malformed input (missing trailer,
    bad base85).
    """
    m = _YEND_RE.search(body)
    if not m:
        raise ValueError("yEnc body missing =yend trailer")
    # A body holds one part: the per-part pcrc32 (when present) is the CRC
    # of exactly these bytes. The whole-file crc2 only matches after the
    # parts are reassembled, so prefer pcrc32. Extract both fields from the
    # trailer line directly (the shared regex's alternation would match
    # crc2 first and leave pcrc unset when both are present).
    trailer = body[m.start():].splitlines()[0]
    mm = re.search(rb"pcrc32=([0-9a-fA-F]{8})", trailer, re.I)
    if mm:
        crc_hex = mm.group(1).decode("ascii").lower()
    else:
        mm = re.search(rb"crc2=([0-9a-fA-F]{8})", trailer, re.I)
        crc_hex = mm.group(1).decode("ascii").lower() if mm else None
    raw = bytearray()
    for part in _split_parts(body[:m.start()]):
        # A trailing 'Z' terminates the part's final (possibly short)
        # base85 group; it is not data and must be removed before
        # decoding. A 'Z' anywhere else is a legitimate data byte.
        if part.endswith(b"Z"):
            part = part[:-1]
        try:
            raw.extend(base64.a85decode(part, adobe=False))
        except (ValueError, OverflowError) as e:
            raise ValueError(f"invalid yEnc base85 payload: {e}") from e
    return bytes(raw), crc_hex


def encode(data: bytes) -> bytes:
    """Encode raw bytes as a single yEnc part: one ``=y`` header, 32
    raw bytes per line, single ``Z`` terminator, ``=yend`` trailer."""
    out = bytearray(b"=y\n")
    n = len(data)
    i = 0
    while i < n:
        chunk = data[i:i + 32]
        i += 32
        enc = base64.a85encode(chunk, adobe=False)
        if i >= n:
            enc += b"Z"
        out.extend(enc)
        out.extend(b"\r\n")
    out.extend(b"=yend crc2=%08x\r\n" % _crc32(data))
    return bytes(out)


def encode_multipart(data: bytes, filename: str, part_size: int = 180_000):
    """Encode ``data`` as a list of yEnc 1.0.3 part bodies.

    Each returned body is one self-contained NNTP article payload:
    ``=ybegin part=N line=... size=... name=...`` followed by the part's
    base85 data (32 raw bytes per line) and ``=yend size=... part=N
    pcrc32=...`` (the final part also carries the whole-file ``crc2=``).
    The parts' ``=ypart begin=/end=`` offsets tile ``data`` exactly, which
    is what :func:`~newzcrawl.queue.DownloadQueue` reassembly relies on.
    """
    total = len(data)
    n_parts = max(1, -(-total // part_size))
    crc2 = _crc32(data)
    bodies: list[bytes] = []
    pos = 0
    for i in range(1, n_parts + 1):
        chunk = data[pos:pos + part_size]
        begin = pos
        end = pos + len(chunk)
        pos = end
        out = bytearray()
        out.extend(b"=ybegin part=%d line=128 size=%d name=%s\r\n"
                   % (i, total, _qname(filename)))
        out.extend(b"=ypart begin=%d end=%d\r\n" % (begin, end))
        j = 0
        while j < len(chunk):
            piece = chunk[j:j + 32]
            j += 32
            enc = base64.a85encode(piece, adobe=False)
            if j >= len(chunk):
                enc += b"Z"
            out.extend(enc)
            out.extend(b"\r\n")
        trailer = ("=yend size=%d part=%d pcrc32=%08x" %
                   (len(chunk), i, _crc32(chunk)))
        if i == n_parts:
            trailer += " crc2=%08x" % crc2
        out.extend(trailer.encode("ascii"))
        out.extend(b"\r\n")
        bodies.append(bytes(out))
    return bodies


def _qname(name: str) -> bytes:
    """Quote a filename for a yEnc ``name=`` field (space -> ``_``)."""
    return name.replace(" ", "_").encode("utf-8", "replace")
