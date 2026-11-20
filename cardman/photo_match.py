"""Whether a seller's photo and the catalog picture are the same card.

A title can name an expensive card while the photo is a different one. The hash is an 8×8 average of
the gray values. A sleeve, a glare or a crop moves it a little. A different card moves it a lot.
Uncertain pairs are left alone: this only speaks when the two pictures clearly disagree.
"""
from __future__ import annotations

import io
import struct
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path

# Mean absolute difference of an 8×8 gray sample, 0–255.
# A sleeve or a glare stays under AGREE. A different picture lands at or over DIFFERENT.
AGREE = 22
DIFFERENT = 48


def disagrees(seller: bytes, catalog: bytes) -> bool | None:
    """True when the two pictures are clearly different cards. None when either picture can't be read,
    or the difference is in the middle and could be a sleeve."""
    a, b = _sample(seller), _sample(catalog)
    if a is None or b is None:
        return None
    dist = sum(abs(x - y) for x, y in zip(a, b)) / len(a)
    if dist >= DIFFERENT:
        return True
    if dist <= AGREE:
        return False
    return None


def _sample(data: bytes) -> list[int] | None:
    px = _gray(data)
    if not px:
        return None
    w, h, vals = px
    out = []
    for y in range(8):
        for x in range(8):
            out.append(vals[min(h - 1, int((y + 0.5) * h / 8)) * w + min(w - 1, int((x + 0.5) * w / 8))])
    return out


def _gray(data: bytes) -> tuple[int, int, list[int]] | None:
    if data.startswith(b"\x89PNG"):
        return _png(data)
    if data.startswith(b"BM"):
        return _bmp(data)
    if data.startswith(b"\xff\xd8"):
        return _jpeg(data)
    return None


def _png(data: bytes) -> tuple[int, int, list[int]] | None:
    if len(data) < 24:
        return None
    try:
        w, h, depth, color, _comp, _filt, interlace = struct.unpack(">IIBBBBB", data[16:29])
    except struct.error:
        return None
    if depth != 8 or color not in (0, 2, 6) or interlace or w < 1 or h < 1 or w * h > 8_000_000:
        return None
    ch = {0: 1, 2: 3, 6: 4}[color]
    idat = b""
    i = 8
    while i + 8 <= len(data):
        n, kind = struct.unpack(">I4s", data[i:i + 8])
        i += 8
        if i + n > len(data):
            return None
        if kind == b"IDAT":
            idat += data[i:i + n]
        i += n + 4
    try:
        raw = zlib.decompress(idat)
    except zlib.error:
        return None
    stride = w * ch
    out: list[int] = []
    prev = bytearray(stride)
    pos = 0
    for _ in range(h):
        if pos >= len(raw):
            return None
        f, pos = raw[pos], pos + 1
        row = bytearray(raw[pos:pos + stride])
        pos += stride
        if len(row) < stride:
            return None
        for x in range(stride):
            left = row[x - ch] if x >= ch else 0
            up = prev[x]
            if f == 1:
                row[x] = (row[x] + left) & 255
            elif f == 2:
                row[x] = (row[x] + up) & 255
            elif f == 3:
                row[x] = (row[x] + ((left + up) // 2)) & 255
            elif f == 4:
                ul = prev[x - ch] if x >= ch else 0
                p = left + up - ul
                row[x] = (row[x] + (left if abs(p - left) <= abs(p - up) and abs(p - left) <= abs(p - ul)
                                    else up if abs(p - up) <= abs(p - ul) else ul)) & 255
        prev = row
        if color == 0:
            out.extend(row)
        else:
            out.extend((row[x] + row[x + 1] + row[x + 2]) // 3 for x in range(0, stride, ch))
    return w, h, out


def _bmp(data: bytes) -> tuple[int, int, list[int]] | None:
    if len(data) < 54:
        return None
    off = struct.unpack_from("<I", data, 10)[0]
    w, h = struct.unpack_from("<ii", data, 18)
    bits = struct.unpack_from("<H", data, 28)[0]
    if bits != 24 or w < 1 or abs(h) < 1:
        return None
    h, flip = abs(h), h > 0
    stride = (w * 3 + 3) & ~3
    out = [0] * (w * h)
    for y in range(h):
        src = h - 1 - y if flip else y
        row = data[off + src * stride:off + src * stride + w * 3]
        if len(row) < w * 3:
            return None
        for x in range(w):
            b, g, r = row[x * 3:x * 3 + 3]
            out[y * w + x] = (r + g + b) // 3
    return w, h, out


def _jpeg(data: bytes) -> tuple[int, int, list[int]] | None:
    try:
        from PIL import Image
    except ImportError:
        Image = None
    if Image is not None:
        try:
            im = Image.open(io.BytesIO(data)).convert("L")
        except Exception:
            return None
        w, h = im.size
        return w, h, list(im.getdata())
    if sys.platform != "darwin":
        return None
    try:
        with tempfile.TemporaryDirectory() as d:
            src, dst = Path(d) / "a.jpg", Path(d) / "a.bmp"
            src.write_bytes(data)
            r = subprocess.run(["sips", "-s", "format", "bmp", str(src), "--out", str(dst)],
                               capture_output=True, timeout=8)
            if r.returncode != 0 or not dst.exists():
                return None
            return _bmp(dst.read_bytes())
    except (OSError, subprocess.TimeoutExpired):
        return None
