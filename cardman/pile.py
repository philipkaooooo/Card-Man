"""The Card Pile: listings a person pulled aside, and chases on cards they want at a price.

A pile is not a watchlist with a nicer name. Every row is re-decided here from what the store already knows -
the ask, the shipping, the sales that card actually made, the spread those sales imply - and the page ranks
them against each other on capital and return. Nothing on this page invents a price; it reads the same
snapshots the rest of the site reads. Amounts in a payload are home-currency, same as the engine wrote them.

A chase is a card (any version, or one printing) with a threshold. The engine's own passes refresh the
cheapest live ask per printing from rows the board already holds - no extra scraping, nothing polled blind -
and a chase fires the moment an ask lands at or under the threshold. Sold comps ride along so the threshold
is set against where the market really is, not where the ask sits today.
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

_ITEM = re.compile(r"/itm/(?:[^/?#]+/)?(\d{9,})")
_BARE = re.compile(r"^(\d{9,})$")
_TCGPROD = re.compile(r"tcgplayer\.com/product/(\d+)")
_TCGKEY = re.compile(r"^tcg:(\d+)\|(.+)$")

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "data" / "cache" / "catalog.sqlite"
ASK_CONDS = ("Near Mint", "Lightly Played")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS card_pile (key TEXT PRIMARY KEY, added_at TEXT NOT NULL, side TEXT NOT NULL DEFAULT 'set');
CREATE TABLE IF NOT EXISTS pile_price (
    key TEXT NOT NULL, at TEXT NOT NULL, ask REAL, resale REAL, PRIMARY KEY (key, at));
CREATE TABLE IF NOT EXISTS chase (
    id INTEGER PRIMARY KEY AUTOINCREMENT, passcode TEXT NOT NULL, printing_id TEXT,
    label TEXT NOT NULL, threshold REAL NOT NULL, cur TEXT NOT NULL, created_at TEXT NOT NULL,
    last_ask REAL, last_fair REAL, seen_at TEXT);
"""

SIDES = ("pile", "set", "chase")


def ensure(db) -> None:
    db.executescript(_SCHEMA)
    if "side" not in {r[1] for r in db.execute("PRAGMA table_info(card_pile)")}:
        db.execute("ALTER TABLE card_pile ADD COLUMN side TEXT NOT NULL DEFAULT 'set'")


