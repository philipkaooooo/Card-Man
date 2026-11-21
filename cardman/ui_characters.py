"""The characters zone. A grid of the Pokémon, then one character's own log. Not a block on Home."""
from __future__ import annotations

from .games import card_href
from .ui import e
from . import charts


def view_zone(rows: list[tuple]) -> str:
    tiles = []
    for slug, name, dex, n in rows:
        tiles.append(
            f'<a class="zchar" href="/pkm/character/{e(slug)}" data-name="{e(name.lower())}">'
            f'<img src="/pkm/icon/{dex}.png" alt="" width="96" height="96" loading="lazy" decoding="async">'
            f'<b>{e(name)}</b><small>{n} card{"s" if n != 1 else ""}</small></a>')
    script = ("<script>(function(){var q=document.getElementById('cfind');if(!q)return;"
              "q.addEventListener('input',function(){var s=q.value.toLowerCase();"
              "document.querySelectorAll('.zchar').forEach(function(a){"
              "a.hidden=s && a.getAttribute('data-name').indexOf(s)<0;});});})();</script>")
    return (f'<section class="zone"><h1>Characters</h1>'
            f'<p class="sub">{len(rows)} Pokémon with a card on this site. The picture is that Pokémon. '
            f'The number is how many different cards it has.</p>'
            f'<input id="cfind" class="cfind" placeholder="Find a character" autocomplete="off">'
            f'<div class="zgrid">{"".join(tiles)}</div></section>{script}')


def _money(usd: float) -> str:
    return e(charts.fmt(usd, "USD"))


def view_one(row: tuple, cards: list[dict], trend: dict, token: str, now) -> str:
    slug, name, dex, n = row
    if trend["moves"]:
        head = (f'<p>{trend["up"]} versions rose, {trend["down"]} fell, {trend["flat"]} held. '
                f'Each line is that version only, from its own logged readings. '
                f'{trend["unread"]} versions have a single reading, so they have no move.</p>')
        lines = []
        for m in trend["moves"][:24]:
            lines.append(f'<p class="char-move">{e(m["card"])} · {e(m["edition"])} ({e(m["set"])} {e(m["number"])}): '
                         f'{_money(m["start"])} on {e(m["start_day"])} → {_money(m["end"])} on {e(m["end_day"])}. This version only.</p>')
        more = f'<p class="muted">{len(trend["moves"]) - 24} more versions moved. They are in the log below.</p>' if len(trend["moves"]) > 24 else ""
        trend_html = head + "".join(lines) + more
    else:
        day = trend["day"] or "no day"
        trend_html = (f'<p>One trusted reading{(" on " + e(day)) if trend["day"] else ""}. '
                      f'It is the TCGplayer market price for each version, logged and not overwritten. '
                      f'A move is the change between two readings of the same version. '
                      f'{trend["unread"]} versions are logged. None of them is a sale, and none of them is the price of another card.</p>')
    body = []
    for c in cards:
        vers = []
        for edition, pts in c["versions"].items():
            reading = " · ".join(f'{_money(usd)} on {e(day)}' for day, usd in pts)
            move = ""
            if len(pts) >= 2 and pts[0][1] != pts[-1][1]:
                move = f' · {_money(pts[0][1])} → {_money(pts[-1][1])}'
            vers.append(f'<li>{e(edition)} · {reading}{move}</li>')
        logged = "<ul class=\"vlog\">" + "".join(vers) + "</ul>" if vers else "<p class=\"muted\">No price of its own logged yet.</p>"
        body.append(f'<a class="clog" href="{e(card_href("pkm-" + c["id"]))}">'
                    f'<span class="ct-name">{e(c["name"])}</span>'
                    f'<span class="ct-meta">{e(c["set"] or "")} · {e(c["number"] or "")} · {e(c["rarity"] or "")}</span>'
                    f'{logged}</a>')
    return (f'<div class="charhead"><img src="/pkm/icon/{dex}.png" alt="" width="120" height="120">'
            f'<div><h1>{e(name)}</h1>'
            f'<p class="sub">{n} card{"s" if n != 1 else ""}. The icon is this Pokémon. Every price below belongs to the card named on that row.</p></div></div>'
            f'<section><h2>Trend</h2>{trend_html}</section>'
            f'<section><h2>Every card</h2><div class="clist">{"".join(body)}</div></section>')


CHAR_CSS = """
section.zone{padding-top:4px}section.zone h1{font-size:clamp(28px,4vw,40px);letter-spacing:-.02em;margin:0 0 6px}
.cfind{width:min(420px,100%);margin:0 0 16px;border-radius:999px;padding:10px 16px}
.zgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(132px,1fr));gap:10px}
.zchar{display:flex;flex-direction:column;align-items:center;gap:4px;text-align:center;text-decoration:none;color:var(--fg);
background:var(--surface);border:1px solid var(--line);border-radius:16px;padding:12px 8px 10px}
.zchar img{width:96px;height:96px;object-fit:contain}
.zchar b{font-size:13px;font-weight:650;line-height:1.2}.zchar small{color:var(--muted);font-size:12px}
.zchar:hover{border-color:var(--axis)}
.charhead{display:flex;gap:16px;align-items:center;margin:0 0 8px}.charhead img{width:120px;height:120px;object-fit:contain}
.clist{display:grid;gap:8px}
.clog{display:block;background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:10px 12px;text-decoration:none;color:var(--fg)}
.clog:hover{border-color:var(--axis)}
.vlog{list-style:none;padding:0;margin:6px 0 0;display:grid;gap:2px}.vlog li{font-size:12px;color:var(--fg2)}
"""
