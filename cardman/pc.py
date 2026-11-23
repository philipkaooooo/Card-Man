"""PriceCharting cross-market: per-card grade prices and years of monthly points, swept from the real site.

The site sits behind a Cloudflare managed challenge. Plain HTTP clients are fingerprinted and refused, and
headless Chrome is too, so the sweep drives a headed Chrome it launches itself over the DevTools pipe
(`--remote-debugging-pipe`, JSON frames on fds 3/4, standard library only). A persistent profile keeps the
clearance cookie warm, so most pages load on the first try after the first challenge.

One card costs two page loads: a search to resolve the product URL, then the product page. The product URL is
stored, so later sweeps go straight to it. Rows go into `pc_card` in the evidence store - the grade strip
(price, change, sales volume note), the monthly points per grade series, and the slab population. No
listing URLs, no page snapshots: roughly 2 KB per card.

    python -m cardman.pc sweep [--limit 40] [--game ygo|pkm] [--refetch] [--pid ID ...]
    python -m cardman.pc ingest capture.json
    python -m cardman.pc stats
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import select
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

UTC = timezone.utc
SCHEMA = """
CREATE TABLE IF NOT EXISTS pc_card (
  printing_id TEXT PRIMARY KEY,
  game TEXT NOT NULL,
  url TEXT, title TEXT,
  query TEXT, match_note TEXT,
  grades TEXT, series TEXT, pop TEXT,
  fetched TEXT NOT NULL, status TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS pc_game ON pc_card(game);
CREATE TABLE IF NOT EXISTS pc_point (
  printing_id TEXT NOT NULL, name TEXT NOT NULL, x INTEGER NOT NULL, v REAL NOT NULL,
  PRIMARY KEY (printing_id, name, x));
CREATE INDEX IF NOT EXISTS pc_point_pid ON pc_point(printing_id);
"""

POINT_KEEP_DAYS = 3400     # every capture adds its newest points; a few years of monthly history is the ceiling

DEX_MAX = 151              # sweep scope: the first 151 Pokémon, complete. Later this moves to 300, no further.
FRESH_DAYS = {"pkm": 1, "ygo": 10}   # the scoped characters refresh daily; the rest of the board weekly

GRADES = ("Ungraded", "Grade 7", "Grade 8", "Grade 9", "Grade 9.5", "PSA 10")
SERIES_TO_GRADE = {"used": "Ungraded", "cib": "Grade 7", "new": "Grade 8", "graded": "Grade 9",
                   "boxonly": "Grade 9.5", "manualonly": "PSA 10"}

EXTRACT_JS = r"""JSON.stringify((()=>{
 if(typeof VGPC==='undefined')return null;
 const ch=(typeof Highcharts!=='undefined'?(Highcharts.charts||[]):[]).filter(Boolean).find(c=>c.renderTo&&c.renderTo.id==='chart');
 const series={};(ch?ch.series:[]).forEach(s=>{series[s.name]=(s.points||[]).map(p=>[p.x,p.y])});
 const pd=document.querySelector('#price_data');
 const g=id=>{const el=document.getElementById(id);return el?el.innerText.trim():null};
 const cells={Ungraded:g('used_price'),'Grade 7':g('complete_price'),'Grade 8':g('new_price'),
  'Grade 9':g('graded_price'),'Grade 9.5':g('box_only_price'),'PSA 10':g('manual_only_price')};
 const vols=[...document.querySelectorAll('#price_data tr.sales_volume td.js-show-tab')].map(td=>td.innerText.trim());
 return {title:document.title,url:location.href,product:VGPC.product,pop:VGPC.pop_data,
  chart:VGPC.chart_data,series,cells,vols,pd:pd?pd.innerText:''};})())"""

SEARCH_JS = r"""JSON.stringify({title:document.title,
 links:[...document.querySelectorAll('a[href*="/game/"]')]
  .map(a=>({h:a.getAttribute('href'),t:(a.textContent||'').replace(/\s+/g,' ').trim()}))
  .filter(x=>x.h&&x.t&&!/\/(de|es|fr|nl|it|jp|pt|ko|zh)\/game\//.test(x.h))})"""

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
PROFILE = Path.home() / ".cache" / "cardman" / "pc-chrome"


class Challenge(Exception):
    """Cloudflare kept the page on the interstitial until the deadline."""


def _dump(obj) -> str:
    return json.dumps(obj, separators=(",", ":"))


_CELL = re.compile(r"^\$([\d,]+(?:\.\d+)?)(?:\s+([+\-−])\s*\$([\d,]+(?:\.\d+)?))?(?:\s+\$0\.00)?$")


def _cell(cell: str) -> tuple[float | None, float | None] | None:
    """One price cell: `$1.04 -$0.08`, `$1,922.70 $0.00` (no change printed as zero), or `$5.00`."""
    m = _CELL.match((cell or "").strip())
    if not m:
        return None
    try:
        p = float(m.group(1).replace(",", ""))
    except ValueError:
        return None
    c = None
    if m.group(2) and m.group(3):
        c = float(m.group(3).replace(",", "")) * (1 if m.group(2) == "+" else -1)
    elif "$0.00" in (cell or "") and "$0.00" not in m.group(1):
        c = 0.0
    return p, c


GRADE_FOR_SERIES = {"BGS 9.5": "Grade 9.5"}


def _usd_pts(pts, ref: float | None) -> list[list]:
    """Monthly price points in dollars. A zero is a month with no sale, not a price of zero, so it drops.
    PriceCharting ships two feeds and they disagree on units - the Highcharts series is dollars, VGPC's
    chart_data is cents - so the grade cell anchors the scale and an off-by-100 series is corrected."""
    clean = [[int(t), round(float(v), 2)] for t, v in (pts or []) if v is not None and float(v) > 0]
    if not clean or not ref or ref <= 0:
        return clean
    last = clean[-1][1]
    if last / ref > 20:
        return [[t, round(v / 100, 2)] for t, v in clean]
    if ref / last > 20:
        return [[t, round(v * 100, 2)] for t, v in clean]
    return clean


def _fix_units(ser: dict, grades: dict) -> dict:
    """History anchored to the grade cells. Older captures wrote cents where the cell is dollars, and a series
    can hold both, so the scale is fixed per series and then per stray point. A real price never sits 20x off
    the cell for the same grade."""
    out: dict[str, list] = {}
    for name, pts in (ser or {}).items():
        ref = (grades.get(name) or grades.get(GRADE_FOR_SERIES.get(name, "")) or {}).get("p")
        try:
            clean = [[int(x), float(v)] for x, v in (pts or []) if v is not None]
        except (TypeError, ValueError):
            continue
        if ref and ref > 0 and clean:
            last = clean[-1][1]
            if last > 0 and last / ref > 20:
                clean = [[x, v / 100.0] for x, v in clean]
            elif last > 0 and ref / last > 20:
                clean = [[x, v * 100.0] for x, v in clean]
            clean = [[x, v / 100.0] if v / ref > 20 else [x, v] for x, v in clean]
        clean = sorted([x, round(v, 2)] for x, v in clean if v > 0)
        if clean:
            out[name] = clean
    return out


def _vol(v) -> str | None:
    v = " ".join(re.sub(r"^\s*volume:\s*", "", (v or "").strip()).split())
    return v or None


def _grade_dict(prices: list, vols: list) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for i, name in enumerate(GRADES):
        if i >= len(prices):
            break
        p, c = prices[i]
        out[name] = {"p": p or None, "c": c, "vol": _vol(vols[i] if i < len(vols) else "")}
    return out


def cells_to_grades(cells: dict, vols: list) -> dict[str, dict]:
    """The table names its own cells, so nothing depends on the printed order."""
    out: dict[str, dict] = {}
    vols = list(vols or [])
    for i, name in enumerate(GRADES):
        vol = _vol(vols[i] if i < len(vols) else "")
        parsed = _cell(cells.get(name) or "")
        if parsed is None:
            continue
        p, c = parsed
        out[name] = {"p": p or None, "c": c, "vol": vol}
    return out


def parse_price_data(text: str) -> dict[str, dict]:
    """The six grade cells in display order, from the strip's visible text (used for captured innerText)."""
    cells = []
    for line in (text or "").splitlines():
        if "$" not in line:
            continue
        cells += [c.strip() for c in line.split("\t") if c.strip().startswith("$")]
    prices = [x for x in (_cell(c) for c in cells) if x is not None]
    vols = [x for x in (c.strip() for line in (text or "").splitlines() for c in line.split("\t"))
            if x and ("sale" in x or x == "rare")]
    return _grade_dict(prices, vols)


def _series_name(name: str) -> str:
    n = (name or "").strip()
    return f"Grade {n}" if n in ("7", "8", "9") else n


def parse_product(payload: dict) -> dict:
    """Turn a captured product page into the stored row: grades, grade-named monthly series in dollars, population."""
    cells = payload.get("cells")
    grades = (cells_to_grades(cells, payload.get("vols") or []) if cells
              else parse_price_data(payload.get("pd") or ""))
    raw_series = {_series_name(k): v for k, v in (payload.get("series") or {}).items()}
    if not raw_series:
        raw_series = {SERIES_TO_GRADE[k]: v for k, v in (payload.get("chart") or {}).items() if k in SERIES_TO_GRADE}
    series: dict[str, list] = {}
    for name, pts in raw_series.items():
        cell = grades.get(name) or grades.get(GRADE_FOR_SERIES.get(name, "")) or {}
        clean = _usd_pts(pts, cell.get("p"))
        if clean:
            series[name] = clean[-62:]
    product = payload.get("product") or {}
    return {"title": (payload.get("title") or "").strip(), "url": payload.get("url"),
            "vid": product.get("id"), "grades": grades, "series": series,
            "pop": payload.get("pop") or {}}


def _slug(path_or_url: str) -> str:
    return (path_or_url or "").rstrip("/").rsplit("/", 1)[-1]


def pick_link(links: list[dict], *, name: str = "", number: str = "", code: str = "",
              rarity: str = "") -> tuple[str | None, str]:
    """Best product href for a card. Exact number suffix and set code first, then name tokens; a bracketed
    variant beats the base page only when its bracket names the rarity we track."""
    words = [w for w in re.split(r"[^a-z0-9]+", name.lower()) if len(w) >= 4]
    generic = {"rare", "holo", "holofoil", "foil", "card", "edition"}
    rwords = {w for w in re.split(r"[^a-z0-9]+", (rarity or "").lower()) if w and w not in generic}
    best, best_t, why, top = None, "", "", float("-inf")
    for i, x in enumerate(links[:12]):
        h = x.get("h") or ""
        t = (x.get("t") or "").strip()
        if not h or not t:
            continue                   # image-only anchors repeat a product with no name to judge it by
        slug = _slug(h)
        score = 0.0
        if number and re.search(rf"-{re.escape(str(number))}$", slug):
            score += 4
        if code and code != "-" and code.lower().replace("-", "") in slug.replace("-", ""):
            score += 4
        score += sum(1 for w in words if w in slug)
        score += 0.5 * sum(1 for w in words if w in t.lower())
        m = re.search(r"\[([^\]]+)\]", t)
        if m:
            bracket = {w for w in re.split(r"[^a-z0-9]+", m.group(1).lower()) if w and w not in generic}
            score += 1.5 if (rwords and bracket & rwords) else -1.5
        score -= i * 0.15
        if score > top:
            best, best_t, top, why = h, t, score, f"score {score:g}"
    if best is None:
        return None, "no candidates"
    # An anchor with the right shape but the wrong card (a Lugia page answered a Wormadam search) scores zero on
    # every real signal. Without the number, the set code, or a name word in the slug or the anchor, ship a
    # nomatch instead of a confident wrong product.
    slug = _slug(best)
    num_ok = bool(number) and bool(re.search(rf"-{re.escape(str(number))}$", slug))
    code_ok = bool(code) and code != "-" and code.lower().replace("-", "") in slug.replace("-", "")
    name_ok = any(w in slug or w in best_t.lower() for w in words)
    if not (num_ok or code_ok or name_ok):
        return None, "no confident match"
    return best, why


def _fds():
    r3, w3 = os.pipe()          # Chrome reads fd 3
    r4, w4 = os.pipe()          # Chrome writes fd 4
    return r3, w3, r4, w4


class Browser:
    """One headed Chrome, one tab, spoken to over the DevTools pipe. Nothing to install, nothing to point at."""

    def __init__(self, profile: Path | None = None, chrome: str | None = None, headless: bool = False):
        self.profile = Path(profile or PROFILE)
        self.chrome = chrome or CHROME
        self.headless = headless
        self.proc = None
        self.w = self.r = None
        self.buf = b""
        self.sid = None
        self.n = 0
        self._tmp = []

    def __enter__(self):
        self.profile.mkdir(parents=True, exist_ok=True)
        r3, w3, r4, w4 = _fds()

        def pre():
            os.dup2(r3, 3)
            os.dup2(w4, 4)
            os.set_inheritable(3, True)
            os.set_inheritable(4, True)

        flags = ["--headless=new"] if self.headless else [
            "--window-position=-3200,-3200", "--window-size=1080,760"]
        self.proc = subprocess.Popen(
            [self.chrome, "--remote-debugging-pipe", "--no-first-run", "--no-default-browser-check",
             "--disable-blink-features=AutomationControlled", "--disable-background-timer-throttling",
             f"--user-data-dir={self.profile}", *flags, "about:blank"],
            pass_fds=(3, 4), preexec_fn=pre, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        os.close(r3)
        os.close(w4)
        self.w, self.r = w3, r4
        tid = self._call(None, "Target.createTarget", {"url": "about:blank"}, timeout=30)["targetId"]
        self.sid = self._call(None, "Target.attachToTarget", {"targetId": tid, "flatten": True}, timeout=30)["sessionId"]
        return self

    def __exit__(self, *exc):
        try:
            if self.proc and self.proc.poll() is None:
                self.proc.terminate()
                try:
                    self.proc.wait(5)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
        finally:
            for fd in (self.w, self.r):
                try:
                    if fd is not None:
                        os.close(fd)
                except OSError:
                    pass

    def _send(self, msg):
        os.write(self.w, _dump(msg).encode() + b"\0")

    def _recv(self, deadline: float):
        while b"\0" not in self.buf:
            left = deadline - time.time()
            if left <= 0:
                return None
            r, _, _ = select.select([self.r], [], [], left)
            if not r:
                return None
            chunk = os.read(self.r, 1 << 16)
            if not chunk:
                return None
            self.buf += chunk
        raw, _, self.buf = self.buf.partition(b"\0")
        return json.loads(raw)

    def _call(self, sid, method, params=None, timeout: float = 20):
        self.n += 1
        msg = {"id": self.n, "method": method, "params": params or {}}
        if sid:
            msg["sessionId"] = sid
        self._send(msg)
        deadline = time.time() + timeout
        while True:
            m = self._recv(deadline)
            if m is None:
                raise TimeoutError(f"no reply to {method}")
            if m.get("id") == self.n:
                if "error" in m:
                    raise RuntimeError(m["error"].get("message") or m["error"])
                return m.get("result") or {}
            if m.get("method") == "Runtime.exceptionThrown":
                self._tmp.append(m)

    def _eval(self, expr, timeout: float = 20):
        try:
            res = self._call(self.sid, "Runtime.evaluate",
                             {"expression": expr, "returnByValue": True, "awaitPromise": True}, timeout=timeout)
        except (RuntimeError, TimeoutError):
            return None
        return ((res.get("result") or {}).get("value"))

    def get(self, url: str, want, *, deadline: float = 30.0) -> dict:
        """Navigate, then poll until `want(payload)` accepts the page. A challenge page is waited out, not bypassed."""
        self._call(self.sid, "Page.navigate", {"url": url}, timeout=20)
        end = time.time() + deadline
        last = ""
        while time.time() < end:
            val = self._eval(want.js, timeout=15)
            if isinstance(val, str) and val:
                try:
                    payload = json.loads(val)
                except ValueError:
                    payload = None
                if payload and want.ok(payload):
                    return payload
                last = (payload or {}).get("title") or last
            time.sleep(1.0)
        if "just a moment" in (last or "").lower():
            raise Challenge(url)
        raise TimeoutError(f"{url} never rendered ({last[:60] or 'blank'})")


class _Want:
    def __init__(self, js, ok):
        self.js, self.ok = js, ok


PRODUCT = _Want(EXTRACT_JS, lambda p: bool(p.get("pd") or p.get("series")))
SEARCH = _Want(SEARCH_JS, lambda p: bool(p.get("links")))


def find_product(b: Browser, query: str) -> tuple[str | None, list[dict]]:
    from urllib.parse import quote_plus
    page = b.get(f"https://www.pricecharting.com/search-products?q={quote_plus(query)}&type=prices", SEARCH)
    seen: set[str] = set()
    links = []
    for x in page.get("links") or []:
        h = x.get("h") or ""
        if not h or not (x.get("t") or "").strip() or h in seen:
            continue
        seen.add(h)
        links.append(x)
    return page.get("title", ""), links


def fetch_card(b: Browser, *, query: str, name: str = "", number: str = "", code: str = "",
               rarity: str = "", url: str | None = None) -> dict:
    """One card: resolve the product page if needed, then read it. Returns the row pieces or a status."""
    note = ""
    if not url:
        _title, links = find_product(b, query)
        url, note = pick_link(links, name=name, number=number, code=code, rarity=rarity)
        if not url:
            return {"status": "nomatch"}
        if url.startswith("/"):
            url = "https://www.pricecharting.com" + url
        time.sleep(random.uniform(1.1, 2.2))
    page = b.get(url, PRODUCT)
    row = parse_product(page)
    if not row["grades"] and not row["series"]:
        return {"status": "empty", "url": url}
    row.update(status="ok", match_note=note)
    return row


def _keep_points(st, pid: str, series: dict) -> int:
    """Every capture's monthly points go into pc_point; a capture only carries its own window, so the store is
    where a year or three of history accumulates. Same x restated: the newer value wins."""
    rows = [(pid, name, int(x), round(float(v), 2)) for name, pts in (series or {}).items()
            for x, v in (pts or []) if v is not None]
    if not rows:
        return 0
    with st.db:
        st.db.executemany(
            "INSERT INTO pc_point (printing_id, name, x, v) VALUES (?,?,?,?) "
            "ON CONFLICT(printing_id, name, x) DO UPDATE SET v=excluded.v", rows)
        for name in {r[1] for r in rows}:
            newest = max(r[2] for r in rows if r[1] == name)
            st.db.execute("DELETE FROM pc_point WHERE printing_id=? AND name=? AND x<?",
                          (pid, name, newest - POINT_KEEP_DAYS * 86_400_000))
    return len(rows)


def _seed(st) -> int:
    """Old stores: copy each stored capture's series into pc_point once, so history starts from what is held."""
    n = 0
    for pid, series in st.db.execute("SELECT printing_id, series FROM pc_card WHERE status='ok'"):
        if st.db.execute("SELECT 1 FROM pc_point WHERE printing_id=? LIMIT 1", (pid,)).fetchone():
            continue
        try:
            n += _keep_points(st, pid, json.loads(series or "{}"))
        except ValueError:
            continue
    return n


def _query_for(pid: str, name: str, number: str, code: str) -> str:
    if code and code != "-":
        return code
    return " ".join(x for x in (name, number) if x)



def pkm_scope(cat, who, dex_max: int = DEX_MAX) -> dict[str, int]:
    """printing id -> dex, for every printing the catalog holds of the in-scope characters."""
    from .character import species_index
    out: dict[str, int] = {}
    for name, cids in species_index(cat).items():
        d = who.dex(name)
        if not d or d > dex_max:
            continue
        for cid in cids:
            for pr in cat.versions(cid):
                out[pr.id] = d
    return out


def _scope(dex_max: int = DEX_MAX) -> dict[str, int]:
    try:
        from .pkm_catalog import DB, PkmCatalog
        from . import zone as Z
        return pkm_scope(PkmCatalog(DB), Z.book(), dex_max)
    except Exception:
        return {}


def _pkm_targets(st, dex_max: int = DEX_MAX) -> list[tuple[str, str]]:
    """Every in-scope printing, uncaptured first, then by the sales demand the board already knows."""
    scope = _scope(dex_max)
    if not scope:
        return [(pid, "pkm") for (pid,) in st.db.execute(
            "SELECT printing_id FROM price_trend WHERE game='pkm' ORDER BY n_sales DESC, last_sale DESC")]
    demand = {pid: (n or 0) for pid, n in st.db.execute(
        "SELECT printing_id, n_sales FROM price_trend WHERE game='pkm'")}
    have = {r[0] for r in st.db.execute("SELECT printing_id FROM pc_card")}
    return [(pid, "pkm") for pid in sorted(scope, key=lambda x: (1 if x in have else 0, -(demand.get(x) or 0), x))]


def cover(st, dex_max: int = DEX_MAX) -> str:
    """How complete the in-scope PriceCharting coverage is, character by character. This is the scoreboard."""
    st.db.executescript(SCHEMA)
    scope = _scope(dex_max)
    if not scope:
        return "pkm catalog unavailable; coverage unknown"
    have = {r[0]: r[1] for r in st.db.execute("SELECT printing_id, status FROM pc_card")}
    total: dict[int, int] = {}
    got: dict[int, int] = {}
    for pid, d in scope.items():
        total[d] = total.get(d, 0) + 1
        if have.get(pid) == "ok":
            got[d] = got.get(d, 0) + 1
    priced = sum(got.values())
    full = sum(1 for d, n in total.items() if got.get(d, 0) >= n)
    lines = [f"dex 1-{dex_max}: {len(total)} characters, {len(scope)} printings",
             f"{priced} priced, {len(scope) - priced} missing, {full} of {len(total)} characters complete"]
    gaps = [(d, got.get(d, 0), n) for d, n in sorted(total.items()) if got.get(d, 0) < n]
    for d, g, n in gaps[:20]:
        lines.append(f"  #{d:03d} {g}/{n}")
    if len(gaps) > 20:
        lines.append(f"  ... {len(gaps) - 20} more characters with gaps")
    return "\n".join(lines)


def sweep(st, *, limit: int = 40, game: str | None = None, refetch: bool = False, ttl_days: int | None = None,
          pids: list[str] | None = None, profile: Path | None = None, chrome: str | None = None,
          headless: bool = False, dex_max: int = DEX_MAX, pacing: tuple[float, float] = (1.1, 2.2),
          log=print) -> dict:
    """Walk the board's best-sold versions and fill `pc_card`. Two page loads per unmapped card, one per refresh."""
    st.db.executescript(SCHEMA)
    _seed(st)
    now = datetime.now(UTC)
    if pids:
        targets = [(p, "pkm" if p.startswith("pk:") else "ygo") for p in pids]
    else:
        targets = []
        if game in (None, "pkm"):
            targets += _pkm_targets(st, dex_max)
        if game in (None, "ygo"):
            targets += list(st.db.execute(
                "SELECT printing_id, game FROM price_trend WHERE game='ygo' "
                "ORDER BY n_sales DESC, last_sale DESC"))
    known = {r[0]: r for r in st.db.execute("SELECT printing_id, url, fetched, status FROM pc_card")}
    queue: list[tuple[str, str]] = []
    for pid, g in targets:
        k = known.get(pid)
        if k and not refetch:
            ttl = FRESH_DAYS.get(g, 10) if ttl_days is None else ttl_days
            try:
                fresh = (now - datetime.fromisoformat(k[2])).days < ttl
            except (TypeError, ValueError):
                fresh = False
            if fresh:
                continue
        queue.append((pid, g))
        if len(queue) >= limit:
            break
    if not queue:
        return {"done": 0, "note": "nothing due"}

    names, numbers, codes, rarities = _identify(st, queue)
    out = {"done": 0, "ok": 0, "nomatch": 0, "empty": 0, "failed": 0}
    seen: dict[str, dict] = {}     # one navigation per product page; printings of it reuse the parse
    with Browser(profile=profile, chrome=chrome, headless=headless) as b:
        for pid, g in queue:
            name, number, code = names.get(pid, ""), numbers.get(pid, ""), codes.get(pid, "")
            query = _query_for(pid, name, number, code)
            prior = known.get(pid)
            url = prior[1] if prior and prior[1] and not refetch else None
            hit = seen.get(url) if url else None
            if hit is not None:
                row = dict(hit)
                row["url"] = url
            else:
                try:
                    row = fetch_card(b, query=query, name=name, number=number, code=code,
                                     rarity=rarities.get(pid, ""), url=url)
                except Challenge:
                    log(f"challenge on {pid}; stopping this pass")
                    out["failed"] += 1
                    break
                except (TimeoutError, RuntimeError, OSError) as exc:
                    log(f"{pid}: {type(exc).__name__} {exc}"[:120])
                    out["failed"] += 1
                    time.sleep(8)
                    continue
                if row.get("status") == "ok" and row.get("url"):
                    seen[row["url"]] = {k: v for k, v in row.items() if k != "status"}
            status = row.pop("status")
            out["done"] += 1
            out[status] = out.get(status, 0) + 1
            with st.db:
                st.db.execute(
                    "INSERT OR REPLACE INTO pc_card (printing_id, game, url, title, query, match_note, grades, series, "
                    "pop, fetched, status) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (pid, g, row.get("url") or url, row.get("title"), query, row.get("match_note"),
                     _dump(row.get("grades") or {}), _dump(row.get("series") or {}), _dump(row.get("pop") or {}),
                     now.isoformat(), status))
            if status == "ok":
                _keep_points(st, pid, row.get("series") or {})
            log(f"{status:7} {pid}")
            time.sleep(random.uniform(*pacing))
    return out


def _identify(st, queue: list[tuple[str, str]]) -> tuple[dict, dict, dict, dict]:
    """Card name, printed number, set code and rarity, from the local catalogs only - never a second request."""
    names: dict[str, str] = {}
    numbers: dict[str, str] = {}
    codes: dict[str, str] = {}
    rarities: dict[str, str] = {}
    cards = None
    pkm = None
    for pid, g in queue:
        if g == "pkm":
            parts = pid.split(":")
            cid = parts[2] if len(parts) > 2 else ""
            if not cid:
                continue
            if pkm is None:
                try:
                    import sqlite3
                    from .pkm_catalog import DB
                    pkm = sqlite3.connect(str(DB))
                except Exception:
                    pkm = False
            if pkm:
                r = pkm.execute("SELECT c.name, c.number, s.name FROM pkm_card c LEFT JOIN pkm_set s ON s.id=c.set_id "
                                "WHERE c.id=?", (cid,)).fetchone()
                if r:
                    names[pid] = " ".join(x for x in (r[0], r[2]) if x)
                    numbers[pid] = (r[1] or "").split("/")[0]
                    ed = (parts[3].split("@")[0] if len(parts) > 3 else "").replace("-", " ")
                    if ed:
                        rarities[pid] = ed     # 1st edition, unlimited, holo: the variant, not a rarity
        else:
            body = pid.split(":", 1)[1] if ":" in pid else ""
            seg = body.split(":")
            code = seg[1] if len(seg) > 1 else "-"
            codes[pid] = code
            rarities[pid] = (seg[3].split("@")[0] if len(seg) > 3 else "").replace("-", " ")
            if code == "-":
                if cards is None:
                    try:
                        from .cardinfo import CardIndex
                        cards = CardIndex()
                    except Exception:
                        cards = False
                pc = re.search(r"@([^@|]+)", pid)
                if cards and pc:
                    c = cards.card(pc.group(1)) or {}
                    names[pid] = c.get("name_en") or ""
    return names, numbers, codes, rarities


def rows_for(st, pids: list[str]) -> dict[str, dict]:
    """Stored cross-market rows for a set of printings, ready for the UI. Missing table means no rows.
    Series are the union of the latest capture and every point pc_point has accumulated."""
    if not pids:
        return {}
    have = {r[0] for r in st.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "pc_card" not in have:
        return {}
    marks = ",".join("?" * len(pids))
    hist: dict[str, dict[str, dict[int, float]]] = {}
    if "pc_point" in have:
        for pid, name, x, v in st.db.execute(
                f"SELECT printing_id, name, x, v FROM pc_point WHERE printing_id IN ({marks})", pids):
            hist.setdefault(pid, {}).setdefault(name, {})[int(x)] = float(v)
    out = {}
    for pid, url, grades, series, pop, fetched in st.db.execute(
            f"SELECT printing_id, url, grades, series, pop, fetched FROM pc_card WHERE printing_id IN ({marks}) "
            "AND status='ok'", pids):
        try:
            cells = json.loads(grades or "{}")
            ser = json.loads(series or "{}")
            for name, pts in (hist.get(pid) or {}).items():
                merged = {int(x): float(v) for x, v in (ser.get(name) or [])}
                merged.update(pts)
                ser[name] = [[x, merged[x]] for x in sorted(merged)]
            out[pid] = {"url": url, "grades": cells, "series": _fix_units(ser, cells),
                        "pop": json.loads(pop or "{}"), "fetched": fetched}
        except ValueError:
            continue
    return out


def grade_series(pc_row: dict | None, gkey: str) -> tuple[str, list] | None:
    """The PriceCharting series that answers for one of our grade keys. Exact first: raw -> Ungraded,
    PSA 10 -> PSA 10, BGS 9.5 -> BGS 9.5, any company's 7/8/9 -> Grade n. Where the exact series is absent,
    the nearest published one stands in and is named, so the surface can say which series answered."""
    if not pc_row:
        return None
    gc, _, gr = (gkey or "|").partition("|")
    series = pc_row.get("series") or {}
    for name in _series_order(gc.upper(), gr):
        pts = series.get(name) or series.get(name.replace("Grade ", ""))
        if pts:
            return name, pts
    return None


def _series_order(gc: str, gr: str) -> list[str]:
    if not gc:
        return ["Ungraded"]
    if gc == "PSA" and gr == "10":
        return ["PSA 10"]
    if gc == "BGS" and gr == "9.5":
        return ["BGS 9.5"]
    if gr in ("7", "8", "9"):
        return [f"Grade {gr}"]
    try:
        n = float(gr)
    except ValueError:
        return []
    if n == 10:
        return ["PSA 10", "BGS 9.5"]
    if n == 9.5:
        return ["BGS 9.5", "PSA 10"]
    return ["Grade 7"] if 1 <= n < 7 else []


def current(pc_row: dict | None, name: str) -> dict | None:
    return (pc_row.get("grades") or {}).get(name) if pc_row else None


def year_change(pts: list) -> float | None:
    """Percent change from the point nearest twelve months before the newest. Points are monthly, so a chart
    spans years; comparing the endpoints would report a five-year move under a one-year label. None unless a
    point sits within 45 days of the year mark - a shorter span would be a guess wearing a year's name."""
    clean = sorted((int(t), float(v)) for t, v in (pts or []) if v and float(v) > 0)
    if len(clean) < 4:
        return None
    last_t, last = clean[-1]
    year = 365 * 86_400_000
    base_t, base = min(clean[:-1], key=lambda p: abs(p[0] - (last_t - year)))
    if abs(base_t - (last_t - year)) > 45 * 86_400_000 or base <= 0:
        return None
    return (last / base - 1) * 100


def stats(st) -> str:
    st.db.executescript(SCHEMA)
    rows = st.db.execute("SELECT status, COUNT(*), MAX(fetched) FROM pc_card GROUP BY status").fetchall()
    n = sum(r[1] for r in rows)
    lines = [f"{r[1]:5} {r[0]:8} last {r[2] or '-'}" for r in rows]
    pts, deep = st.db.execute("SELECT COUNT(*), COUNT(DISTINCT printing_id) FROM pc_point").fetchone()
    return f"{n} cards stored\n" + "\n".join(lines) + f"\n{pts} monthly points kept across {deep} cards"


def ingest(st, path: str) -> int:
    """Import captured product pages: [{printing_id, game?, url?, payload:{...}}]. No network, same parse."""
    st.db.executescript(SCHEMA)
    _seed(st)
    data = json.loads(Path(path).read_text())
    now = datetime.now(UTC).isoformat()
    n = 0
    with st.db:
        for item in data:
            pid = item["printing_id"]
            row = parse_product(item.get("payload") or item)
            st.db.execute(
                "INSERT OR REPLACE INTO pc_card (printing_id, game, url, title, query, match_note, grades, series, "
                "pop, fetched, status) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (pid, item.get("game") or ("pkm" if pid.startswith("pk:") else "ygo"),
                 row.get("url"), row.get("title"), item.get("query"), item.get("match_note"),
                 _dump(row["grades"]), _dump(row["series"]), _dump(row["pop"]), now, "ok"))
            _keep_points(st, pid, row["series"])
            n += 1
    return n


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="python -m cardman.pc")
    ap.add_argument("cmd", choices=["sweep", "ingest", "stats", "cover"])
    ap.add_argument("path", nargs="?")
    ap.add_argument("--store", default="data/store/evidence.sqlite")
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--game", default=None, choices=["ygo", "pkm"])
    ap.add_argument("--refetch", action="store_true")
    ap.add_argument("--ttl-days", type=int, default=None)
    ap.add_argument("--dex-max", type=int, default=DEX_MAX)
    ap.add_argument("--pid", action="append", default=None)
    ap.add_argument("--profile", default=None)
    ap.add_argument("--chrome", default=None)
    ap.add_argument("--headless", action="store_true")
    a = ap.parse_args(argv)
    from .store import Store
    st = Store(a.store)
    if a.cmd == "stats":
        print(stats(st))
    elif a.cmd == "cover":
        print(cover(st, a.dex_max))
    elif a.cmd == "ingest":
        print(f"{ingest(st, a.path)} cards ingested")
    else:
        print(sweep(st, limit=a.limit, game=a.game, refetch=a.refetch, ttl_days=a.ttl_days,
                    pids=a.pid, profile=Path(a.profile) if a.profile else None,
                    chrome=a.chrome, headless=a.headless, dex_max=a.dex_max))


if __name__ == "__main__":
    main()
