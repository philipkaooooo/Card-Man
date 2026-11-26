"""One character's market, built from TCGplayer's own numbers: market, low and mid for every version, the
character's summed value on every captured day, the versions listed under their market, and the sales we hold.

Everything here is a guide or an ask. Nothing on this block is a sale. Daily totals only add the versions that
carry a row on that day, so a version added to the pull later cannot fake a jump.
"""
from __future__ import annotations

import html
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import charts

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "data" / "cache" / "catalog.sqlite"

SOLD_DAYS = 90
MOVER_MIN = 2.0
TOP_ROWS = 24
UNDER_GAP = 0.97           # a listing has to be more than 3% under market to count as under it
ASK_CONDS = ("Near Mint", "Lightly Played")


def e(v) -> str:
    return html.escape("" if v is None else str(v))


def _con():
    try:
        return sqlite3.connect(f"file:{CATALOG}?mode=ro", uri=True)
    except sqlite3.Error:
        return None


def _market(pids: list[str]) -> dict[str, list[tuple[str, float | None, float | None, float | None]]]:
    """printing id -> [(day, low, mid, market)] from the daily TCGplayer pull. Missing days stay missing."""
    out: dict[str, list] = {}
    con = _con()
    if not con or not pids:
        return out
    try:
        for i in range(0, len(pids), 400):
            chunk = pids[i:i + 400]
            q = ",".join("?" * len(chunk))
            for pid, day, low, mid, mkt in con.execute(
                    f"SELECT pid, day, low, mid, market FROM market_price WHERE source='tcgplayer' "
                    f"AND pid IN ({q}) AND market IS NOT NULL ORDER BY day", chunk):
                out.setdefault(pid, []).append((day, low, mid, mkt))
    except sqlite3.Error:
        pass
    finally:
        con.close()
    return out


