"""One small icon set for every page: drawn inline (no fonts, no files from other sites), each with a plain-words
tip on hover and on keyboard focus, a screen-reader label, and the native tooltip as a fallback (removed by ui.js
once the CardMan tip takes over, so there is never a double tooltip)."""
from __future__ import annotations

import html

# 16x16 line icons, stroke = currentColor. Names are what pages ask for; tips come from the caller.
_P = {
    "ebay": '<rect x="1.5" y="3.5" width="13" height="9" rx="2"/><path d="M4.5 8h7M4.5 6h4M4.5 10h5"/>',
    "yahoo": '<path d="M3 3.5l5 5.5 5-5.5M8 9v4"/>',
    "mercari": '<circle cx="8" cy="8" r="5.5"/><path d="M5.5 10.5V6l2.5 3 2.5-3v4.5"/>',
    "bin": '<path d="M2 3h2l1.6 7h6.4l1.5-5H5"/><circle cx="6.5" cy="13" r="1"/><circle cx="11.5" cy="13" r="1"/>',
    "auction": '<path d="M9 2.5l4.5 4.5M7 4.5l4.5 4.5M8 3.5l-3 3 4.5 4.5 3-3M6.5 8L2 12.5"/><path d="M9 14h5"/>',
    "offer": '<path d="M2.5 8.5V3h5.5l6 6-5.5 5.5z"/><circle cx="5.5" cy="6" r="1"/>',
    "graded": '<rect x="3" y="1.5" width="10" height="13" rx="1.5"/><rect x="5" y="3.5" width="6" height="2.5" rx=".5"/><path d="M5 8.5h6v4H5z"/>',
    "raw": '<rect x="3.5" y="2" width="9" height="12" rx="1.5"/><path d="M5.5 5h5M5.5 11h3"/>',
    "first": '<circle cx="8" cy="8" r="6"/><path d="M6.8 6.2L8.2 5v6"/>',
    "unlimited": '<path d="M5 8c0-1.7 1-2.5 2-2.5S9 8 9 8s1 2.5 2 2.5 2-.8 2-2.5-1-2.5-2-2.5S9 8 9 8 8 10.5 7 10.5 5 9.7 5 8z"/>',
    "shadowless": '<rect x="2.5" y="2.5" width="9" height="9" rx="1"/><path d="M13.5 5v8.5H5" stroke-dasharray="1.5 1.5"/>',
    "reverse": '<rect x="3" y="2" width="10" height="12" rx="1.5"/><path d="M5 12l6-8M8 12l3-4"/>',
    "holo": '<rect x="3" y="2" width="10" height="12" rx="1.5"/><path d="M8 5l.9 1.9 2.1.3-1.5 1.4.4 2.1L8 9.7l-1.9 1 .4-2.1L5 7.2l2.1-.3z"/>',
    "en": '<rect x="1.5" y="3.5" width="13" height="9" rx="1.5"/><path d="M4.5 6h2.5M4.5 8h2M4.5 10h2.5M4.5 6v4M9 10V6l2.5 4V6"/>',
    "ja": '<circle cx="8" cy="8" r="6"/><circle cx="8" cy="8" r="2.2" fill="currentColor" stroke="none"/>',
    "confirmed": '<path d="M8 1.5l1.7 1.2 2.1-.1.6 2 1.7 1.3-.8 1.9.8 1.9-1.7 1.3-.6 2-2.1-.1L8 14.5l-1.7-1.2-2.1.1-.6-2-1.7-1.3.8-1.9-.8-1.9 1.7-1.3.6-2 2.1.1z"/><path d="M5.5 8l1.8 1.8 3.2-3.4"/>',
    "ship": '<path d="M1.5 4h8v7h-8zM9.5 6.5h3l2 2.5v2h-5"/><circle cx="4.5" cy="12" r="1.2"/><circle cx="11.5" cy="12" r="1.2"/>',
    "free": '<path d="M1.5 4h8v7h-8zM9.5 6.5h3l2 2.5v2h-5"/><circle cx="4.5" cy="12" r="1.2"/><circle cx="11.5" cy="12" r="1.2"/><path d="M3.5 7.5l1.3 1.3 2.4-2.6"/>',
    "time": '<circle cx="8" cy="8" r="6"/><path d="M8 4.5V8l2.5 1.5"/>',
    "buy": '<circle cx="8" cy="8" r="6.5"/><path d="M5 8.2l2 2 4-4.4"/>',
    "check": '<circle cx="8" cy="8" r="6.5"/><path d="M6.2 6.2a1.9 1.9 0 113 1.5c-.8.5-1.2.9-1.2 1.7M8 11.4v.1"/>',
    "watch": '<path d="M1.5 8s2.3-4.5 6.5-4.5S14.5 8 14.5 8 12.2 12.5 8 12.5 1.5 8 1.5 8z"/><circle cx="8" cy="8" r="2"/>',
    "skip": '<circle cx="8" cy="8" r="6.5"/><path d="M5.7 5.7l4.6 4.6M10.3 5.7l-4.6 4.6"/>',
    "lot": '<rect x="2" y="4" width="8" height="10" rx="1"/><path d="M5 2h7.5a1 1 0 011 1v9"/>',
    "photo": '<rect x="1.5" y="3.5" width="13" height="10" rx="1.5"/><circle cx="8" cy="8.5" r="2.5"/><path d="M5.5 3.5l1-1.5h3l1 1.5"/>',
}


def e(v) -> str:
    return html.escape("" if v is None else str(v), quote=True)


SPRITE = ('<svg xmlns="http://www.w3.org/2000/svg" style="display:none" aria-hidden="true">'
          + "".join(f'<symbol id="i-{k}" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" '
                    f'stroke-linecap="round" stroke-linejoin="round">{v}</symbol>' for k, v in _P.items()) + '</svg>')


