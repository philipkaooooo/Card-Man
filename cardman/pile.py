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
from datetime import datetime

_ITEM = re.compile(r"/itm/(?:[^/?#]+/)?(\d{9,})")
_BARE = re.compile(r"^(\d{9,})$")

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


def _card_of(st, key: str) -> tuple[str, str | None]:
    """Passcode and printing for a pile key, from the engine's own card index."""
    if "card_listing" in _tables(st):
        r = st.db.execute("SELECT passcode, printing_id FROM card_listing WHERE key=? "
                          "ORDER BY card_prob DESC LIMIT 1", (key,)).fetchone()
        if r:
            return (r[0] or ""), r[1]
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

    def _m(v, sign=False):
        return "—" if v is None else f"{v:,.2f}"

    it = next((i for i in _items(st, _m) if i["key"] == key), None)
    if it is None:
        return "missing"
    th = it["resale"] if it["resale"] else it["ask"]
    if not th or float(th) <= 0:
        return "noprice"
    chase_add(st, passcode, it["printing_id"] or printing or "", it["title"], round(float(th), 2), home, now)
    remove(st, key)
    return "ok"


def remove(st, key: str) -> None:
    ensure(st.db)
    with st.db:
        st.db.execute("DELETE FROM card_pile WHERE key=?", (key,))


def keys(st) -> list[str]:
    ensure(st.db)
    return [k for (k,) in st.db.execute("SELECT key FROM card_pile ORDER BY added_at DESC")]


def resolve(st, text: str) -> str | None:
    """A pasted eBay link, an item number, or a stored key -> the key the store knows, if it knows one."""
    t = (text or "").strip()
    if not t:
        return None
    if "listing" in _tables(st) and st.db.execute("SELECT 1 FROM listing WHERE key=?", (t,)).fetchone():
        return t
    m = _ITEM.search(t) or _BARE.match(t)
    if not m or "listing" not in _tables(st):
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


def _items(st, M) -> list[dict]:
    from .photo_match import apply
    from .ui_listing import item_id_of, verdict
    ensure(st.db)
    have = _tables(st)
    out = []
    for key, side in st.db.execute("SELECT key, side FROM card_pile ORDER BY added_at DESC"):
        row = None
        if "listing" in have:
            row = st.db.execute(
                "SELECT l.title, l.url, l.data_json, o.status, o.payload_json "
                "FROM listing l LEFT JOIN opportunity o ON o.key=l.key WHERE l.key=?", (key,)).fetchone()
        if row is None:
            continue
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
        out.append({
            "key": key, "side": side or "set", "title": d.get("title") or title or key, "url": d.get("url") or url,
            "printing_id": d.get("printing_id") or "", "code": code,
            "ask": _num(d.get("all_in_home", d.get("ask_home"))),
            "resale": _num(top.get("resale_mid")), "resale_low": _num(top.get("resale_low")),
            "profit": _num(top.get("profit_mid")),
            "basis": top.get("basis") or "none", "n_sales": top.get("n_sales") or 0,
            "kinds": d.get("kinds") or [], "game": d.get("game") or "ygo",
            "iid": item_id_of(key, d), "hist": hist,
        })
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
                    "pic": img(c["passcode"], "small", c["label"], cls="card")})
    return out



def _sort_form(token: str, key: str, side: str, back: str, label: str, cls: str, title: str) -> str:
    from .ui import e
    return (f'<form class="psort" data-side="{side}" method="post" action="/pile/sort">'
            f'<input type="hidden" name="t" value="{e(token)}"><input type="hidden" name="key" value="{e(key)}">'
            f'<input type="hidden" name="side" value="{side}"><input type="hidden" name="back" value="{e(back)}">'
            f'<button class="sbtn {cls}" title="{e(title)}">{label}</button></form>')


