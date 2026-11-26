"""Tests for newzcrawl.nzb (NZB parse/create)."""

from newzcrawl import nzb


def _sample_xml():
    return b"""<?xml version="1.0" encoding="UTF-8"?>
<nzb xmlns="http://www.newzbin.com/">
  <head>
    <meta type="password">s3kr3t</meta>
    <rating>5</rating>
  </head>
  <file name="movie.iso">
    <headers>
      <group name="alt.binaries.movies" pattern="movie"/>
    </headers>
    <segments>
      <segment id="&lt;p1@x&gt;" bytes="1000" index="1"/>
      <segment id="p2@x" bytes="1000" index="2"/>
      <segment id="p3@x" bytes="500" index="3"/>
    </segments>
  </file>
  <file name="cover.jpg">
    <segments>
      <segment id="c1@x" bytes="123"/>
    </segments>
  </file>
</nzb>"""


def test_parse_basic():
    n = nzb.parse_nzb(_sample_xml())
    assert n.password == "s3kr3t"
    assert n.rating == 5
    assert len(n.items) == 2
    movie = n.items[0]
    assert movie.name == "movie.iso"
    assert movie.groups == ["alt.binaries.movies"]
    assert movie.pattern == "movie"
    assert len(movie.segments) == 3
    # angle brackets stripped from message ids
    assert movie.segments[0].message_id == "p1@x"
    assert movie.segments[1].message_id == "p2@x"
    assert movie.segments[0].bytes == 1000
    assert movie.segments[0].index == 1
    assert movie.size == 2500
    cover = n.items[1]
    assert cover.name == "cover.jpg"
    assert len(cover.segments) == 1
    # segment with no index attr -> None
    assert cover.segments[0].index is None


def test_parse_namespace_and_no_ns():
    # a file without the newzbin namespace must also parse
    xml = (b'<nzb><file name="a.bin">'
           b'<segments><segment id="z@x" bytes="10"/></segments>'
           b'</file></nzb>')
    n = nzb.parse_nzb(xml)
    assert len(n.items) == 1
    assert n.items[0].segments[0].message_id == "z@x"


def test_parse_password_meta():
    xml = (b'<nzb><head><meta type="password">pw!</meta></head>'
           b'<file name="a"><segments><segment id="z@x"/></segments>'
           b'</file></nzb>')
    n = nzb.parse_nzb(xml)
    assert n.password == "pw!"


def test_round_trip():
    item1 = nzb.NZBItem(name="file 1.bin", groups=["alt.binaries.test"],
                        pattern="file 1")
    item1.add_segment(nzb.NZBSegment("a@x", bytes=10, index=1))
    item1.add_segment(nzb.NZBSegment("b@x", bytes=10, index=2))
    item2 = nzb.NZBItem(name="file2.bin")
    item2.add_segment(nzb.NZBSegment("c@x", bytes=5))
    n = nzb.NZB(name="test.nzb", items=[item1, item2], password="pw")
    data = nzb.create_nzb(n.name, n.items, password=n.password)
    back = nzb.parse_nzb(data)
    assert back.password == "pw"
    assert len(back.items) == 2
    assert back.items[0].name == "file 1.bin"
    assert back.items[0].groups == ["alt.binaries.test"]
    assert back.items[0].pattern == "file 1"
    assert [s.index for s in back.items[0].segments] == [1, 2]
    assert back.items[1].name == "file2.bin"
    assert back.items[0].size == 20


def test_create_uses_list_order_for_index():
    item = nzb.NZBItem(name="seq.bin")
    item.add_segment(nzb.NZBSegment("a@x", bytes=1))
    item.add_segment(nzb.NZBSegment("b@x", bytes=1))
    item.add_segment(nzb.NZBSegment("c@x", bytes=1))
    data = nzb.create_nzb("seq.nzb", [item])
    back = nzb.parse_nzb(data)
    # segments without explicit index get 1..N in list order
    assert [s.index for s in back.items[0].segments] == [1, 2, 3]