def _tables(st) -> set[str]:
    return {r[0] for r in st.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def add(st, key: str, now: datetime, side: str = "pile") -> bool:
    """True when the key was not already in the pile. New additions land in the pile, unfiled."""
    ensure(st.db)
    had = st.db.execute("SELECT 1 FROM card_pile WHERE key=?", (key,)).fetchone()
    with st.db:
        if had:
            st.db.execute("UPDATE card_pile SET added_at=? WHERE key=?", (now.isoformat(), key))
        else:
            st.db.execute("INSERT INTO card_pile VALUES (?,?,?)", (key, now.isoformat(), side))
    return had is None


def set_side(st, key: str, side: str) -> None:
    ensure(st.db)
    if side not in SIDES:
        return
    with st.db:
        st.db.execute("UPDATE card_pile SET side=? WHERE key=?", (side, key))


def _catalog_con():
    try:
        return sqlite3.connect(f"file:{CATALOG}?mode=ro", uri=True)
    except sqlite3.Error:
        return None


def _xref_pid(product: str, printing: str) -> str:
    """TCG product + finish -> the catalog printing it is, when the catalog speaks that pair."""
    con = _catalog_con()
    if not con:
        return ""
    try:
        r = con.execute("SELECT pid FROM xref WHERE source='tcgplayer' AND ext_id=? "
                        "AND lower(trim(ext_variant))=? LIMIT 1",
                        (str(product), (printing or "").strip().lower())).fetchone()
        return r[0] if r else ""
    except sqlite3.Error:
        return ""
    finally:
        con.close()


def _tcg_pid(st, key: str) -> str:
    m = _TCGKEY.match(key)
    return _xref_pid(m.group(1), m.group(2)) if m else ""


def _passcode(pid: str) -> str:
    return pid.split("@", 1)[1] if "@" in pid else ""


def _face_pid(d: dict) -> str:
    """The printing whose face to show: the resolved one, else the likeliest the listing could be."""
    pid = d.get("printing_id") or ""
    if pid:
        return pid
    sc = d.get("scenarios") or []
    return (max(sc, key=lambda s: s.get("weight") or 0).get("printing_id") or "") if sc else ""


def known(st, key: str) -> bool:
    """A key the page can render: a stored listing, a card version, or a stored TCGplayer ask."""
    have = _tables(st)
    if "listing" in have and st.db.execute("SELECT 1 FROM listing WHERE key=?", (key,)).fetchone():
        return True
    if key.startswith("card:"):
        return "@" in key
    if key.startswith("tcg:") and "public_listing" in have:
        return st.db.execute("SELECT 1 FROM public_listing WHERE source='tcgplayer' AND card_key=? LIMIT 1",
                             (key,)).fetchone() is not None
    return False


def _card_of(st, key: str) -> tuple[str, str | None]:
    """Passcode and printing for a pile key: the engine's card index first, then the key itself."""
    if "card_listing" in _tables(st):
        r = st.db.execute("SELECT passcode, printing_id FROM card_listing WHERE key=? "
                          "ORDER BY card_prob DESC LIMIT 1", (key,)).fetchone()
        if r:
            return (r[0] or ""), r[1]
    pid = key[5:] if key.startswith("card:") else _tcg_pid(st, key)
    if pid and _passcode(pid):
        return _passcode(pid), pid
    return "", None


def sort(st, key: str, side: str, now: datetime, home: str) -> str:
    """File a pile card left (set aside) or right (chase). A chase takes the sold typical as its threshold,
    the live ask when there is no comp; a card with neither stays in the pile."""
    ensure(st.db)
    if side not in SIDES:
        return "bad"
    if not st.db.execute("SELECT 1 FROM card_pile WHERE key=?", (key,)).fetchone():
        return "missing"
    if side != "chase":
        set_side(st, key, side)
        return "ok"
    passcode, printing = _card_of(st, key)
    if not passcode:
        return "nocard"

    def _home_usd(usd):
        if usd is None:
            return None
        try:
            from .history import Converter
            v = Converter(st, home, home, None)(float(usd), "USD", now, fetch=False)
            return float(v if v is not None else usd)
        except Exception:
            return float(usd)

    pid = key[5:] if key.startswith("card:") else _tcg_pid(st, key) if key.startswith("tcg:") else ""
    fair = ask = None
    try:
        asks, typ = _live(st, now, home, [pid] if pid else [])
        if pid:
            ask = (asks.get(pid) or {}).get("ask")
            fair = (typ.get(pid) or {}).get("fair")
    except Exception:
        pass

    def _m(v, sign=False):
        return "—" if v is None else f"{v:,.2f}"

    it = next((i for i in _items(st, _m) if i["key"] == key), None)
    if it is None:
        return "missing"
    title = it["title"]
    if key.startswith("card:") and fair is None and ask is None:
        con = _catalog_con()
        if con:
            try:
                row = con.execute("SELECT usd FROM market_price WHERE source='tcgplayer' AND pid=? "
                                  "AND market IS NOT NULL ORDER BY day DESC LIMIT 1", (pid,)).fetchone()
                if row:
                    ask = _home_usd(row[0])
            except sqlite3.Error:
                pass
            finally:
                con.close()
    elif key.startswith("tcg:") and ask is None:
        ask = _home_usd(_tcg_all_in(st, key))
    if not key.startswith(("card:", "tcg:")):
        th = it["resale"] if it["resale"] else it["ask"]
    else:
        th = fair if fair else ask
    if not th or float(th) <= 0:
        return "noprice"
    chase_add(st, passcode, pid or printing or "", title, round(float(th), 2), home, now)
    remove(st, key)
    return "ok"


def _tcg_all_in(st, key: str) -> float | None:
    m = _TCGKEY.match(key)
    if not m or "public_listing" not in _tables(st):
        return None
    best = None
    for cond, price, ship, _n, _no, _pr in st.db.execute(
            "SELECT condition, price, shipping, name, number, printing FROM public_listing "
            "WHERE source='tcgplayer' AND card_key=?", (key,)):
        if (cond or "") not in ASK_CONDS:
            continue
        try:
            v = float(price) + (float(ship) if ship else 0.0)
        except (TypeError, ValueError):
            continue
        best = v if best is None or v < best else best
    return best


def remove(st, key: str) -> None:
    ensure(st.db)
    with st.db:
        st.db.execute("DELETE FROM card_pile WHERE key=?", (key,))


def keys(st) -> list[str]:
    ensure(st.db)
    return [k for (k,) in st.db.execute("SELECT key FROM card_pile ORDER BY added_at DESC")]


def resolve(st, text: str) -> str | None:
    """A pasted eBay link, an item number, a TCGplayer link, or a stored key -> the key the store knows."""
    t = (text or "").strip()
    if not t:
        return None
    have = _tables(st)
    if "listing" in have and st.db.execute("SELECT 1 FROM listing WHERE key=?", (t,)).fetchone():
        return t
    if t.startswith("tcg:") and "public_listing" in have and known(st, t):
        return t
    m = _TCGPROD.search(t)
    if m and "public_listing" in have:
        rows = st.db.execute(
            "SELECT card_key, price, shipping, condition FROM public_listing "
            "WHERE source='tcgplayer' AND product_id=?", (m.group(1),)).fetchall()

        def _all_in(r):
            try:
                return float(r[1]) + (float(r[2]) if r[2] else 0.0)
            except (TypeError, ValueError):
                return 1e12
        rows = [r for r in rows if r[0]]
        playable = [r for r in rows if (r[3] or "") in ASK_CONDS] or rows
        return min(playable, key=_all_in)[0] if playable else None
    m = _ITEM.search(t) or _BARE.match(t)
    if not m or "listing" not in have:
        return None
    row = st.db.execute("SELECT key FROM listing WHERE listing_id=? ORDER BY last_seen DESC LIMIT 1",
                        (m.group(1),)).fetchone()
    return row[0] if row else None


# -- chases -----------------------------------------------------------------------------------------------------------

def chase_add(st, passcode: str, printing_id: str, label: str, threshold: float, cur: str, now: datetime) -> int:
    ensure(st.db)
    with st.db:
        cur = st.db.execute(
            "INSERT INTO chase (passcode, printing_id, label, threshold, cur, created_at) VALUES (?,?,?,?,?,?)",
            (passcode, printing_id or None, label[:120], threshold, cur, now.isoformat()))
    return cur.lastrowid


def chase_set(st, chase_id: int, threshold: float) -> None:
    ensure(st.db)
    with st.db:
        st.db.execute("UPDATE chase SET threshold=? WHERE id=?", (threshold, chase_id))


def chase_remove(st, chase_id: int) -> None:
    ensure(st.db)
    with st.db:
        st.db.execute("DELETE FROM chase WHERE id=?", (chase_id,))


def chases(st) -> list[dict]:
    ensure(st.db)
    return [{"id": i, "passcode": pc, "printing_id": pid, "label": lb, "threshold": th, "cur": c,
             "created_at": at, "last_ask": la, "last_fair": lf, "seen_at": s}
            for i, pc, pid, lb, th, c, at, la, lf, s in
            st.db.execute("SELECT id, passcode, printing_id, label, threshold, cur, created_at, last_ask, last_fair, seen_at "
                          "FROM chase ORDER BY created_at DESC")]


def _pids_of(st, chase: dict) -> list[str]:
    """The printings a chase watches: its one printing, or every printing the board knows for its card."""
    if chase["printing_id"]:
        return [chase["printing_id"]]
    if "card_listing" not in _tables(st):
        return []
    return [r[0] for r in st.db.execute(
        "SELECT DISTINCT printing_id FROM card_listing WHERE passcode=? AND printing_id IS NOT NULL",
        (chase["passcode"],))]


def _live(st, now: datetime, home: str, pids: list[str]):
    """Cheapest live ask and its sold typical per printing, straight from the store's own index."""
    from . import alerts
    if not pids:
        return {}, {}
    try:
        return alerts._asks(st, now, home, pids), alerts._typicals(st, pids)
    except Exception:
        return {}, {}


def tick(st, now: datetime, home: str) -> int:
    """Runs inside the engine's own pass, after the sweeps land: refresh pile prices and fire chases.

    Reads only rows the engine already holds, so nothing here scrapes or polls. Returns how many rows changed.
    """
    ensure(st.db)
    have = _tables(st)
    n = 0
    if "card_pile" in have and {"opportunity", "listing"} <= have:
        for (key,) in st.db.execute("SELECT key FROM card_pile"):
            row = st.db.execute("SELECT payload_json FROM opportunity WHERE key=?", (key,)).fetchone()
            if not row:
                continue
            try:
                d = json.loads(row[0] or "{}")
            except ValueError:
                continue
            top = None
            for s in d.get("scenarios") or []:
                if s.get("weight"):
                    top = s
                    break
            ask = d.get("all_in_home", d.get("ask_home"))
            resale = (top or {}).get("resale_mid")
            last = st.db.execute("SELECT ask, resale FROM pile_price WHERE key=? ORDER BY at DESC LIMIT 1",
                                 (key,)).fetchone()
            if last and (last[0], last[1]) == (ask, resale):
                continue
            with st.db:
                st.db.execute("INSERT INTO pile_price VALUES (?,?,?,?)", (key, now.isoformat(), ask, resale))
            n += 1
    if "chase" in have:
        from . import alerts as A
        try:
            from .games import card_href
        except Exception:
            card_href = None
        st.db.executescript(A._SCHEMA)
        for c in chases(st):
            pids = _pids_of(st, c)
            asks, typ = _live(st, now, home, pids)
            best_pid = min(pids, key=lambda p: asks[p]["ask"]) if asks else None
            ask = asks[best_pid]["ask"] if best_pid else None
            fair = None
            for pid in pids:
                if typ.get(pid) and typ[pid].get("fair"):
                    fair = typ[pid]["fair"] if fair is None else min(fair, typ[pid]["fair"])
            with st.db:
                st.db.execute("UPDATE chase SET last_ask=?, last_fair=?, seen_at=? WHERE id=?",
                              (ask, fair, now.isoformat(), c["id"]))
            if ask is not None and ask <= c["threshold"] and (c["last_ask"] is None or ask < c["last_ask"] - 0.005):
                disc = (c["threshold"] - ask) / c["threshold"] * 100 if c["threshold"] else 0.0
                href = card_href(c["passcode"]) if card_href is not None else ""
                detail = {"label": c["label"], "ask": round(ask, 2), "typical": round(c["threshold"], 2),
                          "pct": round(disc, 1), "cur": home,
                          "game": "pkm" if str(c["passcode"]).startswith("pkm-") else "ygo",
                          "day": now.date().isoformat(), "href": href or "",
                          "key": asks[best_pid]["key"], "url": asks[best_pid]["url"],
                          "text": f"{c['label']} asked at {ask:,.0f} {home} - under your {c['threshold']:,.0f} {home}."}
                sid = f"CHASE_UNDER:{c['id']}:{ask:.2f}"
                dj = json.dumps(detail, ensure_ascii=False)
                try:
                    with st.db:
                        got = st.db.execute("INSERT OR IGNORE INTO market_signal VALUES (?,?,?,?,?)",
                                            (sid, "CHASE_UNDER", "", now.isoformat(), dj)).rowcount
                    n += got
                except Exception:
                    pass
    return n


# -- page -------------------------------------------------------------------------------------------------------------

def _num(x):
    try:
        return float(x) if x not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _spark(points: list[float], w: int = 84, h: int = 22) -> str:
    pts = [p for p in points if p is not None]
    if len(pts) < 2:
        return ""
    lo, hi = min(pts), max(pts)
    span = (hi - lo) or 1.0
    step = w / (len(pts) - 1)
    xy = " ".join(f"{i * step:.1f},{h - 3 - (p - lo) / span * (h - 6):.1f}" for i, p in enumerate(pts))
    return (f'<svg class="pspark" viewBox="0 0 {w} {h}" width="{w}" height="{h}" aria-hidden="true">'
            f'<polyline points="{xy}" fill="none" stroke="var(--accent)" stroke-width="1.6"/></svg>')


def _usd(st, M, usd, now) -> float | None:
    if usd is None:
        return None
    try:
        from .history import Converter
        v = Converter(st, M.home, getattr(M, "cur", M.home), None)(float(usd), "USD", now, fetch=False)
        return float(v if v is not None else usd)
    except Exception:
        return float(usd)


_PKM = {}


def _pkm_cat():
    if "v" not in _PKM:
        try:
            from .pkm_catalog import DB, PkmCatalog
            _PKM["v"] = PkmCatalog(DB)
        except Exception:
            _PKM["v"] = None
    return _PKM["v"]


def _cat_for(cat, pid: str):
    by = getattr(cat, "by_id", None) if cat is not None else None
    if isinstance(by, dict) and pid in by:
        return cat
    if "@pkm-" in pid:
        p = _pkm_cat()
        if p is not None:
            return p
    return cat


def _card_label(cat, pid: str) -> str:
    """'Dragonite · Fossil 4/62 · Holofoil' from the catalog, when the catalog has the printing."""
    cat = _cat_for(cat, pid)
    p = None
    by_id = getattr(cat, "by_id", None) if cat is not None else None
    if isinstance(by_id, dict):
        p = by_id.get(pid)
    if p is None and cat is not None:
        pl = getattr(cat, "printings", ()) or ()
        try:
            pl = pl() if callable(pl) else pl
        except TypeError:
            pl = ()
        p = next((x for x in pl if x.id == pid), None)
    if p is None:
        return ""
    fam = p.family_id or ""
    cid = fam.split(":", 1)[1] if ":" in fam else fam
    cards = getattr(cat, "_cards", None)
    row = cards.get(cid) if isinstance(cards, dict) else None
    if row:
        s = cat.set(row[3]) or {} if hasattr(cat, "set") else {}
        name = f"{row[1]} · {s.get('name') or ''}".rstrip(" ·")
        num = f" {row[4]}" if row[4] else ""
        base = f"{name}{num}"
    else:
        base = " ".join(x for x in (p.product_name, p.set_code) if x)
    return f"{base} · {p.edition}" if p and p.edition else base


def _card_money(st, M, cat, pid: str, now):
    ask = resale = None
    cat = _cat_for(cat, pid)
    try:
        g = cat.guide(pid) if cat is not None and hasattr(cat, "guide") else None
    except Exception:
        g = None
    if g:
        ask = _usd(st, M, float(g[0]), now)
    try:
        asks, typ = _live(st, now, M.home, [pid])
        if asks.get(pid):
            ask = asks[pid]["ask"]
        if typ.get(pid):
            resale = typ[pid].get("fair")
    except Exception:
        pass
    return ask, resale


def _card_item(st, key: str, cat, M, now) -> dict | None:
    pid = key[5:]
    pc = _passcode(pid)
    if not pc:
        return None
    ask, resale = _card_money(st, M, cat, pid, now)
    title = _card_label(cat, pid) or pc
    return {"title": title, "url": f"/pkm/card/{pc[4:]}" if pc.startswith("pkm-") else f"/card/{pc}",
            "printing_id": pid, "code": "ASK", "ask": ask, "resale": resale, "resale_low": None,
            "profit": None, "basis": "market" if ask is not None else "none", "n_sales": 0,
            "kinds": [], "game": "pkm" if pc.startswith("pkm-") else "ygo", "iid": "", "hist": [], "pic": pc}


def _tcg_item(st, key: str, cat, M, now) -> dict | None:
    m = _TCGKEY.match(key)
    if not m:
        return None
    rows = st.db.execute(
        "SELECT name, number, printing, condition, price, shipping FROM public_listing "
        "WHERE source='tcgplayer' AND card_key=?", (key,)).fetchall()
    if not rows:
        return None
    best = None
    for name, number, printing, cond, price, ship in rows:
        if (cond or "") not in ASK_CONDS:
            continue
        try:
            all_in = float(price) + (float(ship) if ship else 0.0)
        except (TypeError, ValueError):
            continue
        if best is None or all_in < best[0]:
            best = (all_in, name, number, printing)
    if best is None:
        best = (None, rows[0][0], rows[0][1], rows[0][2])
    all_in, name, number, printing = best
    pid = _tcg_pid(st, key)
    pc = _passcode(pid)
    ask = _usd(st, M, all_in, now)
    resale = None
    if pid:
        try:
            _a, typ = _live(st, now, M.home, [pid])
            if typ.get(pid):
                resale = typ[pid].get("fair")
        except Exception:
            pass
    title = " ".join(x for x in (name or "", ("#" + str(number)) if number else "", f"· {printing}" if printing else "") if x)
    return {"title": title or key, "url": f"https://www.tcgplayer.com/product/{m.group(1)}",
            "printing_id": pid, "code": "ASK", "ask": ask, "resale": resale, "resale_low": None,
            "profit": None, "basis": "market" if ask is not None else "none", "n_sales": 0,
            "kinds": [], "game": "pkm" if pc.startswith("pkm-") else "ygo", "iid": "", "hist": [],
            "pic": pc or ""}


def _listing_item(st, key: str, have: set, M) -> dict | None:
    from .photo_match import apply
    from .ui_listing import item_id_of, verdict
    row = None
    if "listing" in have:
        row = st.db.execute(
            "SELECT l.title, l.url, l.data_json, o.status, o.payload_json "
            "FROM listing l LEFT JOIN opportunity o ON o.key=l.key WHERE l.key=?", (key,)).fetchone()
    if row is None:
        return None
    title, url, data_json, status, payload = row
    base = json.loads(data_json or "{}")
    try:
        d = json.loads(payload) if payload else {
            "title": title, "url": url, "game": base.get("game"), "kinds": [], "scenarios": []}
    except ValueError:
        d = {"title": title, "url": url, "game": base.get("game"), "kinds": [], "scenarios": []}
    d = apply(d, key, st.db)
    code = "OPEN"
    try:
        v = verdict(d, status or "OPEN", M)
        code, top = v.code, v.top or {}
    except Exception:
        top = {}
    hist = [r[0] for r in st.db.execute(
        "SELECT ask FROM pile_price WHERE key=? ORDER BY at DESC LIMIT 8", (key,))]
    hist.reverse()
    pid = _face_pid(d)
    return {
        "title": d.get("title") or title or key, "url": d.get("url") or url,
        "printing_id": pid, "code": code,
        "ask": _num(d.get("all_in_home", d.get("ask_home"))),
        "resale": _num(top.get("resale_mid")), "resale_low": _num(top.get("resale_low")),
        "profit": _num(top.get("profit_mid")),
        "basis": top.get("basis") or "none", "n_sales": top.get("n_sales") or 0,
        "kinds": d.get("kinds") or [], "game": d.get("game") or "ygo",
        "iid": item_id_of(key, d), "hist": hist, "pic": _passcode(pid),
    }


def _items(st, M, cat=None, now=None) -> list[dict]:
    ensure(st.db)
    have = _tables(st)
    now = now or getattr(M, "now", None) or datetime.now(timezone.utc)
    out = []
    for key, side, added in st.db.execute("SELECT key, side, added_at FROM card_pile ORDER BY added_at DESC"):
        if key.startswith("card:"):
            it = _card_item(st, key, cat, M, now)
        elif key.startswith("tcg:"):
            it = _tcg_item(st, key, cat, M, now)
        else:
            it = _listing_item(st, key, have, M)
        if it is None:
            continue
        it.update({"key": key, "side": side or "set", "added": added or ""})
        out.append(it)
    return out


def _chase_rows(st, M) -> list[dict]:
    from .ui_home import img
    all_pids: list[str] = []
    for c in chases(st):
        all_pids += _pids_of(st, c)
    asks, typ = _live(st, M.now, M.home, all_pids) if all_pids else ({}, {})
    out = []
    for c in chases(st):
        pids = _pids_of(st, c)
        best_pid = min(pids, key=lambda p: asks[p]["ask"]) if asks and pids else None
        ask = asks[best_pid]["ask"] if best_pid else c["last_ask"]
        fair = None
        for pid in pids:
            if typ.get(pid) and typ[pid].get("fair"):
                fair = typ[pid]["fair"] if fair is None else min(fair, typ[pid]["fair"])
        under = ask is not None and ask <= c["threshold"]
        sold_under = fair is not None and fair <= c["threshold"]
        out.append({**c, "ask": ask, "fair": fair, "under": under, "sold_under": sold_under,
                    "pic": img(c["passcode"], "small", c["label"], cls="card").replace(' loading="lazy"', "")})
    return out



def _sort_form(token: str, key: str, side: str, back: str, label: str, cls: str, title: str) -> str:
    from .ui import e
    return (f'<form class="psort" data-side="{side}" method="post" action="/pile/sort">'
            f'<input type="hidden" name="t" value="{e(token)}"><input type="hidden" name="key" value="{e(key)}">'
            f'<input type="hidden" name="side" value="{side}"><input type="hidden" name="back" value="{e(back)}">'
            f'<button class="sbtn {cls}" title="{e(title)}">{label}</button></form>')


def _ago(added: str, now: datetime) -> str:
    try:
        then = datetime.fromisoformat(added)
        if then.tzinfo is None:
            then = then.replace(tzinfo=timezone.utc)
        days = (now - then).days
    except (ValueError, TypeError):
        return ""
    if days <= 0:
        return "today"
    if days == 1:
        return "1d"
    if days < 30:
        return f"{days}d"
    return f"{days // 30}mo"


_ICON = {
    "link": '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1" '
            'fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/>'
            '<path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1" fill="none" stroke="currentColor" '
            'stroke-width="1.7" stroke-linecap="round"/></svg>',
    "plus": '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v14M5 12h14" fill="none" '
            'stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>',
    "coin": '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="8" fill="none" '
            'stroke="currentColor" stroke-width="1.6"/><path d="M12 7.5v9M9.4 9.6h4.2a1.6 1.6 0 0 1 0 3.2h-3.2'
            'a1.6 1.6 0 0 0 0 3.2h4.2" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>',
    "stack": '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="5" y="9" width="14" height="9" rx="2" '
             'fill="none" stroke="currentColor" stroke-width="1.6"/><path d="M7 6.5h10M9 4h6" fill="none" '
             'stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>',
    "target": '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="7.5" fill="none" '
              'stroke="currentColor" stroke-width="1.6"/><circle cx="12" cy="12" r="3.4" fill="none" '
              'stroke="currentColor" stroke-width="1.6"/><circle cx="12" cy="12" r="1" fill="currentColor"/></svg>',
    "spark": '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3.5 14 9l5.5 2-5.5 2-2 5.5L10 13l-5.5-2L10 9z" '
             'fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/></svg>',
}


def view(st, idx, M, now: datetime, token: str, game: str = "ygo", back: str = "/pile",
         added: str = "") -> str:
    from .ui import e
    from .ui_home import img
    from .ui_listing import pair_art, photo_counts
    cat = getattr(idx, "cat", idx) if idx is not None else None
    items = _items(st, M, cat, now)
    filed = [i for i in items if i["side"] != "chase"]
    piled = [i for i in filed if i["side"] == "pile"]
    aside = [i for i in filed if i["side"] != "pile"]
    live = [i for i in items if i["code"] in ("BUY", "CHECK", "WATCH")]
    backed = [i for i in items if i["basis"] == "sales" and i["profit"] is not None]
    capital = sum(i["ask"] or 0 for i in items)
    upside = sum(i["profit"] or 0 for i in backed)
    chs = _chase_rows(st, M)
    firing = sum(1 for c in chs if c["under"])

    def roas(i):
        return None if not i["ask"] or i["profit"] is None else i["profit"] / i["ask"]

    ph = photo_counts(st, [i["iid"] for i in filed if i["iid"]])

    def art_of(i) -> str:
        pic = i.get("pic") or ""
        if pic:
            return img(pic, "large", i["title"][:60], cls="card").replace(' loading="lazy"', "")
        photos = {i["iid"]: 1} if i["iid"] and ph.get(i["iid"]) else None
        return pair_art(i["key"], {"title": i["title"], "printing_id": i["printing_id"], "kinds": i["kinds"]},
                        photos, big=True)

    def tile(i):
        is_card = i["key"].startswith(("card:", "tcg:"))
        ext = is_card and i["url"].startswith("http")
        op = e(i["url"]) if is_card else f"/opp/{e(i['key'])}"
        info = (f'<b>{e(i["title"][:90])}</b>'
                f'<div class="pi-line">{"Market" if is_card else "All-in"} <b>{M(i["ask"])}</b>'
                + (f' · Resale <b>{M(i["resale"])}</b>' if i["resale"] is not None else "") + '</div>'
                + (f'<div class="pi-line">{e(i["basis"])} · {i["n_sales"]} sales</div>' if i["n_sales"] else "")
                + (f'<div class="pi-line">Profit {M(i["profit"])} ({roas(i):.2f}× per $)</div>' if roas(i) is not None else "")
                + (f'<div class="pi-line">{" → ".join(str(M(h)) for h in i["hist"])}{_spark(i["hist"])}</div>'
                   if len(i["hist"]) > 1 else "")
                + f'<div class="pi-go"><a href="{op}"{" target=_blank rel=noreferrer" if ext else ""}>'
                + ("Site ↗" if ext else "Open") + '</a></div>')
        drop = (f'<form class="pcard-form" method="post" action="/pile/remove">'
                f'<input type="hidden" name="t" value="{e(token)}"><input type="hidden" name="key" value="{e(i["key"])}">'
                f'<input type="hidden" name="back" value="{e(back)}">'
                f'<button class="pbtn on" title="Out of the pile" aria-label="Out of the pile">✕</button></form>')
        if i["side"] == "pile":
            left = _sort_form(token, i["key"], "set", back, "←", "left", "Set aside: keep it on the left")
        else:
            left = _sort_form(token, i["key"], "pile", back, "↥", "up", "Back to the middle pile")
        right = _sort_form(token, i["key"], "chase", back, "→", "right", "Chase: watch this card under a number")
        when = _ago(i["added"], now)
        flash = '<span class="padd" aria-hidden="true">+ added</span>' if i["key"] == added else ""
        mark = " added" if i["key"] == added else ""
        price = M(i["ask"]) if i["ask"] is not None else "—"
        return (f'<article class="pcard cut{mark}" draggable="true" data-side="{e(i["side"])}" data-key="{e(i["key"])}">'
                f'<div class="pface"><div class="part">{art_of(i)}</div><span class="pglare"></span>{flash}'
                f'<span class="pcoin">{price}</span>'
                + (f'<span class="pwhen" title="Added {e(i["added"][:10])}">{e(when)}</span>' if when else "")
                + drop + '</div>'
                f'<div class="pname">{e(i["title"][:46])}</div>'
                f'<div class="pinfo">{info}</div>'
                f'<div class="psortrow">{left}{right}</div></article>')

    pile_tiles = "".join(tile(i) for i in piled)
    aside_tiles = "".join(tile(i) for i in aside)

    chase_tiles = []
    for c in chs:
        state_cls = "fire" if c["under"] else "watch" if c["ask"] is not None else "idle"
        state = ('<span class="cdot ' + state_cls + '"></span>')
        scope = "any version" if not c["printing_id"] else "one version"
        info = (f'<b>{e(c["label"])}</b>'
                f'<div class="pi-line">Chase under <b>{M(c["threshold"])}</b> · {e(scope)}</div>'
                f'<div class="pi-line">Live ask {M(c["ask"])} · Sold typical {M(c["fair"])}</div>'
                + ('<div class="pi-line sold">Sold at or below your number.</div>' if c["sold_under"] else ""))
        forms = (f'<div class="chase-edit"><form class="cset" method="post" action="/pile/chase/set">'
                 f'<input type="hidden" name="t" value="{e(token)}"><input type="hidden" name="id" value="{c["id"]}">'
                 f'<input type="hidden" name="back" value="{e(back)}">'
                 f'<input type="number" name="threshold" step="0.01" min="0.01" value="{c["threshold"]:g}" aria-label="New threshold">'
                 f'<button class="mini" title="Save the threshold" aria-label="Save the threshold">✓</button></form>'
                 f'<form class="pcard-form" method="post" action="/pile/chase/remove">'
                 f'<input type="hidden" name="t" value="{e(token)}"><input type="hidden" name="id" value="{c["id"]}">'
                 f'<input type="hidden" name="back" value="{e(back)}">'
                 f'<button class="pbtn" title="Stop chasing" aria-label="Stop chasing">✕</button></form></div>')
        chase_tiles.append(f'<article class="pcard cut chase"><div class="pface"><div class="part">{c["pic"]}</div>'
                           f'<span class="pglare"></span><span class="pcoin">{M(c["threshold"])}{state}</span></div>'
                           f'<div class="pname">{e(c["label"][:46])}</div>'
                           f'<div class="pinfo">{info}</div>{forms}</article>')

    assists = []
    if items:
        assists.append(f"{len(backed)} of {len(items)} price from real sales; the rest wait on a comp before they get a profit number.")
        best = max((i for i in items if roas(i) is not None), key=roas, default=None)
        if best is not None:
            assists.append(f"Best return per dollar: {best['title'][:70]} at {roas(best):.2f}× on {M(best['profit'])}.")
        acts = [i for i in live if i["code"] in ("BUY", "CHECK")]
        if acts:
            assists.append(f"{len(acts)} can move now: {', '.join(i['title'][:40] for i in acts[:3])}.")
    if chs:
        under = [c for c in chs if c["under"]]
        if under:
            assists.append(f"{len(under)} chase{'s' if len(under) != 1 else ''} under threshold right now - the bell has the link.")
        sold = [c for c in chs if c["sold_under"]]
        if sold:
            assists.append(f"Sold comps sit at or under your number for {len(sold)} chase{'s' if len(sold) != 1 else ''}: "
                           "the market may have already moved to you.")
    said = ('<section><h2>What the pile says</h2><ul class="moves">' + "".join(f"<li>{e(a)}</li>" for a in assists)
            + "</ul></section>" if assists else "")

    def cue(ic: str, value: str, word: str, cls: str = "") -> str:
        return (f'<span class="pcue {cls}" title="{e(word)}">{_ICON[ic]}<b>{value}</b></span>')

    head = ('<section class="phead">'
            '<div class="phtop"><h1>The Card Pile</h1>'
            '<div class="pcues">'
            + cue("coin", M(capital), "all-in capital across the pile", "money")
            + cue("stack", str(len(piled)), "waiting to sort")
            + cue("target", str(firing), "chases under threshold now", "fire" if firing else "")
            + cue("spark", str(len(backed)), "cards with a sale-backed profit", "")
            + '</div></div>'
            '<form class="pad" method="post" action="/pile">'
            f'<input type="hidden" name="t" value="{e(token)}">'
            f'<input type="hidden" name="back" value="{e(back)}">'
            f'<span class="padico">{_ICON["link"]}</span>'
            '<input class="padin" name="url" autocomplete="off" spellcheck="false" '
            'aria-label="Paste a listing link, item number, or TCGplayer link">'
            f'<button class="padgo" title="Add to the pile" aria-label="Add to the pile">{_ICON["plus"]}</button></form>'
            '</section>')

    def zhead(word: str, hint: str, n: int) -> str:
        return (f'<div class="zhead"><h2>{e(word)}</h2><span class="zhint">{e(hint)}</span>'
                f'<span class="zcount">{n}</span></div>')

    return (head + '<section class="pboard">'
            '<div class="zcol zc-set" data-zone="set">'
            + zhead("Set aside", "kept for a buy you mean to make", len(aside))
            + (f'<div class="pgrid">{aside_tiles}</div>' if aside_tiles else
               '<p class="empt">Drop a pile card here to keep it.</p>')
            + '</div>'
            '<div class="zcol zc-pile" data-zone="pile">'
            + zhead("The pile", "new cards land here - sort left or right", len(piled))
            + (f'<div class="pfan">{pile_tiles}</div>' if pile_tiles else
               '<p class="empt">Nothing in the pile yet. Add a card above, or press ＋ on any listing or card page.</p>')
            + '</div>'
            '<div class="zcol zc-chase" data-zone="chase">'
            + zhead("Chases", "the bell fires under your number", len(chs))
            + (f'<div class="pgrid">{"".join(chase_tiles)}</div>' if chase_tiles else
               '<p class="empt">Drop a pile card here, or press Chase on any card page.</p>')
            + '</div></section>' + said)


def _cls(code: str) -> str:
    return "good" if code == "BUY" else "warn" if code == "CHECK" else ""


def _glyph(code: str) -> str:
    return "✓" if code == "BUY" else "!" if code == "CHECK" else "·"


PILE_JS = r"""
(function(){
function init(){
 var reduce=window.matchMedia&&matchMedia('(prefers-reduced-motion: reduce)').matches;
 if(!reduce){
  [].forEach.call(document.querySelectorAll('.pcard .pface'),function(f){
   f.addEventListener('pointermove',function(e){
    var r=f.getBoundingClientRect(),
        px=(e.clientX-r.left)/r.width-.5,py=(e.clientY-r.top)/r.height-.5;
    f.style.setProperty('--ry',(px*16).toFixed(1)+'deg');
    f.style.setProperty('--rx',(-py*13).toFixed(1)+'deg');
   });
   f.addEventListener('pointerleave',function(){f.style.removeProperty('--ry');f.style.removeProperty('--rx')});
  });
 }
 var a=document.querySelector('.pcard.added');
 if(a){
  setTimeout(function(){a.classList.remove('added')},2400);
  try{var u=new URL(location.href);if(u.searchParams.has('added')){u.searchParams.delete('added');
   history.replaceState({},'',u.pathname+(u.search||'')+u.hash)}}catch(_){}
 }
 var zones={},tiles=document.querySelectorAll('.pcard[draggable="true"]'),drag=null;
 [].forEach.call(document.querySelectorAll('[data-zone]'),function(z){zones[z.getAttribute('data-zone')]=z});
 function clear(){Object.keys(zones).forEach(function(k){zones[k].classList.remove('over')})}
 [].forEach.call(tiles,function(t){
  t.addEventListener('dragstart',function(e){drag=t;t.classList.add('lift');
   if(e.dataTransfer){e.dataTransfer.setData('text/plain','');e.dataTransfer.effectAllowed='move'}clear()});
  t.addEventListener('dragend',function(){if(drag)drag.classList.remove('lift');drag=null;clear()});
 });
 Object.keys(zones).forEach(function(k){
  var z=zones[k];
  z.addEventListener('dragover',function(e){if(e.preventDefault)e.preventDefault();
   if(e.dataTransfer)e.dataTransfer.dropEffect='move';z.classList.add('over')});
  z.addEventListener('dragleave',function(e){if(!z.contains(e.relatedTarget))z.classList.remove('over')});
  z.addEventListener('drop',function(e){if(e.preventDefault)e.preventDefault();clear();if(!drag)return;
   var f=drag.querySelector('form.psort[data-side="'+k+'"]');
   if(f)f.submit()});
 });
}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init);else init();
})();
"""

PILE_CSS = """
.phead{margin:6px 0 18px;padding-left:0;padding-right:0}
.phtop{display:flex;align-items:flex-end;justify-content:space-between;gap:18px;flex-wrap:wrap}
.phead h1{margin:0;font-size:clamp(38px,5.6vw,68px);letter-spacing:-.035em;line-height:.94;font-weight:900}
.pcues{display:flex;gap:8px;flex-wrap:wrap;align-items:center;padding-bottom:8px}
.pcue{display:inline-flex;align-items:center;gap:6px;height:32px;padding:0 12px 0 9px;border:1px solid var(--line);
border-radius:999px;background:var(--surface);color:var(--fg2);font-variant-numeric:tabular-nums}
.pcue svg{width:16px;height:16px;color:var(--muted);flex:none}
.pcue b{font-size:14px;font-weight:800;color:var(--fg)}
.pcue.money b{color:var(--teal-deep)}
.pcue.fire{border-color:color-mix(in srgb,#c4553b 45%,var(--line))}
.pcue.fire b{color:#c4553b}
.pad{display:flex;align-items:center;gap:6px;margin:14px 0 0;height:44px;padding:0 6px 0 13px;border:1px solid var(--line);
border-radius:999px;background:var(--surface);max-width:560px;box-shadow:inset 0 1px 0 rgba(255,255,255,.5)}
.pad:focus-within{border-color:var(--accent);box-shadow:0 0 0 3px color-mix(in srgb,var(--accent) 14%,transparent)}
.padico{display:flex;color:var(--muted)}
.padico svg{width:17px;height:17px}
.padin{flex:1;min-width:0;border:0;background:none;color:var(--fg);font-size:14px;outline:none;height:100%}
.padgo{width:32px;height:32px;border-radius:50%;border:0;background:var(--accent);color:#fff;display:inline-flex;
align-items:center;justify-content:center;cursor:pointer;flex:none}
.padgo svg{width:16px;height:16px}
.padgo:hover{filter:brightness(1.09)}
.pboard{display:grid;grid-template-columns:minmax(180px,.62fr) minmax(0,2.3fr) minmax(180px,.62fr);gap:16px;
align-items:stretch;margin:6px 0 8px;padding:0}
.shell:has(.pboard){max-width:none}
main:has(.pboard){padding-left:16px;padding-right:16px}
.zcol{position:relative;border:1px solid var(--line);padding:12px 12px 14px;min-height:300px;
border-radius:26px 20px 28px 18px/20px 28px 18px 26px;
transition:box-shadow .25s var(--ease),border-color .25s var(--ease),background .25s var(--ease)}
.zcol.over{border-color:var(--accent);outline:2px dashed color-mix(in srgb,var(--accent) 55%,transparent);
outline-offset:-8px;box-shadow:0 0 0 4px color-mix(in srgb,var(--accent) 16%,transparent)}
.zcol.over .zhint{color:var(--fg)}
.zc-pile{background:
 radial-gradient(color-mix(in srgb,var(--teal) 9%,transparent) 1px,transparent 1.4px) 0 0/18px 18px,
 radial-gradient(130% 160% at 16% -25%,color-mix(in srgb,var(--teal) 18%,var(--surface)) 0,
  color-mix(in srgb,var(--teal) 6%,var(--surface)) 58%,var(--surface) 100%);
border-color:color-mix(in srgb,var(--teal) 36%,var(--line));
box-shadow:inset 0 1px 0 color-mix(in srgb,#fff 55%,transparent),0 16px 34px rgba(8,40,36,.11)}
.zc-set{background:linear-gradient(165deg,color-mix(in srgb,var(--sky) 16%,var(--surface)),
 color-mix(in srgb,var(--sky) 4%,var(--surface)) 72%);
border-color:color-mix(in srgb,var(--sky) 38%,var(--line));
box-shadow:inset 0 1px 0 color-mix(in srgb,#fff 50%,transparent),0 12px 26px rgba(20,50,80,.09)}
.zc-chase{background:linear-gradient(200deg,color-mix(in srgb,#d0861f 15%,var(--surface)),
 color-mix(in srgb,#d0861f 4%,var(--surface)) 74%);
border-color:color-mix(in srgb,#d0861f 36%,var(--line));
box-shadow:inset 0 1px 0 color-mix(in srgb,#fff 50%,transparent),0 12px 26px rgba(90,55,0,.10)}
.zc-set h2{color:color-mix(in srgb,var(--sky-text) 80%,var(--fg))}
.zc-chase h2{color:color-mix(in srgb,#c77b12 72%,var(--fg))}
.zhead{display:flex;align-items:baseline;gap:8px;margin:0 2px 8px;flex-wrap:wrap}
.zhead h2{margin:0;font-size:13px;letter-spacing:.08em;text-transform:uppercase;font-weight:800}
.zhint{font-size:11.5px;color:var(--fg2)}
.zcount{margin-left:auto;font-variant-numeric:tabular-nums;border:1px solid var(--line);border-radius:999px;
padding:1px 9px;font-size:11.5px;color:var(--fg2);background:var(--surface)}
.pfan{display:flex;flex-wrap:wrap;justify-content:center;align-items:flex-start;padding:34px 8px 10px;
perspective:1000px;row-gap:26px}
.pgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(96px,1fr));gap:12px 10px;padding:14px 2px 4px}
.pcard{position:relative;display:flex;flex-direction:column;gap:4px;width:132px;flex:none;cursor:grab}
.pgrid .pcard{width:auto}
.pfan .pcard{width:148px;margin:0 -12px}
.pfan .pcard:nth-child(odd){transform:rotate(-2.4deg) translateY(4px)}
.pfan .pcard:nth-child(3n){transform:rotate(2deg)}
.pfan .pcard:nth-child(4n){transform:rotate(-.8deg) translateY(-3px)}
.pfan .pcard:hover{transform:translateY(-9px) rotate(0) scale(1.055);z-index:40}
.pcard:active{cursor:grabbing}
.pcard.lift{opacity:.3;transform:scale(.96) rotate(0)!important}
.pcard:hover{z-index:40}
.pface{position:relative;border-radius:9px;transform-style:preserve-3d;
transform:perspective(760px) rotateX(var(--rx,0deg)) rotateY(var(--ry,0deg));
transition:transform .14s var(--ease);
filter:drop-shadow(0 9px 14px rgba(0,0,0,.22))}
.pcard:hover .pface{filter:drop-shadow(0 17px 28px rgba(0,0,0,.30))}
.part{position:relative;display:block;border-radius:9px;overflow:hidden;aspect-ratio:59/86;
background:linear-gradient(170deg,color-mix(in srgb,var(--fg) 7%,var(--surface)),color-mix(in srgb,var(--fg) 12%,var(--surface)))}
.part img.card{display:block;width:100%;height:100%;object-fit:cover;border-radius:9px}
.part .shot,.part .shot-off{position:absolute;inset:0;display:block;width:100%}
.part .shot img.card,.part .shot-off img.card{height:100%}
.part .card.ph{display:flex;width:100%;aspect-ratio:59/86;align-items:center;justify-content:center;
border-radius:9px;background:var(--bg);color:var(--muted);font-weight:800;border:1px dashed var(--axis)}
.pname{font-size:11px;font-weight:650;text-align:center;line-height:1.22;color:var(--fg);opacity:.92;margin-top:4px;
display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;min-height:2.1em}
.pcoin{position:absolute;left:6px;bottom:6px;z-index:4;padding:2px 8px;border-radius:999px;
background:color-mix(in srgb,#101418 78%,transparent);color:#fff;font-size:11.5px;font-weight:800;
font-variant-numeric:tabular-nums;box-shadow:0 2px 6px rgba(0,0,0,.3)}
.pwhen{position:absolute;right:6px;bottom:6px;z-index:4;padding:2px 7px;border-radius:999px;
background:color-mix(in srgb,#fff 85%,transparent);color:#20242a;font-size:10.5px;font-weight:800}
.pglare{position:absolute;inset:0;border-radius:9px;pointer-events:none;opacity:0;transition:opacity .18s;
background:linear-gradient(115deg,rgba(255,255,255,.36) 0%,rgba(255,255,255,0) 34%,
 rgba(255,255,255,0) 66%,rgba(255,255,255,.18) 100%)}
.pcard:hover .pglare{opacity:1}
.padd{position:absolute;left:50%;top:30%;transform:translateX(-50%);font-size:15px;font-weight:900;
letter-spacing:.02em;color:#fff;text-shadow:0 2px 10px rgba(0,0,0,.6);opacity:0;pointer-events:none;z-index:9;
white-space:nowrap}
.pcard.added .padd{animation:padd 2.2s cubic-bezier(.2,.7,.3,1) forwards}
.pcard.added .pface{animation:pchime 2.2s var(--ease)}
@keyframes padd{0%{opacity:0;transform:translate(-50%,12px)}12%{opacity:1}
 55%{opacity:1;transform:translate(-50%,-14px)}100%{opacity:0;transform:translate(-50%,-30px)}}
@keyframes pchime{0%,100%{filter:drop-shadow(0 9px 14px rgba(0,0,0,.22))}
 18%{filter:drop-shadow(0 0 16px color-mix(in srgb,var(--teal) 85%,transparent))}}
.psortrow{position:absolute;left:50%;top:-15px;transform:translateX(-50%);display:flex;gap:4px;z-index:6;
opacity:0;transition:opacity .14s var(--ease)}
.pcard:hover .psortrow,.pcard:focus-within .psortrow{opacity:1}
.psort{display:flex;margin:0}
.sbtn{width:25px;height:25px;border-radius:50%;border:1px solid var(--axis);background:var(--surface);
color:var(--fg2);font-size:12px;font-weight:800;line-height:1;cursor:pointer;display:inline-flex;
align-items:center;justify-content:center;padding:0;box-shadow:0 2px 5px rgba(0,0,0,.13)}
.sbtn:hover{transform:translateY(-1px)}
.sbtn.left:hover{border-color:var(--sky);color:color-mix(in srgb,var(--sky-text) 78%,var(--fg))}
.sbtn.up:hover{border-color:var(--teal);color:var(--teal-deep)}
.sbtn.right:hover{border-color:#c77b12;color:color-mix(in srgb,#c77b12 72%,var(--fg))}
.pcard-form{position:absolute;top:-8px;right:-8px;z-index:7;opacity:0;transition:opacity .14s var(--ease)}
.pcard:hover .pcard-form,.pcard-form:focus-within{opacity:1}
.pbtn{width:22px;height:22px;border-radius:50%;border:1px solid var(--axis);background:var(--surface);
color:var(--fg2);font-weight:800;font-size:10px;line-height:1;cursor:pointer;padding:0;
display:inline-flex;align-items:center;justify-content:center;box-shadow:0 2px 5px rgba(0,0,0,.14)}
.pbtn:hover{border-color:var(--accent);color:var(--accent)}
.pbtn.on{background:var(--accent);border-color:var(--accent);color:#fff}
.pinfo{display:none;position:absolute;left:50%;top:calc(100% + 6px);transform:translateX(-50%);z-index:60;
width:max-content;max-width:260px;background:var(--surface);border:1.5px solid var(--fg2);
border-radius:12px 10px 13px 9px/10px 13px 9px 12px;box-shadow:6px 8px 0 rgba(0,0,0,.14);
padding:10px 12px;font-size:12px;color:var(--fg)}
.pcard:hover .pinfo{display:block}
.pinfo b{display:block;font-size:12.5px;margin-bottom:4px}
.pi-line{margin:3px 0;color:var(--fg2);font-variant-numeric:tabular-nums}
.pi-line b{display:inline;font-size:12px;color:var(--fg);margin:0}
.pi-line.sold{color:#1d6b4f;font-weight:700}
.pi-go{display:flex;gap:10px;margin-top:6px}
.pi-go a{font-size:12px;font-weight:700;color:var(--teal-deep);text-decoration:none}
.pi-go a:hover{text-decoration:underline}
.pspark{display:inline-block;vertical-align:middle;margin-left:6px}
.cdot{display:inline-block;width:7px;height:7px;border-radius:50%;margin-left:5px;vertical-align:1px;background:#8a8f96}
.cdot.fire{background:#e05a3a;box-shadow:0 0 0 3px color-mix(in srgb,#e05a3a 25%,transparent)}
.cdot.watch{background:#e0a53a}
.chase-edit{display:flex;align-items:center;gap:4px;margin-top:2px}
.cset{display:flex;flex:1;gap:4px;margin:0}
.cset input{width:100%;min-width:0;padding:4px 6px;border:1px solid var(--axis);border-radius:7px;
background:var(--bg);color:var(--fg);font-size:11.5px;font-variant-numeric:tabular-nums}
.mini{width:22px;height:22px;border:0;border-radius:7px;background:var(--accent);color:#fff;font-weight:800;
font-size:11px;cursor:pointer;flex:none;padding:0}
.chaseform{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:12px 0 4px;padding:10px;
border:1px dashed var(--axis);border-radius:12px;background:var(--surface)}
.chaseform select,.chaseform input{padding:8px 10px;border:1px solid var(--axis);border-radius:9px;background:var(--bg);color:var(--fg)}
.chaseform input[type=number]{width:150px}
.chaseform button{padding:8px 16px;border:0;border-radius:9px;background:var(--accent);color:#fff;font-weight:700;cursor:pointer}
.chaseform button:hover{filter:brightness(1.06)}
.empt{margin:8px 4px;color:var(--fg2);font-size:12.5px;border:1.5px dashed color-mix(in srgb,var(--axis) 80%,transparent);
border-radius:14px;padding:12px 14px;text-align:center;background:color-mix(in srgb,var(--surface) 55%,transparent)}
.hun-item{position:relative;display:flex;min-width:0}
.hun-item>.hun{flex:1}
.hun-item .pilebtn{position:absolute;top:8px;right:8px;z-index:5;opacity:0;transition:opacity .15s}
.hun-item:hover .pilebtn{opacity:1}
.hun-item .pilebtn .pbtn{background:color-mix(in srgb,var(--surface) 88%,transparent)}
.hungrid>.hun-item{animation:hunin .5s cubic-bezier(.2,.7,.3,1) backwards}
.hungrid>.hun-item:nth-child(2){animation-delay:40ms}.hungrid>.hun-item:nth-child(3){animation-delay:80ms}
.hungrid>.hun-item:nth-child(4){animation-delay:120ms}.hungrid>.hun-item:nth-child(5){animation-delay:160ms}
.hungrid>.hun-item:nth-child(6){animation-delay:200ms}.hungrid>.hun-item:nth-child(7){animation-delay:240ms}
.hungrid>.hun-item:nth-child(8){animation-delay:280ms}.hungrid>.hun-item:nth-child(9){animation-delay:320ms}
.hungrid>.hun-item:nth-child(10){animation-delay:360ms}.hungrid>.hun-item:nth-child(11){animation-delay:400ms}
.hungrid>.hun-item:nth-child(12){animation-delay:440ms}
.cp-img{position:relative}
.cpile{position:absolute;top:10px;right:10px;z-index:8}
.cpile .pbtn{width:30px;height:30px;font-size:15px;border-width:2px;background:color-mix(in srgb,var(--surface) 90%,transparent)}
@media(max-width:1120px){.pboard{grid-template-columns:1fr 1fr}.zc-pile{grid-column:1/-1;order:-1}}
@media(max-width:700px){.pboard{grid-template-columns:1fr}.pad{max-width:none}
 .phead h1{font-size:clamp(34px,11vw,54px)}.pfan .pcard{margin:0 -10px}}
@media(hover:none){.psortrow,.pcard-form{opacity:1}}
@media(prefers-reduced-motion:reduce){.hungrid>.hun-item{animation:none}
 .pcard,.pfan .pcard:hover,.pface,.pcard.added .pface,.pcard.added .padd{transition:none!important;animation:none!important}
 .pfan .pcard,.pfan .pcard:nth-child(odd),.pfan .pcard:nth-child(3n),.pfan .pcard:nth-child(4n){transform:none!important}}
.dpile{display:inline-flex;align-items:center;gap:7px;margin-left:4px}
.dpile form{display:inline-flex}
.dpile .pbtn{width:24px;height:24px;font-size:12px}
"""