def view(st, idx, M, now: datetime, token: str, game: str = "ygo", back: str = "/pile") -> str:
    from .ui import e
    from .ui_listing import pair_art, photo_counts
    items = _items(st, M)
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

    def tile(i):
        photos = {i["iid"]: 1} if i["iid"] and ph.get(i["iid"]) else None
        art = pair_art(i["key"], {"title": i["title"], "printing_id": i["printing_id"], "kinds": i["kinds"]},
                       photos, big=True)
        op = f"/opp/{e(i['key'])}"
        info = (f'<b>{e(i["title"][:90])}</b>'
                f'<div class="pi-line">All-in <b>{M(i["ask"])}</b> · Resale mid <b>{M(i["resale"])}</b></div>'
                f'<div class="pi-line">Low end {M(i["resale_low"])} · {e(i["basis"])} · {i["n_sales"]} sales</div>'
                + (f'<div class="pi-line">Profit {M(i["profit"])} ({roas(i):.2f}× per $)</div>' if roas(i) is not None else "")
                + (f'<div class="pi-line">{" → ".join(str(M(h)) for h in i["hist"])}{_spark(i["hist"])}</div>'
                   if len(i["hist"]) > 1 else "")
                + f'<div class="pi-go"><a href="{op}">Open in CardMan</a>'
                + (f'<a href="{e(i["url"])}" target="_blank" rel="noreferrer noopener">Site ↗</a>'
                   if (i["url"] or "").startswith("http") else "")
                + '</div>')
        drop = (f'<form class="pcard-form" method="post" action="/pile/remove">'
                f'<input type="hidden" name="t" value="{e(token)}"><input type="hidden" name="key" value="{e(i["key"])}">'
                f'<input type="hidden" name="back" value="{e(back)}">'
                f'<button class="pbtn on" title="Out of the pile">✓</button></form>')
        if i["side"] == "pile":
            left = _sort_form(token, i["key"], "set", back, "← Set aside", "left", "Keep it: file left under Set aside")
        else:
            left = _sort_form(token, i["key"], "pile", back, "↥ Pile", "up", "Back to the unfiled pile")
        right = _sort_form(token, i["key"], "chase", back, "Chase →", "right", "Watch this card under a threshold")
        return (f'<div class="pcard" draggable="true" data-side="{e(i["side"])}" data-key="{e(i["key"])}">'
                f'<div class="ppic">{art}</div>{drop}'
                f'<a class="ptitle" href="{op}">{e(i["title"][:70])}</a>'
                f'<div class="pprice">{M(i["ask"])}<span class="call {_cls(i["code"])}">{_glyph(i["code"])} {e(i["code"])}</span></div>'
                f'<div class="pinfo">{info}</div>'
                f'<div class="psortrow">{left}{right}</div></div>')

    pile_tiles = "".join(tile(i) for i in piled)
    aside_tiles = "".join(tile(i) for i in aside)

    chase_tiles = []
    for c in chs:
        if c["under"]:
            state = '<span class="cchip fire">under now</span>'
        elif c["ask"] is not None:
            state = '<span class="cchip watch">watching</span>'
        else:
            state = '<span class="cchip idle">no live ask</span>'
        if c["sold_under"]:
            state += '<span class="cchip sold">sold at/below</span>'
        scope = "any version" if not c["printing_id"] else "one version"
        info = (f'<b>{e(c["label"])}</b>'
                f'<div class="pi-line">Chase under <b>{M(c["threshold"])}</b> · {e(scope)}</div>'
                f'<div class="pi-line">Live ask {M(c["ask"])} · Sold typical {M(c["fair"])}</div>')
        forms = (f'<form class="cset" method="post" action="/pile/chase/set">'
                 f'<input type="hidden" name="t" value="{e(token)}"><input type="hidden" name="id" value="{c["id"]}">'
                 f'<input type="hidden" name="back" value="{e(back)}">'
                 f'<input type="number" name="threshold" step="0.01" min="0.01" value="{c["threshold"]:g}" aria-label="New threshold">'
                 f'<button class="mini">Save</button></form>'
                 f'<form class="pcard-form" method="post" action="/pile/chase/remove">'
                 f'<input type="hidden" name="t" value="{e(token)}"><input type="hidden" name="id" value="{c["id"]}">'
                 f'<input type="hidden" name="back" value="{e(back)}">'
                 f'<button class="pbtn" title="Stop chasing">×</button></form>')
        chase_tiles.append(f'<div class="pcard chase"><div class="ppic">{c["pic"]}</div>{forms}'
                           f'<div class="ptitle">{e(c["label"][:70])}</div>'
                           f'<div class="pprice">{state}</div>'
                           f'<div class="pinfo">{info}</div></div>')

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
    return ('<section><h1>Card Pile</h1>'
            f'<p class="sub">{len(piled)} waiting in the pile, {len(aside)} set aside and {len(chs)} chase{"s" if len(chs) != 1 else ""} '
            'running. New additions land in the pile first - drag one left to set it aside, right to chase it, or use its buttons. '
            'Everything is priced from the sales the store already holds.</p>'
            '<form class="pileadd" method="post" action="/pile">'
            f'<input type="hidden" name="t" value="{e(token)}">'
            f'<input type="hidden" name="back" value="{e(back)}">'
            '<input name="url" placeholder="Paste an eBay listing link or item number" autocomplete="off">'
            '<button>Drop it in the pile</button></form>'
            '<div class="stats">'
            f'<div class="stat"><small>All-in capital</small><b>{M(capital)}</b></div>'
            f'<div class="stat"><small>Expected profit, sale-backed</small><b>{M(upside)}</b></div>'
            f'<div class="stat"><small>Waiting to sort</small><b>{len(piled)}</b></div>'
            f'<div class="stat"><small>Chases under threshold</small><b>{firing}</b></div></div></section>'
            '<section class="board">'
            '<div class="zone zone-pile" data-zone="pile">'
            f'<header class="zhead"><h2>The pile</h2><span class="zhint">every new addition lands here - sort it left or right</span>'
            f'<span class="zcount">{len(piled)}</span></header>'
            + (f'<div class="pfan">{pile_tiles}</div>' if pile_tiles else
               '<p class="empt">Nothing in the pile yet. Paste a listing above, or press ＋ on any listing card - it lands '
               'here first, then you sort it.</p>')
            + '</div><div class="zrow">'
            '<div class="zone zone-set" data-zone="set">'
            f'<header class="zhead"><h2>← Set aside</h2><span class="zhint">kept for a buy you mean to make</span>'
            f'<span class="zcount">{len(aside)}</span></header>'
            + (f'<div class="pgrid">{aside_tiles}</div>' if aside_tiles else
               '<p class="empt">Nothing set aside. Drag a pile card left to keep it here.</p>')
            + '</div>'
            '<div class="zone zone-chase" data-zone="chase">'
            f'<header class="zhead"><h2>Chases →</h2><span class="zhint">the bell fires under your number</span>'
            f'<span class="zcount">{len(chs)}</span></header>'
            + (f'<div class="pgrid">{"".join(chase_tiles)}</div>' if chase_tiles else
               '<p class="empt">No chases. Drag a pile card right, or press Chase on any card page - the threshold is '
               'the sold typical until you move it.</p>')
            + '</div></div></section>'
            + said)


