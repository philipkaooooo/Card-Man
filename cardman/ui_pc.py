"""PriceCharting on a card page: the six grades PC tracks for this exact printing, drawn to be poked at.

Everything here comes from the sweep's own capture (pc_card). PriceCharting sells in USD; every number below
is converted to the display currency once, on the server, so the chart, the chips and the table agree.
"""
from __future__ import annotations

import html
import json
from datetime import datetime
from decimal import Decimal

from . import pc

SYM = {"USD": "$", "TWD": "NT$", "JPY": "¥", "EUR": "€", "GBP": "£"}
SERIES_FOR = {"Grade 9.5": "BGS 9.5"}


def e(v) -> str:
    return html.escape("" if v is None else str(v), quote=True)


def _fx_usd(st, cur: str, now: datetime) -> float:
    if cur == "USD":
        return 1.0
    try:
        from .fx import FxTable
        return float(FxTable(st.fx_rates()).convert(Decimal(1), "USD", cur, now))
    except Exception:
        return 1.0


def _year_change(pts: list) -> float | None:
    live = [(x, v) for x, v in pts if v > 0]
    if len(live) < 2 or live[0][1] <= 0:
        return None
    return (live[-1][1] - live[0][1]) / live[0][1] * 100


def section(st, printing_id: str, cur: str, now: datetime) -> str:
    row = pc.rows_for(st, [printing_id]).get(printing_id) if printing_id else None
    if not row:
        return ""
    k = _fx_usd(st, cur, now)
    grades = row.get("grades") or {}
    series = row.get("series") or {}
    out = []
    for g in pc.GRADES:
        cell = grades.get(g) or {}
        pts = [(int(x), round(float(v) * k, 2)) for x, v in (series.get(SERIES_FOR.get(g, g)) or series.get(g) or [])
               if v is not None]
        out.append({"g": g, "p": round(float(cell["p"]) * k, 2) if cell.get("p") is not None else None,
                    "c": round(float(cell["c"]) * k, 2) if cell.get("c") is not None else None,
                    "vol": cell.get("vol"), "y": _year_change(pts), "pts": pts})
    if not any(x["pts"] or x["p"] for x in out):
        return ""
    pop = row.get("pop") or {}
    psa, cgc = pop.get("psa") or [], pop.get("cgc") or []
    popnote = ""
    if len(psa) >= 10:
        bits = [f"PSA 10: {psa[9]:,} graded"]
        if len(psa) >= 9:
            bits.append(f"PSA 9: {psa[8]:,}")
        if len(cgc) >= 10:
            bits.append(f"CGC 10: {cgc[9]:,}")
        popnote = f'<p class="pcpop muted small">{e(" · ".join(bits))} (population, all copies ever graded)</p>'
    rows = []
    for x in out:
        if x["p"] is None:
            continue
        ch = "" if x["c"] in (None, 0) else (f' <span class="{"up" if x["c"] > 0 else "down"}">{x["c"]:+,.2f}</span>')
        yr = "—" if x["y"] is None else f'{x["y"]:+.0f}%'
        rows.append(f'<tr data-g="{e(x["g"])}"><td>{e(x["g"])}</td><td class="n">{SYM.get(cur, "")}{x["p"]:,.2f}{ch}</td>'
                    f'<td class="n">{yr}</td><td>{e(x["vol"] or "—")}</td></tr>')
    payload = {"cur": cur, "sym": SYM.get(cur, ""), "grades": out,
               "url": row.get("url"), "title": row.get("title")}
    return ('<section id="pcsheet"><h2>Graded prices on PriceCharting</h2>'
            '<p class="hnote">What copies of this exact printing sold for at each grade PC tracks. '
            'Pick the grades to compare.</p>'
            f'<div data-pcsheet><script type="application/json" class="pcjson">{json.dumps(payload).replace("</", "<\\/")}</script>'
            '<div class="pctabs" role="group" aria-label="Grades to draw"></div>'
            '<div class="pcplot"></div><div class="pcleg"></div>'
            '<div class="wrap"><table class="compact pctable"><tr><th>Grade</th><th class="n">Now</th><th class="n">Since first point</th>'
            f'<th>Sold</th></tr>{"".join(rows)}</table></div>{popnote}'
            + (f'<p class="small"><a href="{e(row["url"])}" target="_blank" rel="noreferrer noopener">Open this card on '
               'PriceCharting</a></p>' if row.get("url") else "")
            + '</div></section>')


