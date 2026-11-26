"""Tests for newzcrawl.yenc (yEnc v1.0.3 codec)."""

import base64
import os
import random
import zlib

import pytest

from newzcrawl import yenc


@pytest.mark.parametrize("n", list(range(0, 40)) + [1000, 10000, 100000, 100003])
def test_round_trip(n):
    data = os.urandom(n)
    enc = yenc.encode(data)
    dec, crc = yenc.decode(enc)
    assert dec == data
    assert yenc.check_crc(dec, crc)


def _real_world_body(data: bytes, line_len: int = 40) -> bytes:
    """Build a body the way real Usenet posts do: one =y part, fixed
    line width, Z terminating the final line, =yend crc2 trailer."""
    enc = base64.a85encode(data, adobe=False)
    lines = [enc[i:i + line_len] for i in range(0, len(enc), line_len)]
    lines[-1] += b"Z"
    body = b"=y line=%d\r\n" % len(enc) + b"\r\n".join(lines) + b"\r\n"
    body += b"=yend crc2=%08x\r\n" % (zlib.crc32(data) & 0xFFFFFFFF)
    return body


@pytest.mark.parametrize("n", [1, 13, 99, 100, 101, 137, 100000])
def test_real_world_single_part(n):
    data = os.urandom(n)
    dec, crc = yenc.decode(_real_world_body(data))
    assert dec == data
    assert yenc.check_crc(dec, crc)


def test_multi_part_body():
    d1, d2 = os.urandom(50), os.urandom(70)
    p1 = base64.a85encode(d1, adobe=False) + b"Z"
    p2 = base64.a85encode(d2, adobe=False) + b"Z"
    body = (b"=y\r\n" + p1 + b"\r\n" + b"=y\r\n" + p2 + b"\r\n"
            + b"=yend crc2=%08x\r\n" % (zlib.crc32(d1 + d2) & 0xFFFFFFFF))
    dec, _ = yenc.decode(body)
    assert dec == d1 + d2


def test_crc_mismatch_detected():
    enc = yenc.encode(os.urandom(200))
    dec, _ = yenc.decode(enc)
    assert not yenc.check_crc(dec, "00000000")


def test_missing_trailer_raises():
    with pytest.raises(ValueError):
        yenc.decode(b"=y\r\n0abcd\r\n")


def test_corrupted_payload_detected():
    data = os.urandom(300)
    body = bytearray(_real_world_body(data))
    # flip a byte in the middle of the payload
    body[20] ^= 0x01
    dec, crc = yenc.decode(bytes(body))
    assert not yenc.check_crc(dec, crc)


def test_random_fuzz():
    rng = random.Random(1234)
    for _ in range(20):
        data = bytes(rng.randrange(256) for _ in range(rng.randrange(0, 5000)))
        enc = yenc.encode(data)
        dec, crc = yenc.decode(enc)
        assert dec == data
        assert yenc.check_crc(dec, crc)