def _cls(code: str) -> str:
    return "good" if code == "BUY" else "warn" if code == "CHECK" else ""


def _glyph(code: str) -> str:
    return "✓" if code == "BUY" else "!" if code == "CHECK" else "·"


PILE_JS = r"""
(function(){
function init(){
 var zones={},tiles=document.querySelectorAll('.pcard[draggable="true"]'),drag=null;
 [].forEach.call(document.querySelectorAll('.zone[data-zone]'),function(z){zones[z.getAttribute('data-zone')]=z});
 if(!tiles.length)return;
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
.board{display:grid;gap:16px;margin:10px 0 6px}
.zone{position:relative;padding:14px 16px 16px;border:1px solid var(--line);
border-radius:26px 20px 28px 18px/20px 28px 18px 26px;
transition:box-shadow .25s var(--ease),border-color .25s var(--ease),background .25s var(--ease)}
.zone .zhead{display:flex;align-items:baseline;gap:10px;margin:0 0 12px;flex-wrap:wrap}
.zone h2{margin:0;font-size:17px;letter-spacing:.01em}
.zone .zhint{color:var(--fg2);font-size:12.5px}
.zone .zcount{margin-left:auto;font-variant-numeric:tabular-nums;border:1px solid var(--line);border-radius:999px;
padding:1px 10px;font-size:12px;color:var(--fg2);background:var(--surface)}
.zone.over{border-color:var(--accent);
outline:2px dashed color-mix(in srgb,var(--accent) 55%,transparent);outline-offset:-8px;
box-shadow:0 0 0 4px color-mix(in srgb,var(--accent) 16%,transparent)}
.zone.over .zhint{color:var(--fg)}
.zone-pile{background:
 radial-gradient(color-mix(in srgb,var(--teal) 9%,transparent) 1px,transparent 1.4px) 0 0/18px 18px,
 radial-gradient(130% 160% at 16% -25%,color-mix(in srgb,var(--teal) 18%,var(--surface)) 0,
  color-mix(in srgb,var(--teal) 6%,var(--surface)) 58%,var(--surface) 100%);
border-color:color-mix(in srgb,var(--teal) 36%,var(--line));
box-shadow:inset 0 1px 0 color-mix(in srgb,#fff 55%,transparent),0 14px 30px rgba(8,40,36,.10)}
.zone-set{background:linear-gradient(165deg,color-mix(in srgb,var(--sky) 17%,var(--surface)),
 color-mix(in srgb,var(--sky) 4%,var(--surface)) 72%);
border-color:color-mix(in srgb,var(--sky) 38%,var(--line));
box-shadow:inset 0 1px 0 color-mix(in srgb,#fff 50%,transparent),0 12px 26px rgba(20,50,80,.09)}
.zone-chase{background:linear-gradient(200deg,color-mix(in srgb,#d0861f 16%,var(--surface)),
 color-mix(in srgb,#d0861f 4%,var(--surface)) 74%);
border-color:color-mix(in srgb,#d0861f 36%,var(--line));
box-shadow:inset 0 1px 0 color-mix(in srgb,#fff 50%,transparent),0 12px 26px rgba(90,55,0,.10)}
.zone-set h2{color:color-mix(in srgb,var(--sky-text) 80%,var(--fg))}
.zone-chase h2{color:color-mix(in srgb,#c77b12 72%,var(--fg))}
.zrow{display:grid;grid-template-columns:1fr 1fr;gap:16px;align-items:start}
.pfan{display:grid;grid-template-columns:repeat(auto-fill,minmax(190px,1fr));gap:18px;perspective:900px}
.pfan .pcard:nth-child(odd){transform:rotate(-1.3deg)}
.pfan .pcard:nth-child(3n){transform:rotate(1.1deg)}
.pfan .pcard:nth-child(4n){transform:rotate(-.3deg)}
.pfan .pcard:hover{transform:translateY(-5px) rotate(0) scale(1.015)}
.pcard.lift{opacity:.35;transform:scale(.97) rotate(0deg)!important}
.pcard{position:relative;display:flex;flex-direction:column;gap:6px;background:var(--surface);border:1px solid var(--line);
border-radius:16px;padding:10px;transition:transform .18s var(--ease),box-shadow .18s var(--ease);cursor:grab}
.pcard:active{cursor:grabbing}
.pcard:hover{transform:translateY(-3px) rotate(0);box-shadow:0 12px 28px rgba(0,0,0,.16);z-index:12}
.ppic{display:flex;justify-content:center;align-items:center;min-height:150px;background:var(--bg);border-radius:11px;
padding:6px;overflow:hidden}
.ppic img.card{max-height:168px;object-fit:contain}
.ptitle{font-size:13px;font-weight:600;color:var(--fg);text-decoration:none;display:-webkit-box;-webkit-line-clamp:2;
-webkit-box-orient:vertical;overflow:hidden;min-height:2.4em;line-height:1.3}
.ptitle:hover{text-decoration:underline}
.pprice{display:flex;align-items:center;justify-content:space-between;gap:8px;font-size:16px;font-weight:750;color:var(--accent);
font-variant-numeric:tabular-nums}
.pcard-form{position:absolute;top:14px;right:14px;z-index:3}
.pbtn{width:26px;height:26px;border-radius:999px;border:1.5px solid var(--line);background:var(--surface);color:var(--fg2);
font-weight:800;font-size:13px;line-height:1;cursor:pointer;padding:0;display:inline-flex;align-items:center;justify-content:center}
.pbtn:hover{border-color:var(--accent);color:var(--accent)}
.pbtn.on{background:var(--accent);border-color:var(--accent);color:#fff}
.pinfo{display:none;position:absolute;left:-1px;right:-1px;top:calc(100% - 3px);z-index:20;background:var(--surface);
border:1.5px solid var(--fg2);border-radius:12px 10px 13px 9px/10px 13px 9px 12px;box-shadow:6px 8px 0 rgba(0,0,0,.14);
padding:11px 12px;font-size:12px;color:var(--fg)}
.pcard:hover .pinfo{display:block}
.pinfo b{display:block;font-size:13px;margin-bottom:5px}
.pi-line{margin:3px 0;color:var(--fg2);font-variant-numeric:tabular-nums}
.pi-line b{display:inline;font-size:12px;color:var(--fg);margin:0}
.pi-go{display:flex;gap:10px;margin-top:7px}
.pi-go a{font-size:12px;font-weight:700;color:var(--teal-deep);text-decoration:none}
.pi-go a:hover{text-decoration:underline}
.pspark{display:inline-block;vertical-align:middle;margin-left:6px}
.psortrow{display:flex;gap:8px;margin-top:2px}
.psort{flex:1;display:flex;margin:0}
.sbtn{width:100%;padding:7px 8px;border-radius:10px 8px 11px 7px/8px 11px 7px 10px;
border:1px solid var(--axis);background:var(--surface);font-size:12px;font-weight:700;color:var(--fg2);cursor:pointer;
transition:background .15s var(--ease),border-color .15s var(--ease),color .15s var(--ease),transform .12s var(--ease)}
.sbtn:hover{transform:translateY(-1px)}
.sbtn.left:hover{border-color:var(--sky);color:color-mix(in srgb,var(--sky-text) 78%,var(--fg));
background:color-mix(in srgb,var(--sky) 14%,var(--surface))}
.sbtn.up:hover{border-color:var(--teal);color:var(--teal-deep);
background:color-mix(in srgb,var(--teal) 13%,var(--surface))}
.sbtn.right:hover{border-color:#c77b12;color:color-mix(in srgb,#c77b12 72%,var(--fg));
background:color-mix(in srgb,#d0861f 14%,var(--surface))}
.empt{margin:6px 4px 10px;color:var(--fg2);font-size:13px;border:1.5px dashed color-mix(in srgb,var(--axis) 80%,transparent);
border-radius:14px;padding:14px 16px;background:color-mix(in srgb,var(--surface) 55%,transparent)}
.cchip{display:inline-block;padding:1px 8px;border-radius:999px;font-size:11px;font-weight:800;letter-spacing:.03em;
text-transform:uppercase;border:1px solid var(--line);color:var(--fg2)}
.cchip.fire{color:#c4553b;border-color:#c4553b;background:color-mix(in srgb,#c4553b 10%,var(--surface))}
.cchip.watch{color:#9a6a15;border-color:#9a6a15;background:color-mix(in srgb,#9a6a15 9%,var(--surface))}
.cchip.sold{color:#1d6b4f;border-color:#1d6b4f;background:color-mix(in srgb,#1d6b4f 10%,var(--surface))}
.cchip.idle{color:var(--muted)}
.pcard.chase .cset{display:flex;gap:6px;margin-top:auto}
.pcard.chase .cset input{width:100%;min-width:0;padding:6px 8px;border:1px solid var(--axis);border-radius:8px;
background:var(--bg);color:var(--fg);font-variant-numeric:tabular-nums}
.chaseform{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:12px 0 4px;padding:10px;border:1px dashed var(--axis);
border-radius:12px;background:var(--surface)}
.chaseform select,.chaseform input{padding:8px 10px;border:1px solid var(--axis);border-radius:9px;background:var(--bg);color:var(--fg)}
.chaseform input[type=number]{width:150px}
.chaseform button{padding:8px 16px;border:0;border-radius:9px;background:var(--accent);color:#fff;font-weight:700;cursor:pointer}
.chaseform button:hover{filter:brightness(1.06)}
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
@media(max-width:860px){.zrow{grid-template-columns:1fr}}
@media(prefers-reduced-motion:reduce){.hungrid>.hun-item{animation:none}.pcard,.pfan .pcard:hover{transition:none;transform:none}}
.dpile{display:inline-flex;align-items:center;gap:7px;margin-left:4px}
.dpile form{display:inline-flex}
.dpile .pbtn{width:24px;height:24px;font-size:12px}
"""
