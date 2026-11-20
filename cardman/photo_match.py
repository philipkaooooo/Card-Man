"""Whether a seller's photo and the catalog picture are the same card.

A title can name an expensive card while the photo is a different one. The hash is an 8×8 average of
the gray values. A sleeve, a glare or a crop moves it a little. A different card moves it a lot.
Uncertain pairs are left alone: this only speaks when the two pictures clearly disagree.
"""
from __future__ import annotations

import io
import re
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


def apply(d: dict, key: str = "", db=None, *, listing_root: Path | None = None, image_root: Path | None = None) -> dict:
    """A listing is one catalog card only when its photo is that card's picture.

    No seller photo: left as the engine stored it (photo "none").
    A photo that is clearly a different picture: unbound (photo "clash"). The card's price is removed.
    A photo that is not a clear match: unbound (photo "unconfirmed"). No name and no price are attached.
    """
    if "LOT" in (d.get("kinds") or []):
        return {**d, "photo": "none"}
    iid = _item_id(key, d)
    seller = _listing_bytes(iid, listing_root) if iid else None
    has_photo = seller is not None or _listed_images(db, iid)
    if not has_photo:
        return {**d, "photo": "none"}
    pcs = _passcodes(d)
    if len(pcs) != 1:
        return _unbind(d, "unconfirmed")
    catalog = _catalog_bytes(pcs[0], image_root)
    if seller and catalog:
        same = disagrees(seller, catalog)
        if same is False:
            return {**d, "photo": "match"}
        if same is True:
            return _unbind(d, "clash")
    return _unbind(d, "unconfirmed")


def _unbind(d: dict, photo: str) -> dict:
    kinds = [k for k in (d.get("kinds") or []) if k not in ("REFERENCE_GAP", "UNDERPRICED", "RIGHT_PRINT_ONLY")]
    if photo == "clash":
        kinds.append("PHOTO_REJECTED")
    scenarios = []
    for s in d.get("scenarios") or []:
        scenarios.append({**s, "basis": "none", "resale_mid": None, "resale_low": None, "max_buy": None,
                          "profit_mid": None, "profit_low": None})
    return {**d, "photo": photo, "printing_id": None, "expected_profit": None, "best_profit": None,
            "kinds": kinds, "scenarios": scenarios}


def _item_id(key: str, d: dict) -> str | None:
    iid = (d or {}).get("listing_id") or (key.rsplit(":", 1)[-1] if str(key).startswith("ebay") else None)
    return iid if iid and str(iid).isdigit() else None


def _passcodes(d: dict) -> list[str]:
    found = []
    for s in d.get("scenarios") or []:
        m = re.search(r"@([^@|]+)", s.get("printing_id") or "")
        if m and m.group(1) not in found:
            found.append(m.group(1))
    if not found:
        m = re.search(r"@([^@|]+)", d.get("printing_id") or "")
        if m:
            found.append(m.group(1))
    return found


def _listing_bytes(item_id: str, root: Path | None) -> bytes | None:
    from .listing_detail import CACHE, cache_key
    base = Path(root) if root is not None else CACHE
    for size in ("s", "m"):
        k = cache_key(item_id, 0, size)
        path = base / k[:2] / f"{k}.img"
        if path.exists() and path.stat().st_size > 32:
            return path.read_bytes()
    return None


def _catalog_bytes(passcode: str, root: Path | None) -> bytes | None:
    from .cardinfo import IMAGES
    base = Path(root) if root is not None else IMAGES
    if passcode.startswith("pkm-"):
        cid = passcode[4:]
        paths = [base / "pkm" / size / f"{cid}{ext}" for size in ("small", "large") for ext in (".png", ".jpg")]
    else:
        paths = [base / "artwork" / size / f"{passcode}.jpg" for size in ("small", "full")]
    for path in paths:
        if path.exists() and path.stat().st_size > 32:
            return path.read_bytes()
    return None


def _listed_images(db, item_id: str | None) -> bool:
    if db is None or not item_id:
        return False
    try:
        from .listing_detail import get
        return bool(get(db, item_id).get("images"))
    except Exception:
        return False

