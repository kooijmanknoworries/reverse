"""Usenet article search.

The original NewsLeecher ships two distinct search features:

* **SuperSearch** — a *proprietary, licensed* server-side search index
  (requires a NewsLeecher account). It cannot be reconstructed from the
  client; this module provides a *drop-in replacement interface*
  (:class:`SearchProvider`) so any public Usenet search API (or the
  paid SuperSearch, if a user has an account) can be plugged in later.

* **Local header search** — what the original also does inside group
  view: search recent article headers on the connected server. This is
  :class:`LocalHeaderSearch`, implemented here over XHDR. It is real and
  works offline against whatever NNTP server is configured.

Search queries are simple: case-insensitive terms that must all appear
in the Subject (an optional ``from:`` term filters the poster, a
``groups:`` term restricts to specific newsgroups). Example::

    "The Matrix" from:anon groups:alt.binaries

Hits are ranked by how many terms matched and returned newest-first.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Protocol

from newzcrawl.nntp_client import NNTPClient, NNTPError

_QUERY_FROM = re.compile(r"\bfrom:(\S+)", re.I)
_QUERY_GROUPS = re.compile(r"\bgroups:(\S+)", re.I)


@dataclass
class SearchHit:
    group: str
    article_number: int
    message_id: str
    subject: str
    from_: str = ""
    date: str = ""
    size: int = 0
    has_yenc: bool = False
    score: int = 0


class SearchProvider(Protocol):
    """Interface every search backend must implement.

    ``query`` is the raw user query string; ``groups`` optionally limits
    which newsgroups are searched; ``limit`` caps the number of hits.
    Returns hits best-first.
    """

    def search(self, query: str, groups: list[str] | None = None,
               limit: int = 100) -> list[SearchHit]:
        ...  # pragma: no cover


def parse_query(query: str) -> dict:
    """Split a raw query into ``subject`` terms, ``from``, and
    ``groups`` (a list of group names). Quoted phrases keep their spaces;
    surrounding quotes are stripped."""
    q = (query or "").strip()
    groups: list[str] = []
    m = _QUERY_GROUPS.search(q)
    if m:
        groups = [g.strip() for g in m.group(1).split(",") if g.strip()]
        q = q[:m.start()] + q[m.end():]
    frm = ""
    m = _QUERY_FROM.search(q)
    if m:
        frm = m.group(1).lower()
        q = q[:m.start()] + q[m.end():]
    # tokenize, keeping quoted phrases intact. Use finditer + group(0):
    # findall would return the (empty) capture-group content for the
    # bare-word branch instead of the full match.
    terms: list[str] = []
    for m in re.finditer(r'"([^"]+)"|\S+', q):
        token = m.group(0).strip().lower().strip("\"'")
        if token:
            terms.append(token)
    return {"terms": terms, "from": frm, "groups": groups}


class LocalHeaderSearch:
    """Search recent article headers on a connected NNTP server.

    Uses the server's ``LAST`` to bound the scan window (``window`` most
    recent articles per group) and pulls ``Subject``/``From``/
    ``Message-ID``/``Date``/``Bytes`` via XHDR, then filters client-side.
    """

    def __init__(self, client_factory: Callable[[], NNTPClient],
                 window: int = 2000, group: str | None = None):
        self._factory = client_factory
        self.window = int(window)
        self.group = group  # restrict to a single group when browsing

    def search(self, query: str, groups: list[str] | None = None,
               limit: int = 100) -> list[SearchHit]:
        parsed = parse_query(query)
        if self.group:
            groups = [self.group]
        elif parsed["groups"]:
            groups = parsed["groups"]
        if not groups:
            # fall back to every subscribed/known group passed by caller
            groups = groups or []
        if not groups:
            return []
        terms = parsed["terms"]
        frm = parsed["from"]
        client = self._factory()
        hits: list[SearchHit] = []
        try:
            for grp in groups:
                try:
                    last = client.group(grp)  # 211 response carries the count
                except NNTPError:
                    continue
                if last <= 0:
                    continue
                lo = max(1, last - self.window + 1)
                try:
                    subjects = client.xhdr("Subject", str(lo), str(last))
                    mids = client.xhdr("Message-ID", str(lo), str(last))
                except NNTPError:
                    continue
                froms = dates = bytes_ = {}
                try:
                    froms = client.xhdr("From", str(lo), str(last))
                    dates = client.xhdr("Date", str(lo), str(last))
                    bytes_ = client.xhdr("Bytes", str(lo), str(last))
                except NNTPError:
                    pass
                for num, subj in subjects.items():
                    subj_l = subj.lower()
                    if terms and not all(t in subj_l for t in terms):
                        continue
                    if frm and frm not in froms.get(num, "").lower():
                        continue
                    mid = mids.get(num, f"<{num}@{grp}>")
                    score = sum(subj_l.count(t) for t in terms) if terms else 1
                    hits.append(SearchHit(
                        group=grp, article_number=int(num),
                        message_id=mid, subject=subj,
                        from_=froms.get(num, ""),
                        date=dates.get(num, ""),
                        size=int(bytes_.get(num, 0) or 0),
                        score=score,
                    ))
        finally:
            try:
                client.close()
            except Exception:
                pass
        # has_yenc can't be known without fetching; leave False (UI can
        # treat as "unknown")
        hits.sort(key=lambda h: (h.date, h.article_number), reverse=True)
        return hits[:limit]