def icon(name: str, tip: str, cls: str = "") -> str:
    """A focusable icon that says exactly what it means: hover or Tab to it."""
    if name not in _P:
        raise KeyError(name)
    return (f'<span class="ic{(" " + cls) if cls else ""}" role="img" tabindex="0" aria-label="{e(tip)}" title="{e(tip)}" data-tip="{e(tip)}">'
            f'<svg aria-hidden="true" focusable="false"><use href="#i-{name}"/></svg></span>')


def SVG_USE(name: str) -> str:
    return f'<svg class="svi" aria-hidden="true" focusable="false"><use href="#i-{name}"/></svg>'


def names() -> list[str]:
    return list(_P)


ICON_CSS = """
.ic{display:inline-flex;align-items:center;justify-content:center;width:24px;height:24px;border-radius:7px;color:var(--fg2);
background:var(--soft);vertical-align:middle;transition:background .15s,color .15s,transform .15s}
.ic svg{width:15px;height:15px}.ic:hover,.ic:focus-visible{color:var(--fg);background:color-mix(in srgb,var(--accent) 16%,var(--soft))}
.ic:focus-visible{outline:2px solid var(--accent);outline-offset:1px}.ic.good{color:var(--good-text,var(--good))}.ic.warn{color:#9a6700}
.ic.crit{color:var(--crit)}.ic.info{color:var(--accent)}.icons{display:inline-flex;gap:4px;flex-wrap:wrap;align-items:center}
.ic.txt{width:auto;padding:0 6px;font-size:11px;font-weight:700;gap:3px}
"""


# Small effects only: a lift on hover, a soft fade-in for photos, smooth gallery swaps, clear focus rings.
# Nothing moves for people who ask their system for less motion. No layout shift: sizes are fixed before images load.
EFFECTS_CSS = """
.lrow,.ctile,.tile-l{transition:transform .16s ease,box-shadow .16s ease,border-color .16s ease}
.lrow:hover,.ctile:hover,.tile-l:hover{transform:translateY(-2px);box-shadow:0 8px 22px rgba(0,0,0,.09)}
.lrow:focus-visible,.ctile:focus-visible,.tile-l:focus-visible,a:focus-visible,button:focus-visible,select:focus-visible,
input:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
img.lphoto,img.card,.gmain img{animation:cm-fade .35s ease both}@keyframes cm-fade{from{opacity:0}to{opacity:1}}
.gmain img{transition:opacity .12s ease}.gmain img.swap{opacity:.15}
.artbox{position:relative;display:inline-block;width:64px;height:88px;flex:none}
.artbox img.lphoto{width:64px;height:88px;object-fit:cover;border-radius:8px;background:var(--soft);display:block}
.pair{display:inline-flex;align-items:flex-end;gap:5px;flex:none}
.pair .card{width:56px;height:82px}
.pair.big .card{width:68px;height:100px}
.pair-side{display:flex;flex-direction:column;align-items:center;gap:2px}
.pair-shot{width:26px;height:36px;object-fit:cover;border-radius:3px;display:block;background:var(--soft)}
.pair.big .pair-shot{width:28px;height:40px}
.pair-side small{font-size:9px;line-height:1;color:var(--muted);font-weight:600}
.pair.p-same .pair-shot{box-shadow:0 0 0 2px var(--teal)}
.pair.p-diff .pair-shot{box-shadow:0 0 0 2px var(--fg2)}
.pair.p-unsure .pair-shot{box-shadow:0 0 0 2px var(--axis)}
.refart{position:absolute;right:-6px;bottom:-6px;width:26px;height:37px;border-radius:4px;overflow:hidden;box-shadow:0 0 0 2px var(--surface),0 2px 6px rgba(0,0,0,.2)}
.refart img{width:100%;height:100%;object-fit:cover;display:block}.refart .card.ph{width:100%;height:100%}
.gallery{display:grid;gap:8px}.gmain{position:relative;border-radius:12px;overflow:hidden;background:var(--soft);aspect-ratio:3/4;max-width:340px}
.gmain img{width:100%;height:100%;object-fit:contain;cursor:zoom-in;display:block}
.gthumbs{display:flex;gap:6px;flex-wrap:wrap;max-width:340px}.gthumbs button{padding:0;border:2px solid transparent;border-radius:6px;
background:var(--soft);width:48px;height:48px;overflow:hidden;cursor:pointer}.gthumbs button[aria-current=true]{border-color:var(--accent)}
.gthumbs img{width:100%;height:100%;object-fit:cover;display:block}
.gref{display:flex;gap:10px;align-items:center;font-size:12px;color:var(--fg2);max-width:340px}.gref .card{width:56px;height:auto;border-radius:4px}
.gzoom{position:fixed;inset:0;background:rgba(0,0,0,.86);display:flex;align-items:center;justify-content:center;z-index:100;cursor:zoom-out}
.gzoom img{max-width:94vw;max-height:94vh;object-fit:contain;animation:cm-fade .2s ease both}
.lr-money,.lr-max{display:flex;flex-direction:column;align-items:flex-end;line-height:1.15}.lr-money b{font-size:17px}.lr-max b{font-size:14px;color:var(--good-text,var(--good))}
.lr-money small,.lr-max small{font-size:11px;color:var(--muted);font-weight:500}
.vbadge .svi{width:15px;height:15px;vertical-align:-3px}
@media (prefers-reduced-motion: reduce){*,*::before,*::after{animation:none!important;transition:none!important}
.lrow:hover,.ctile:hover,.tile-l:hover{transform:none}}
"""
