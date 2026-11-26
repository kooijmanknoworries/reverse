#!/usr/bin/env python3
"""Generate assets/icon.ico for NewzCrawl (pure stdlib, no Pillow).

Draws a rounded blue tile with a white download arrow (the app is a
Usenet downloader) at several sizes and packs them into a multi-image
.ico file. Supersampled 4x and downsampled for smooth anti-aliased edges.

Usage:  python3 tools/gen_icon.py
Writes: assets/icon.ico
"""
import os
import struct

TOP = (91, 141, 255)      # tile gradient, top
BOTTOM = (52, 84, 216)    # tile gradient, bottom
WHITE = (255, 255, 255)


def _clamp01(v):
    return 0.0 if v < 0 else (1.0 if v > 1 else v)


def _lerp(a, b, t):
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


def _rounded_rect(px, py, x0, y0, x1, y1, r):
    """Coverage (0..1) of a rounded rectangle (supersample cell center)."""
    if px < x0 or px > x1 or py < y0 or py > y1:
        return 0.0
    # distance to corner circles
    cx = max(x0 + r, min(px, x1 - r))
    cy = max(y0 + r, min(py, y1 - r))
    dx, dy = px - cx, py - cy
    d = (dx * dx + dy * dy) ** 0.5
    if d <= r:
        return 1.0
    # approximate AA on the rounded corner
    return _clamp01(r - d + 0.5)


def _arrow(px, py, S):
    """Coverage (0..1) of the white download-arrow at super-sample (px,py).
    Coordinates are in the 0..S space of a super-sampled SxS image."""
    u, v = px / S, py / S  # normalized 0..1
    # vertical stem
    if 0.415 <= u <= 0.585 and 0.26 <= v <= 0.56:
        return 1.0
    # downward triangle head
    if 0.56 <= v <= 0.78:
        t = (v - 0.56) / 0.22          # 0 at top of head -> 1 at tip
        half = 0.19 * (1 - t) + 0.02
        if abs(u - 0.5) <= half:
            return 1.0
    # base bar
    if 0.40 <= u <= 0.60 and 0.80 <= v <= 0.885:
        return 1.0
    return 0.0


def render(size):
    """Return a list of RGBA rows (top-down) for a `size`x`size` icon."""
    SS = 4
    S = size * SS
    img = [[(0, 0, 0, 0) for _ in range(S)] for _ in range(S)]
    for y in range(S):
        for x in range(S):
            u, v = (x + 0.5) / S, (y + 0.5) / S
            cov = _rounded_rect(x + 0.5, y + 0.5, 0.5, 0.5, S - 0.5, S - 0.5,
                                S * 0.24)
            if cov <= 0:
                continue
            bg = _lerp(TOP, BOTTOM, v)
            acov = _arrow(x + 0.5, y + 0.5, S)
            # blend white arrow over the gradient
            r = int(bg[0] + (WHITE[0] - bg[0]) * acov)
            g = int(bg[1] + (WHITE[1] - bg[1]) * acov)
            b = int(bg[2] + (WHITE[2] - bg[2]) * acov)
            img[y][x] = (r, g, b, int(255 * cov))
    # downsample SSxSS -> size
    out = [[(0, 0, 0, 0) for _ in range(size)] for _ in range(size)]
    for oy in range(size):
        for ox in range(size):
            acc = [0, 0, 0, 0]
            for sy in range(SS):
                for sx in range(SS):
                    p = img[oy * SS + sy][ox * SS + sx]
                    acc = [acc[i] + p[i] for i in range(4)]
            n = SS * SS
            out[oy][ox] = tuple(c // n for c in acc)
    return out


def _to_bgra_rows(img):
    """ICO wants bottom-up BGRA, 4 bytes/px, rows 4-byte aligned (W*4 is ok)."""
    h = len(img)
    w = len(img[0])
    rows = []
    for y in range(h - 1, -1, -1):
        buf = bytearray()
        for x in range(w):
            r, g, b, a = img[y][x]
            buf += struct.pack("<BBBB", b, g, r, a)
        # pad row to 4 bytes (already multiple of 4 since w*4)
        rows.append(bytes(buf))
    return rows


def _and_mask_rows(h, w):
    """AND mask: 1 bit/px, rows padded to 4 bytes. All zero (we use alpha)."""
    rowbytes = ((w + 31) // 32) * 4
    rows = []
    for _ in range(h):
        rows.append(b"\x00" * rowbytes)
    return rows


def make_ico(images):
    """images: list of (size, img). Returns ICO bytes."""
    n = len(images)
    out = struct.pack("<HHH", 0, 1, n)
    offsets = []
    entries = b""
    # compute sizes first
    blobs = []
    for size, img in images:
        xor = b"".join(_to_bgra_rows(img))
        andm = b"".join(_and_mask_rows(len(img), len(img[0])))
        # Full 40-byte BITMAPINFOHEADER: biSize, w, h(2x), planes, bpp,
        # compression, biSizeImage, xPels/m, yPels/m, clrUsed, clrImportant
        header = struct.pack("<IiiHHIIiiII", 40, size, size * 2, 1, 32, 0,
                             len(xor) + len(andm), 0, 0, 0, 0)
        blob = header + xor + andm
        blobs.append((size, blob))
    offset = 6 + 16 * n
    for size, blob in blobs:
        w = 0 if size >= 256 else size
        off = offset
        offsets.append(off)
        entries += struct.pack("<BBBBHHII", w, w, 0, 0, 1, 32, len(blob), off)
        offset += len(blob)
    return out + entries + b"".join(blob for _, blob in blobs)


def main():
    out_dir = os.path.join(os.path.dirname(__file__), "..", "assets")
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.normpath(os.path.join(out_dir, "icon.ico"))
    images = []
    for s in (16, 24, 32, 48, 64, 128):
        images.append((s, render(s)))
    data = make_ico(images)
    with open(out, "wb") as f:
        f.write(data)
    print(f"wrote {out} ({len(data)} bytes, {len(images)} sizes)")


if __name__ == "__main__":
    main()
