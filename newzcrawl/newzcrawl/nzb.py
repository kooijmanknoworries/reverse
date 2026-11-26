"""NZB read/write (the standard Usenet download-list format).

An NZB file is a small XML document. Each ``<file>`` element groups the
segments (Usenet articles) that make up one downloadable binary; each
``<segment>`` carries the article's message-id, its size in bytes, and
optionally a part index. ``<group>`` elements under ``<headers>`` state
the newsgroup(s) a segment may be found in, plus an optional subject
pattern for filtering crossposts.

This module is dependency-free (xml.etree) so it works inside the
portable bundle.
"""

from __future__ import annotations

import io
import os
import re
from dataclasses import dataclass, field
from xml.etree import ElementTree as ET

_NZB_NS = "http://www.newzbin.com/"


def _strip_ns(tag: str) -> str:
    """Return the local tag name, dropping any ``{namespace}`` prefix."""
    return tag.rsplit("}", 1)[-1]


@dataclass
class NZBSegment:
    """One Usenet article that contributes bytes to an item."""
    message_id: str
    bytes: int = 0
    index: int | None = None
    order: int | None = None

    def __post_init__(self):
        if self.message_id.startswith("<") and self.message_id.endswith(">"):
            self.message_id = self.message_id[1:-1]


@dataclass
class NZBItem:
    """One logical file inside an NZB (usually one binary)."""
    name: str
    segments: list[NZBSegment] = field(default_factory=list)
    groups: list[str] = field(default_factory=list)
    pattern: str = ""
    post_date: str = ""
    size: int = 0

    def add_segment(self, seg: NZBSegment) -> None:
        self.segments.append(seg)
        if seg.bytes:
            self.size += seg.bytes

    def missing(self) -> list[NZBSegment]:
        """Segments that still need downloading (not yet marked done)."""
        return [s for s in self.segments if not getattr(s, "done", False)]


@dataclass
class NZB:
    name: str
    items: list[NZBItem] = field(default_factory=list)
    password: str = ""
    meta: dict[str, str] = field(default_factory=dict)
    rating: int | None = None

    def add_item(self, item: NZBItem) -> None:
        self.items.append(item)


def parse_nzb(source: str | bytes | os.PathLike) -> NZB:
    """Parse an NZB from a path, XML string, or raw bytes."""
    if isinstance(source, (bytes, bytearray)):
        data = bytes(source)
    elif isinstance(source, os.PathLike):
        with open(source, "rb") as f:
            data = f.read()
    else:
        if os.path.exists(source):
            with open(source, "rb") as f:
                data = f.read()
        else:
            data = source.encode("utf-8")
    root = ET.fromstring(data)
    return _parse_element(root, default_name="unknown")


def _parse_element(root: ET.Element, default_name: str) -> NZB:
    nzb = NZB(name=default_name)
    # password may sit on <nzb> or a <meta type="password">
    for meta in root.iter():
        if _strip_ns(meta.tag) == "meta":
            mtype = (meta.get("type") or "").lower()
            val = (meta.text or "").strip()
            nzb.meta[mtype] = val
            if mtype == "password":
                nzb.password = val
            elif mtype in ("title", "description") and nzb.name == default_name:
                nzb.name = val or nzb.name
    if root.get("password"):
        nzb.password = root.get("password")
    rating = root.get("rating")
    if rating is None:
        for r in root.iter():
            if _strip_ns(r.tag) == "rating" and r.text:
                rating = r.text
                break
    try:
        nzb.rating = int(float(rating)) if rating is not None else None
    except (TypeError, ValueError):
        nzb.rating = None
    for file_el in root:
        if _strip_ns(file_el.tag) != "file":
            continue
        item = _parse_file(file_el, nzb.name)
        if item is not None:
            nzb.items.append(item)
    if not nzb.items:
        # a single implicit <file> is not required; some producers put
        # <segment> directly under <nzb>
        segs = [s for s in _segments_of(root)]
        if segs:
            item = NZBItem(name=nzb.name)
            for s in segs:
                item.add_segment(s)
            nzb.items.append(item)
    return nzb


def _segments_of(el: ET.Element) -> list[NZBSegment]:
    out: list[NZBSegment] = []
    for seg in el.iter():
        if _strip_ns(seg.tag) != "segment":
            continue
        mid = (seg.get("id") or (seg.text or "")).strip()
        if not mid:
            continue
        nbytes = 0
        try:
            nbytes = int(seg.get("bytes") or 0)
        except ValueError:
            nbytes = 0
        index = None
        idx = seg.get("index")
        if idx is not None:
            try:
                index = int(idx)
            except ValueError:
                index = None
        order = None
        ord_ = seg.get("order")
        if ord_ is not None:
            try:
                order = int(ord_)
            except ValueError:
                order = None
        out.append(NZBSegment(message_id=mid, bytes=nbytes, index=index,
                              order=order))
    return out


def _parse_file(file_el: ET.Element, nzb_name: str) -> NZBItem | None:
    name = file_el.get("name") or nzb_name
    item = NZBItem(name=name, post_date=file_el.get("postdate") or "")
    # groups/pattern live under <headers>
    for headers in file_el:
        if _strip_ns(headers.tag) != "headers":
            continue
        for grp in headers:
            if _strip_ns(grp.tag) != "group":
                continue
            gname = (grp.get("name") or "").strip()
            if gname:
                item.groups.append(gname)
            pat = (grp.get("pattern") or "").strip()
            if pat and not item.pattern:
                item.pattern = pat
    segs = _segments_of(file_el)
    if not segs:
        return None
    for s in segs:
        item.add_segment(s)
    return item


# ---------------------------------------------------------------------------
# creation
# ---------------------------------------------------------------------------

def _escape(text: str) -> str:
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def create_nzb(name: str, items: list[NZBItem],
               password: str = "", creator: str = "NewzCrawl") -> bytes:
    """Serialize an NZB to bytes.

    Items whose ``segments`` carry no explicit ``order``/``index`` are
    emitted in list order, which for a freshly assembled item is the
    intended part order.
    """
    lines = ['<?xml version="1.0" encoding="UTF-8"?>']
    lines.append(f'<nzb xmlns="{_NZB_NS}">')
    lines.append("  <head>")
    if password:
        lines.append(f'    <meta type="password">{_escape(password)}</meta>')
    lines.append(f'    <meta type="creator">{_escape(creator)}</meta>')
    lines.append("  </head>")
    for item in items:
        lines.append(f'  <file name="{_escape(item.name)}">')
        if item.groups:
            lines.append("    <headers>")
            for g in item.groups:
                pat = f' pattern="{_escape(item.pattern)}"' if item.pattern else ""
                lines.append(f'      <group name="{_escape(g)}"{pat}/>')
            lines.append("    </headers>")
        lines.append("    <segments>")
        for i, seg in enumerate(item.segments, start=1):
            attrs = f'id="{_escape(seg.message_id)}" bytes="{seg.bytes}"'
            if seg.order is not None:
                attrs += f' order="{seg.order}"'
            elif seg.index is not None:
                attrs += f' index="{seg.index}"'
            else:
                attrs += f' index="{i}"'
            lines.append(f"      <segment {attrs}/>")
        lines.append("    </segments>")
        lines.append("  </file>")
    lines.append("</nzb>")
    return ("\n".join(lines) + "\n").encode("utf-8")


def save_nzb(nzb: NZB, path: str | os.PathLike) -> None:
    data = create_nzb(nzb.name, nzb.items, password=nzb.password)
    with open(path, "wb") as f:
        f.write(data)