def _tcg_asks(st, pids: list[str]) -> dict[str, dict]:
    """printing id -> cheapest stored TCGplayer ask in playable condition. The listing's product and finish are
    joined to the catalog's cross-reference; a pair the catalog does not speak is skipped, never guessed."""
    have = {r[0] for r in st.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "public_listing" not in have or not pids:
        return {}
    rows = st.db.execute(
        "SELECT product_id, printing, condition, price, shipping FROM public_listing "
        "WHERE source='tcgplayer'").fetchall()
    if not rows:
        return {}
    con = _con()
    if not con:
        return {}
    want = set(pids)
    try:
        prods = sorted({str(r[0]) for r in rows})
        key: dict[tuple[str, str], str] = {}
        for i in range(0, len(prods), 400):
            chunk = prods[i:i + 400]
            for pid, ext, var in con.execute(
                    f"SELECT pid, ext_id, ext_variant FROM xref WHERE source='tcgplayer' "
                    f"AND ext_id IN ({','.join('?' * len(chunk))})", chunk):
                if pid in want:
                    key[(ext, (var or "").strip().lower())] = pid
    except sqlite3.Error:
        return {}
    finally:
        con.close()
    out: dict[str, dict] = {}
    for product_id, printing, cond, price, ship in rows:
        if (cond or "") not in ASK_CONDS:
            continue
        pid = key.get((str(product_id), (printing or "").strip().lower()))
        if not pid:
            continue
        try:
            all_in = float(price) + (float(ship) if ship else 0.0)
        except (TypeError, ValueError):
            continue
        cur = out.get(pid)
        if cur is None or all_in < cur["ask"]:
            out[pid] = {"ask": all_in, "src": "TCG"}
    return out


def _daily_asks(hx: dict) -> dict[str, dict]:
    """The daily ask table's latest low per version. It merges sources, so it is labelled as an ask, not a site."""
    live = hx.get("live") or {}
    return {pid: {"ask": float(a["low"]), "src": "ask"}
            for pid, a in live.items() if a and a.get("low")}


def _sold(st, pids: list[str], now: datetime) -> dict[str, int]:
    """Raw sales per version, the same filter the chart uses: valid, single copy, single printing."""
    from .history import card_sales
    cut = (now - timedelta(days=SOLD_DAYS))
    out: dict[str, int] = {}
    try:
        groups = card_sales(st, pids)
    except Exception:
        return out
    for (pid, gc, _gr), obs in groups.items():
        if gc or not pid:
            continue
        out[pid] = sum(1 for o in obs if o.event_time and o.event_time >= cut)
    return out


def _mn(v, cur: str) -> str:
    return html.escape(charts.fmt(v, cur))


def _pct(v: float | None) -> str:
    return "—" if v is None else f"{v:+.1f}%"


def _chart(days: list[tuple[str, float]], cur: str) -> str:
    """The character's summed market value per captured day. One point per day TCGplayer published."""
    if len(days) < 2:
        return ""
    w, h, L, R, T, B = 920, 220, 60, 20, 16, 30
    lo = min(v for _d, v in days)
    hi = max(v for _d, v in days)
    pad = (hi - lo) * 0.18 or max(1.0, hi * 0.02)
    lo, hi = max(0.0, lo - pad), hi + pad
    x = lambda i: L + (w - L - R) * i / (len(days) - 1)
    y = lambda v: T + (h - T - B) * (1 - (v - lo) / ((hi - lo) or 1))
    parts = []
    for k in range(4):
        v = lo + (hi - lo) * k / 3
        parts.append(f'<line class="cmg" x1="{L}" x2="{w-R}" y1="{y(v):.1f}" y2="{y(v):.1f}"/>'
                     f'<text class="cmt" x="{L-8}" y="{y(v)+4:.1f}" text-anchor="end">{_mn(v, cur)}</text>')
    pts = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, (_d, v) in enumerate(days))
    parts.append(f'<polyline class="cml" points="{pts}"/>')
    step = max(1, len(days) // 6)
    for i, (day, v) in enumerate(days):
        cls = ""
        if i:
            cls = "warm" if v > days[i - 1][1] else "cold" if v < days[i - 1][1] else ""
        parts.append(f'<circle class="cmd {cls}" cx="{x(i):.1f}" cy="{y(v):.1f}" r="{4 if i == len(days)-1 else 3}">'
                     f'<title>{e(day)}: {_mn(v, cur)}</title></circle>')
        if i % step == 0 or i == len(days) - 1:
            lbl = datetime.fromisoformat(day).strftime("%d %b")
            parts.append(f'<text class="cmt" x="{x(i):.1f}" y="{h-8}" text-anchor="middle">{lbl}</text>')
    last = days[-1][1]
    parts.append(f'<text class="cmlast" x="{w-R}" y="{y(last)-9:.1f}" text-anchor="end">{_mn(last, cur)}</text>')
    return (f'<div class="cmchart"><svg viewBox="0 0 {w} {h}" role="img" '
            f'aria-label="Summed TCGplayer market value of this character\'s versions, per captured day">'
            f'{"".join(parts)}</svg>'
            f'<p class="cmcap">Every version with a captured market price, added up. One point per day TCGplayer '
            f'published a price. A guide, not a sale.</p></div>')


def _vol(months: list[dict], cur: str) -> str:
    months = months[-18:]
    vmax = max((b["n"] for b in months), default=0)
    if not months or not vmax:
        return ('<p class="cmfoot">No real sale of any of these cards is recorded yet, so there is no monthly '
                'volume to draw. Guides are not sales.</p>')
    bars = "".join(
        f'<div class="vcol"><div class="vbar" style="height:{max(4, round(b["n"] / vmax * 100))}%" '
        f'title="{e(b["start"][:7])}: {b["n"]} sale{"s" if b["n"] != 1 else ""}"></div>'
        f'<small>{e(b["start"][5:7])}<br>{e(b["start"][2:4])}</small></div>' for b in months)
    return (f'<p class="cmfoot">Real sales per month across every card of this character, last {len(months)} '
            f'month{"s" if len(months) != 1 else ""}. Busiest month: {vmax}.</p><div class="vol">{bars}</div>')


def _asks_cell(p: dict, cur: str) -> str:
    a = p["ask"]
    if not a:
        return "—"
    gap = (a["ask"] - p["market"]) / p["market"] * 100 if p["market"] else 0.0
    cls = "under" if gap < -1 else "flat"
    return (f'<span class="ask"><b>{_mn(a["ask"], cur)}</b><span class="src">{e(a["src"])}</span> '
            f'<span class="{cls}">{_pct(gap)}</span></span>')


def section(st, cat, species: str, ids: list[str], variants: list, now: datetime, cur: str, home: str,
            book, hx: dict) -> str:
    """The character's market block. USD is converted once, on read, at the display rate."""
    from .history import Converter
    conv = Converter(st, home, cur, getattr(book, "fx", None))

    def money(usd):
        try:
            v = conv(float(usd), "USD", now, fetch=False)
        except Exception:
            v = None
        return float(v if v is not None else usd)

    pids = [v.id for v in variants]
    mkt = _market(pids)
    ask_of = _daily_asks(hx)
    ask_of.update(_tcg_asks(st, pids))
    sold = _sold(st, pids, now)
    labels: dict[str, str] = {}
    for cid in ids:
        row = cat._cards.get(cid)
        if not row:
            continue
        s = cat.set(row[3]) or {}
        for v in cat.versions(cid):
            labels[v.id] = f'{row[1]} · {s.get("name") or ""} {row[4]} · {v.edition or "Standard"}'

    per: list[dict] = []
    for v in variants:
        rows = mkt.get(v.id) or []
        if not rows:
            g = cat.guide(v.id)
            if not g:
                continue
            rows = [(str(g[1])[:10], None, None, float(g[0]))]
        day, low, mid, last = rows[-1]
        chg = None
        if len(rows) >= 2 and rows[0][3] and last:
            chg = (last - rows[0][3]) / rows[0][3] * 100
        ask = None
        a = ask_of.get(v.id)
        if a:
            ask = {"ask": money(a["ask"]), "src": a["src"]}
        per.append({"pid": v.id, "name": labels.get(v.id, v.id), "day": day, "market": money(last),
                    "low": money(low) if low is not None else None,
                    "mid": money(mid) if mid is not None else None,
                    "chg": chg, "ask": ask, "sold": sold.get(v.id, 0)})
    if not per:
        return ""
    per.sort(key=lambda p: -(p["market"] or 0.0))
    latest = max(p["day"] for p in per)
    total = sum(p["market"] or 0.0 for p in per if p["day"] == latest)

    days_seen = sorted({d for rows in mkt.values() for d, *_ in rows})
    series = []
    for d in days_seen:
        s = 0.0
        for rows in mkt.values():
            for day, _l, _m, v in rows:
                if day == d and v:
                    s += money(v)
                    break
        if s:
            series.append((d, round(s, 2)))
    move = None
    if len(series) >= 2 and series[0][1]:
        move = (series[-1][1] - series[0][1]) / series[0][1] * 100
    window = len(series)
    chg_label = f"{window}-day" if window > 1 else "move"

    under = [p for p in per if p["ask"] and p["market"] and p["ask"]["ask"] < p["market"] * UNDER_GAP]
    under.sort(key=lambda p: (p["ask"]["ask"] - p["market"]) / p["market"])
    listed = sum(1 for p in per if p["ask"])
    n_sales = sum(p["sold"] for p in per)

    lead = (f'<b>{len(per)}</b> of {len(variants)} versions carry a TCGplayer market price on '
            f'<b>{e(latest)}</b>, <b>{_mn(total, cur)}</b> in total')
    lead += (f', <b class="{"up" if move > 0 else "down" if move < 0 else ""}">{move:+.1f}%</b> across the '
             f'{window} captured days.' if move is not None else '.')
    if under:
        d0 = under[0]
        lead += (f' <b>{len(under)}</b> listed under its market right now; the deepest is '
                 f'<b>{e(d0["name"])}</b> at {_mn(d0["ask"]["ask"], cur)} against '
                 f'{_mn(d0["market"], cur)}.')
    elif listed:
        lead += ' Nothing sits under its market right now.'
    else:
        lead += ' No live ask is captured yet.'

    rows_html = []
    for p in per[:TOP_ROWS]:
        chg_cell = (f'<span class="{"up" if p["chg"] > 0 else "down" if p["chg"] < 0 else "flat"}">'
                    f'{_pct(p["chg"])}</span>' if p["chg"] is not None else '<span class="flat">—</span>')
        rows_html.append(
            f'<tr><td><span class="vname" title="{e(p["name"])}">{e(p["name"])}</span></td>'
            f'<td class="n mk">{_mn(p["market"], cur)}</td>'
            f'<td class="n low">{_mn(p["low"], cur)}</td>'
            f'<td class="n mid">{_mn(p["mid"], cur)}</td>'
            f'<td class="n">{chg_cell}</td><td class="n sold">{p["sold"] or "—"}</td>'
            f'<td class="n">{_asks_cell(p, cur)}</td></tr>')
    table = (f'<table class="cmtab"><thead><tr><th>Card version</th><th class="n">Market</th><th class="n">Low</th>'
             f'<th class="n mid">Mid</th><th class="n">{chg_label}</th><th class="n sold">Sold {SOLD_DAYS}d</th>'
             f'<th class="n">Best ask</th></tr></thead><tbody>{"".join(rows_html)}</tbody></table>')
    if len(per) > TOP_ROWS:
        table += (f'<p class="cmfoot">The top {TOP_ROWS} by market. {len(per) - TOP_ROWS} cheaper versions are in the '
                  f'cards below.</p>')

    movers = [p for p in per if p["chg"] is not None and (p["market"] or 0) >= MOVER_MIN and abs(p["chg"]) >= 0.5]
    movers.sort(key=lambda p: -abs(p["chg"]))
    if movers:
        items = "".join(
            f'<li><span class="vname" title="{e(p["name"])}">{e(p["name"])}</span>'
            f'<span class="{"up" if p["chg"] > 0 else "down"}">{_pct(p["chg"])}</span>'
            f'<span class="mvp">{_mn(p["market"], cur)}</span></li>' for p in movers[:6])
        col_l = (f'<div><h3>Biggest moves, {chg_label}</h3><ul class="cmmoves">{items}</ul>'
                 f'<p class="cmfoot">Versions under {_mn(MOVER_MIN, cur)} and those flat inside a half point '
                 f'are left out. A market price is a guide, not a trade.</p></div>')
        tail = (f'<div class="cmcols">{col_l}<div><h3>Listed under market</h3>{_under_table(under, cur)}</div></div>')
    else:
        tail = f'<h3>Listed under market</h3>{_under_table(under, cur)}'

    stats = (f'<div class="cmstats">'
             f'<div><small>Cards</small><b>{len(ids)}</b></div>'
             f'<div><small>Versions priced</small><b>{len(per)}</b></div>'
             f'<div><small>Market, summed</small><b>{_mn(total, cur)}</b></div>'
             f'<div><small>Move, {chg_label}</small>'
             f'<b class="{"up" if (move or 0) > 0 else "down" if (move or 0) < 0 else ""}">{_pct(move)}</b></div>'
             f'<div><small>Listed now</small><b>{listed}</b></div>'
             f'<div><small>Sold {SOLD_DAYS}d</small><b>{n_sales}</b></div></div>')

    months = (hx.get("series") or [{}])[0].get("month") or []
    return (f'<style>{CSS}</style>'
            f'<section class="chmk"><h2>How {e(species)} is performing</h2>'
            f'<p class="cmlead">{lead}</p>{_chart(series, cur)}{stats}'
            f'<h3>Most valuable versions</h3>{table}{tail}'
            f'<h3>Sales volume</h3>{_vol(months, cur)}'
            f'<p class="cmfoot">Market, low and mid are TCGplayer\'s numbers for the version, captured '
            f'{e(latest)} and converted to {e(cur)}. Asks are what is listed now, with shipping included. '
            f'The sold column counts receipts we hold for that version in the last {SOLD_DAYS} days.</p></section>')


def _under_table(under: list[dict], cur: str) -> str:
    if not under:
        return '<p class="cmfoot">No stored ask sits more than 3% under its market right now.</p>'
    rows = "".join(
        f'<tr><td><span class="vname" title="{e(p["name"])}">{e(p["name"])}</span></td>'
        f'<td class="n"><b>{_mn(p["ask"]["ask"], cur)}</b><span class="src">{e(p["ask"]["src"])}</span></td>'
        f'<td class="n">{_mn(p["market"], cur)}</td>'
        f'<td class="n"><span class="under">'
        f'{_pct((p["ask"]["ask"] - p["market"]) / p["market"] * 100)}</span></td></tr>' for p in under[:8])
    deep = min(((p["ask"]["ask"] - p["market"]) / p["market"] for p in under), default=0.0) < -0.6
    tail = ('<p class="cmfoot">A gap past 60% is usually a mismatched printing or a listing that is not the card. '
            'Open it before buying.</p>' if deep else '')
    return (f'<table class="cmtab"><thead><tr><th>Card version</th><th class="n">Ask</th><th class="n">Market</th>'
            f'<th class="n">Gap</th></tr></thead><tbody>{rows}</tbody></table>{tail}')


CSS = """
section.chmk{background:var(--surface);border:1px solid var(--line);border-radius:20px;padding:22px 24px;margin:0 0 26px;box-shadow:var(--shadow)}
section.chmk h2{margin:0 0 6px;font-size:clamp(22px,2.4vw,30px);letter-spacing:-.02em;font-weight:800}
section.chmk h3{margin:22px 0 8px;font-size:13px;letter-spacing:.08em;text-transform:uppercase;color:var(--muted)}
.cmlead{margin:0 0 12px;font-size:15px;color:var(--fg2);max-width:80ch}
.cmlead b{color:var(--fg)}
.cmlead b.up{color:#0f9a86}.cmlead b.down{color:#c4553b}
.cmchart{margin:6px 0 4px;border:1px solid var(--line);border-radius:14px;padding:12px 10px 2px;
background:linear-gradient(180deg,color-mix(in srgb,var(--soft) 55%,transparent),transparent 75%)}
.cmchart svg{display:block;width:100%;height:auto}
.cmg{stroke:var(--line);stroke-width:1;stroke-dasharray:2 4}
.cmt{fill:var(--muted);font-size:11.5px;font-variant-numeric:tabular-nums}
.cml{fill:none;stroke:var(--fg2);stroke-width:2.2;stroke-linejoin:round;stroke-linecap:round}
.cmd{fill:var(--fg2)}
.cmd.warm{fill:#c9741f}.cmd.cold{fill:#3f6fa8}
.cmlast{fill:var(--fg);font-size:13px;font-weight:800;font-variant-numeric:tabular-nums}
.cmcap{margin:2px 0 8px;font-size:12px;color:var(--muted)}
.cmstats{display:flex;flex-wrap:wrap;gap:10px 26px;margin:14px 0 0}
.cmstats>div{display:flex;flex-direction:column;min-width:84px}
.cmstats small{font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);margin-bottom:2px}
.cmstats b{font-size:clamp(18px,2vw,25px);font-weight:800;font-variant-numeric:tabular-nums;line-height:1.1}
.cmstats b.up{color:#0f9a86}.cmstats b.down{color:#c4553b}
.cmtab{width:100%;border-collapse:collapse;font-size:13px}
.cmtab th{text-align:left;font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);font-weight:600;
padding:0 10px 6px 0;border-bottom:1px solid var(--line);white-space:nowrap}
.cmtab td{padding:8px 10px 8px 0;border-bottom:1px solid var(--line);vertical-align:middle}
.cmtab td.n,.cmtab th.n{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.cmtab .vname{display:block;max-width:360px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--fg)}
.cmtab .mk{font-weight:750}
.cmtab .up,.cmmoves .up{color:#0f9a86;font-weight:700}
.cmtab .down,.cmmoves .down{color:#c4553b;font-weight:700}
.cmtab .ask{color:var(--fg2)}.cmtab .ask b{color:var(--fg)}
.cmtab .src{display:inline-block;margin-left:5px;font-size:10px;font-weight:700;letter-spacing:.04em;
border:1px solid var(--line);border-radius:999px;padding:0 6px;color:var(--muted);vertical-align:1px}
.cmtab .under{color:#0f9a86;font-weight:750}
.cmtab .flat{color:var(--muted)}
.cmcols{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:26px;align-items:start}
.cmmoves{list-style:none;margin:0;padding:0}
.cmmoves li{display:grid;grid-template-columns:minmax(0,1fr) auto auto;gap:12px;align-items:center;
padding:7px 0;border-bottom:1px solid var(--line);font-size:13px}
.cmmoves .vname{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.cmmoves .mvp{font-variant-numeric:tabular-nums;font-weight:700;color:var(--fg2)}
.cmfoot{margin:12px 0 0;font-size:12px;color:var(--muted)}
@media(max-width:820px){.cmcols{grid-template-columns:1fr}}
@media(max-width:700px){.cmtab .mid,.cmtab .sold{display:none}.cmtab .vname{max-width:min(46vw,240px)}}
"""
