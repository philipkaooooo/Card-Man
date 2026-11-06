"""Read listing photos, binder pages, auction recordings and livestream captures, locally and for free.

Text comes from Apple's Vision framework through a small Swift helper (macOS only; on anything else every call reports
that OCR is unavailable). Video frames come from ffmpeg. What is read here is a machine reading: it can propose codes and
contradict a seller, and a person still confirms before it counts as printed_set_code.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import unicodedata
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from .listing_parser import parse_listing_text

SWIFT = r'''
import Foundation
import Vision
import AppKit
for path in CommandLine.arguments.dropFirst() {
    guard let img = NSImage(contentsOfFile: path), let cg = img.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
        print("{\"path\":\"\(path)\",\"error\":\"unreadable\"}"); continue }
    let req = VNRecognizeTextRequest()
    req.recognitionLevel = .accurate
    req.recognitionLanguages = ["ja-JP", "en-US", "zh-Hant"]
    req.usesLanguageCorrection = false
    try? VNImageRequestHandler(cgImage: cg, options: [:]).perform([req])
    var rows: [[String: Any]] = []
    for o in req.results ?? [] {
        guard let c = o.topCandidates(1).first else { continue }
        let b = o.boundingBox
        rows.append(["text": c.string, "conf": c.confidence, "x": b.minX, "y": 1 - b.maxY, "w": b.width, "h": b.height])
    }
    let d = try! JSONSerialization.data(withJSONObject: ["path": path, "lines": rows])
    print(String(data: d, encoding: .utf8)!)
}
'''

_PRICE_RE = re.compile(r"(?:(¥|￥|NT\$|US\$|\$|円)\s*([\d,]{1,12}(?:\.\d{1,2})?))|(?:([\d,]{2,12})\s*(円|元))")
_BID_WORD = re.compile(r"入札|現在|即決|落札|bid|current|sold|得標|出價", re.I)
_CUR = {"¥": "JPY", "￥": "JPY", "円": "JPY", "NT$": "TWD", "元": "TWD", "$": "USD", "US$": "USD"}
# Vision confuses these inside codes; only applied to digit positions of a code-shaped token.
_DIGIT_FIX = str.maketrans({"O": "0", "o": "0", "I": "1", "l": "1", "S": "5", "B": "8"})
_CODE_LIKE = re.compile(r"(?<![A-Za-z0-9])([A-Za-z0-9]{2,5})[-–—ー―]([A-Za-z]{0,2})([0-9OoIlSB]{2,4})(?![A-Za-z0-9])")


@dataclass(frozen=True)
class TextLine:
    text: str
    conf: float
    x: float
    y: float
    w: float
    h: float


@dataclass
class PriceMention:
    amount: Decimal
    currency: str
    is_bid_context: bool
    text: str


@dataclass
class ImageReading:
    path: str
    lines: list[TextLine] = field(default_factory=list)
    codes: tuple[str, ...] = ()
    prices: list[PriceMention] = field(default_factory=list)
    grade_company: str | None = None
    grade: str | None = None
    cert: str | None = None
    error: str | None = None
    seconds: float | None = None   # position in a video

    @property
    def text(self) -> str:
        return "\n".join(l.text for l in self.lines)


def available() -> tuple[bool, str]:
    if not shutil.which("swiftc"):
        return False, "OCR needs macOS with swiftc (Xcode command line tools)"
    return True, "Apple Vision"


def _helper(cache: Path) -> Path:
    cache.mkdir(parents=True, exist_ok=True)
    exe = cache / "ocr_vision"
    src = cache / "ocr_vision.swift"
    if exe.exists() and src.exists() and src.read_text() == SWIFT:
        return exe
    src.write_text(SWIFT)
    subprocess.run(["swiftc", "-O", str(src), "-o", str(exe)], check=True, capture_output=True, timeout=300)
    return exe


def normalize_codes(text: str) -> tuple[str, ...]:
    """Set-code-shaped tokens in OCR text, with look-alike characters repaired only in digit positions."""
    norm = unicodedata.normalize("NFKC", text)
    out = []
    for prefix, lang, digits in _CODE_LIKE.findall(norm):
        code = f"{prefix.upper()}-{lang.upper()}{digits.translate(_DIGIT_FIX)}"
        if code not in out and not prefix.upper().startswith(("PSA", "BGS", "CGC")):
            out.append(code)
    return tuple(out)


def extract(text: str) -> tuple[list[PriceMention], dict]:
    prices = []
    for line in unicodedata.normalize("NFKC", text).splitlines():
        for m in _PRICE_RE.finditer(line):
            sym = m.group(1) or m.group(4)
            amt = (m.group(2) or m.group(3)).replace(",", "")
            if not amt or sym not in _CUR:
                continue
            prices.append(PriceMention(Decimal(amt), _CUR[sym], bool(_BID_WORD.search(line)), line.strip()))
    p = parse_listing_text(text)
    return prices, {"grade_company": p.grade_company, "grade": p.grade, "cert": p.cert}


def read_images(paths: list[str | Path], cache: Path = Path("data/cache/vision")) -> list[ImageReading]:
    ok, why = available()
    if not ok:
        return [ImageReading(str(p), error=why) for p in paths]
    if not paths:
        return []
    exe = _helper(Path(cache))
    run = subprocess.run([str(exe), *map(str, paths)], capture_output=True, text=True, timeout=600)
    if run.returncode != 0:
        return [ImageReading(str(p), error=f"OCR failed: {run.stderr[:200]}") for p in paths]
    out = []
    for line in run.stdout.splitlines():
        d = json.loads(line)
        if d.get("error"):
            out.append(ImageReading(d["path"], error=d["error"]))
            continue
        lines = [TextLine(x["text"], float(x["conf"]), x["x"], x["y"], x["w"], x["h"]) for x in d["lines"]]
        text = "\n".join(l.text for l in lines)
        prices, g = extract(text)
        out.append(ImageReading(d["path"], lines, normalize_codes(text), prices, **g))
    return out


def binder_cells(reading: ImageReading, rows: int = 3, cols: int = 3) -> dict[tuple[int, int], tuple[str, ...]]:
    """Which grid pocket each machine-read code sits in: a 9-pocket page becomes per-card leads."""
    cells: dict[tuple[int, int], list[str]] = {}
    for l in reading.lines:
        codes = normalize_codes(l.text)
        if codes:
            k = (min(rows - 1, int((l.y + l.h / 2) * rows)), min(cols - 1, int((l.x + l.w / 2) * cols)))
            cells.setdefault(k, []).extend(codes)
    return {k: tuple(dict.fromkeys(v)) for k, v in cells.items()}


def read_video(path: str | Path, *, every_seconds: float = 5.0, max_frames: int = 240, cache: Path = Path("data/cache/vision")) -> list[ImageReading]:
    """Sample frames, read them, keep frames whose text changed. Returns readings carrying their position in the video."""
    if not shutil.which("ffmpeg"):
        return [ImageReading(str(path), error="video reading needs ffmpeg")]
    with tempfile.TemporaryDirectory() as tmp:
        run = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-vf", f"fps=1/{every_seconds}", "-frames:v", str(max_frames),
                              f"{tmp}/f%05d.png"], capture_output=True, text=True, timeout=1800)
        frames = sorted(Path(tmp).glob("f*.png"))
        if run.returncode != 0 or not frames:
            return [ImageReading(str(path), error=f"could not read frames: {run.stderr[:200]}")]
        readings = read_images(frames, cache)
    out, last = [], None
    for i, r in enumerate(readings):
        r.seconds = i * every_seconds
        r.path = f"{path}@{r.seconds:g}s"
        key = r.text.strip()
        if key and key != last:
            out.append(r)
            last = key
    return out


def auction_timeline(readings: list[ImageReading]) -> list[dict]:
    """Prices seen over a recording, in order, with each change: bids climbing, a hammer price, a price quoted on stream."""
    rows, prev = [], None
    for r in readings:
        for p in r.prices:
            k = (p.amount, p.currency)
            if k != prev:
                rows.append({"seconds": r.seconds, "amount": p.amount, "currency": p.currency, "bid": p.is_bid_context,
                             "codes": r.codes, "text": p.text})
                prev = k
    return rows