PC_CSS = """
#pcsheet .pctabs{display:flex;flex-wrap:wrap;gap:6px;margin:2px 0 10px}
#pcsheet .pctabs button{border:1px solid var(--axis);background:none;border-radius:999px;padding:4px 11px;font-size:12.5px;
color:var(--fg2);cursor:pointer;margin:0;display:inline-flex;gap:7px;align-items:center}
#pcsheet .pctabs button[aria-pressed=true]{color:var(--fg);border-color:var(--fg2);background:color-mix(in srgb,var(--soft) 55%,var(--surface))}
#pcsheet .pctabs i{display:inline-block;width:9px;height:9px;border-radius:50%}
#pcsheet .pctabs small{color:var(--muted);font-size:11px}
#pcsheet .pcplot{position:relative;width:100%;touch-action:pan-y;margin:0 0 4px}
#pcsheet .pcplot svg{display:block;width:100%;height:auto;overflow:visible}
#pcsheet .pcplot .ax{stroke:var(--line);stroke-width:1}#pcsheet .pcplot .tk{fill:var(--fg2);font-size:11px}
#pcsheet .pcplot .xh{stroke:var(--fg2);stroke-width:1;stroke-dasharray:2 3}
#pcsheet .pcleg{display:flex;flex-wrap:wrap;gap:4px 14px;font-size:12px;color:var(--fg2);margin:2px 0 8px}
#pcsheet .pcleg i{display:inline-block;width:14px;height:3px;vertical-align:middle;margin-right:5px;border-radius:2px}
#pcsheet .pctable .up{color:var(--teal-deep)}#pcsheet .pctable .down{color:#c4553b}
#pcsheet .pcpop{margin:6px 0 2px}
"""


PC_JS = r"""
(function(){
var COL=['#2f7d6d','#b07c2a','#4a6fa5','#8a5a9a','#c4553b','#6b7280'];
function fmt(v,sym){if(v==null)return '—';var d=Math.abs(v)>=1000?0:2;
 return sym+Number(v).toLocaleString('en-US',{minimumFractionDigits:d,maximumFractionDigits:d})}
function el(n,a,t){var x=document.createElementNS('http://www.w3.org/2000/svg',n);for(var k in a)x.setAttribute(k,a[k]);if(t!=null)x.textContent=t;return x}
function h(tag,a,t){var x=document.createElement(tag);for(var k in a)x.setAttribute(k,a[k]);if(t!=null)x.textContent=t;return x}
function nice(lo,hi,n){var span=hi-lo||Math.abs(hi)||1,step=Math.pow(10,Math.floor(Math.log10(span/n))),e=span/n/step;
 step*=e>=5?10:e>=2?5:e>=1?2:1;var t=[],v=Math.ceil(lo/step)*step;for(;v<=hi+1e-9;v+=step)t.push(v);return t}
document.querySelectorAll('[data-pcsheet]').forEach(function(root){
 var d=JSON.parse(root.querySelector('.pcjson').textContent),sym=d.sym||'$';
 var gs=d.grades.filter(function(g){return g.pts.length||g.p!=null}),on={};
 gs.forEach(function(g,i){g.color=COL[i%COL.length]});
 var def=gs.filter(function(g){return g.g==='Ungraded'||g.g==='PSA 10'}),pick=def.length?def:[gs[0]];
 pick.forEach(function(g){on[g.g]=true});
 var tabs=root.querySelector('.pctabs'),plot=root.querySelector('.pcplot'),leg=root.querySelector('.pcleg');
 var tip=h('div',{'class':'htip',hidden:''});plot.appendChild(tip);
 gs.forEach(function(g){var b=h('button',{type:'button','aria-pressed':String(!!on[g.g])});
  b.appendChild(h('i',{style:'background:'+g.color}));b.appendChild(document.createTextNode(g.g));
  if(g.p!=null)b.appendChild(h('small',{},fmt(g.p,sym)));
  b.onclick=function(){on[g.g]=!on[g.g];b.setAttribute('aria-pressed',String(!!on[g.g]));if(!gs.some(function(q){return on[q.g]})){on[g.g]=true;b.setAttribute('aria-pressed','true')}draw()};
  tabs.appendChild(b)});
 function draw(){
  var vis=gs.filter(function(g){return on[g.g]&&g.pts.length}),W=Math.max(300,plot.clientWidth||640),H=W<520?220:280,
   L=W<520?48:62,R=12,T=12,B=26;
  plot.querySelectorAll('svg').forEach(function(n){n.remove()});
  leg.textContent='';
  vis.forEach(function(g){var sp=h('span',{});sp.appendChild(h('i',{style:'background:'+g.color}));
   sp.appendChild(document.createTextNode(g.g+(g.vol?' · '+g.vol:'')));leg.appendChild(sp)});
  if(!vis.length){plot.insertBefore(h('p',{'class':'muted'},'Pick a grade above.'),tip);return}
  var x0=Infinity,x1=-Infinity,lo=Infinity,hi=-Infinity;
  vis.forEach(function(g){g.pts.forEach(function(p){x0=Math.min(x0,p[0]);x1=Math.max(x1,p[0]);lo=Math.min(lo,p[1]);hi=Math.max(hi,p[1])})});
  if(!(hi>lo)){hi=lo+1;lo=Math.max(0,lo-.2)}
  var pad=(hi-lo)*.12||1;lo=Math.max(0,lo-pad);hi+=pad;
  var X=function(t){return L+(W-L-R)*(t-x0)/((x1-x0)||1)},Y=function(v){return T+(H-T-B)*(1-(v-lo)/(hi-lo))};
  var svg=el('svg',{viewBox:'0 0 '+W+' '+H,role:'img','aria-label':'PriceCharting sold prices by grade; the numbers are in the table below'});
  nice(lo,hi,W<520?4:5).forEach(function(v){svg.appendChild(el('line',{x1:L,x2:W-R,y1:Y(v),y2:Y(v),'class':'ax'}));
   svg.appendChild(el('text',{x:L-6,y:Y(v)+4,'text-anchor':'end','class':'tk'},fmt(v,sym)))});
  var nt=W<520?3:6;for(var i=0;i<=nt;i++){var t=x0+(x1-x0)*i/nt;
   svg.appendChild(el('text',{x:X(t),y:H-6,'text-anchor':i===0?'start':i===nt?'end':'middle','class':'tk'},
    new Date(t).toLocaleDateString('en-US',{month:'short',day:'numeric',year:'2-digit',timeZone:'UTC'})))}
  vis.forEach(function(g){var pts=g.pts.filter(function(p){return p[1]>0});if(!pts.length)return;
   var d=pts.map(function(p,i){return (i?'L':'M')+X(p[0]).toFixed(1)+' '+Y(p[1]).toFixed(1)}).join(' ');
   svg.appendChild(el('path',{d:d,fill:'none',stroke:g.color,'stroke-width':2,'stroke-linejoin':'round','stroke-linecap':'round'}));
   var lp=pts[pts.length-1];svg.appendChild(el('circle',{cx:X(lp[0]),cy:Y(lp[1]),r:3.2,fill:g.color}))});
  var xh=el('line',{y1:T,y2:H-B,'class':'xh',visibility:'hidden'});svg.appendChild(xh);
  plot.insertBefore(svg,tip);
  function move(cx,cy){var r=svg.getBoundingClientRect(),sx=(cx-r.left)*W/r.width;
   if(sx<L||sx>W-R){tip.hidden=true;xh.setAttribute('visibility','hidden');return}
   var t=x0+(sx-L)/(W-L-R)*((x1-x0)||1),rows=[];
   xh.setAttribute('x1',sx);xh.setAttribute('x2',sx);xh.setAttribute('visibility','visible');
   vis.forEach(function(g){var best=null,bd=Infinity;g.pts.forEach(function(p){var dd=Math.abs(p[0]-t);if(dd<bd){bd=dd;best=p}});
    if(best&&bd<8*864e5&&best[1]>0)rows.push([g,best])});
   rows.sort(function(a,b){return b[1][1]-a[1][1]});
   tip.textContent='';
   tip.appendChild(h('b',{},new Date(t).toLocaleDateString('en-US',{month:'short',day:'numeric',year:'numeric',timeZone:'UTC'})));
   rows.forEach(function(x){tip.appendChild(h('br',{}));var sp=h('b',{style:'color:'+x[0].color},fmt(x[1][1],sym)+' ');
    tip.appendChild(sp);tip.appendChild(document.createTextNode(x[0].g))});
   if(!rows.length){tip.appendChild(h('br',{}));tip.appendChild(document.createTextNode('No sales that week.'))}
   tip.hidden=false;var px=(sx/W)*r.width,left=px+14;if(left+240>r.width)left=Math.max(0,px-254);
   tip.style.left=left+'px';tip.style.top=Math.max(0,(cy-r.top)-10)+'px'}
  svg.onmousemove=function(ev){move(ev.clientX,ev.clientY)};svg.onmouseleave=function(){tip.hidden=true;xh.setAttribute('visibility','hidden')};
  svg.ontouchstart=svg.ontouchmove=function(ev){if(ev.touches[0])move(ev.touches[0].clientX,ev.touches[0].clientY)}}
 var rt=null;window.addEventListener('resize',function(){clearTimeout(rt);rt=setTimeout(draw,120)});
 draw()});
})();
"""
