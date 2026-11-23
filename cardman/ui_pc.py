"""PriceCharting on a card page: the six grades PC tracks for this exact printing, drawn to be poked at.

Everything here comes from the sweep's own capture (pc_card). PriceCharting sells in USD; every number below
is converted to the display currency once, on the server, so the chart, the chips and the table agree.

The chart draws one dot per weekly sold-price point. Hovering stops on the nearest dot and names the sale date,
the price and the move since the prior one; dots run warm when the price rose and cold when it fell. A range
switch (3/6/9/12 months) rescales the plot and the table together.
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
                    f'<td class="n py">{yr}</td><td>{e(x["vol"] or "—")}</td></tr>')
    payload = {"cur": cur, "sym": SYM.get(cur, ""), "grades": out,
               "url": row.get("url"), "title": row.get("title")}
    return ('<section id="pcsheet"><h2>Graded prices on PriceCharting</h2>'
            '<p class="hnote">Weekly sold prices for this exact printing at each grade PC tracks. Every point on the '
            'line is one week of sales - stop on a dot for the date, the price and the move since the week before. '
            'Dots run warm when the price rose, cold when it fell.</p>'
            f'<div data-pcsheet><script type="application/json" class="pcjson">{json.dumps(payload).replace("</", "<\\/")}</script>'
            '<div class="pcbar"><div class="pctabs" role="group" aria-label="Grades to draw"></div>'
            '<div class="pcrng" role="group" aria-label="Months to show"></div></div>'
            '<div class="pcplot"></div>'
            '<div class="wrap"><table class="compact pctable"><tr><th>Grade</th><th class="n">Now</th>'
            f'<th class="n prh">Change</th><th>Sold</th></tr>{"".join(rows)}</table></div>{popnote}'
            + (f'<p class="small"><a href="{e(row["url"])}" target="_blank" rel="noreferrer noopener">Open this card on '
               'PriceCharting</a></p>' if row.get("url") else "")
            + '</div></section>')


PC_CSS = """
#pcsheet .pcbar{display:flex;flex-wrap:wrap;gap:10px 16px;align-items:center;justify-content:space-between;margin:2px 0 10px}
#pcsheet .pctabs,#pcsheet .pcrng{display:flex;flex-wrap:wrap;gap:6px}
#pcsheet .pctabs button,#pcsheet .pcrng button{border:1px solid var(--axis);background:none;border-radius:999px;padding:5px 12px;
font-size:13px;color:var(--fg2);cursor:pointer;margin:0;display:inline-flex;gap:7px;align-items:center;line-height:1.1}
#pcsheet .pcrng button{font-variant-numeric:tabular-nums;padding:5px 11px}
#pcsheet .pctabs button[aria-pressed=true],#pcsheet .pcrng button[aria-pressed=true]{color:var(--fg);border-color:var(--fg2);
background:color-mix(in srgb,var(--soft) 55%,var(--surface));font-weight:700}
#pcsheet button:focus-visible{outline:2px solid var(--fg);outline-offset:2px}
#pcsheet .pctabs i{display:inline-block;width:9px;height:9px;border-radius:50%}
#pcsheet .pctabs small{color:var(--muted);font-size:11px}
#pcsheet .pcplot{position:relative;width:100%;touch-action:pan-y;margin:0 0 10px;
border:1px solid var(--line);border-radius:12px;background:color-mix(in srgb,var(--surface) 88%,var(--soft));
padding:6px 4px 2px}
#pcsheet .pcplot svg{display:block;width:100%;height:auto;overflow:visible}
#pcsheet .pcplot .ax{stroke:var(--line);stroke-width:1}
#pcsheet .pcplot .ax.zero{stroke:var(--fg2);stroke-dasharray:3 3;opacity:.5}
#pcsheet .pcplot .tk{fill:var(--fg2);font-size:11.5px;font-variant-numeric:tabular-nums}
#pcsheet .pcplot .line{fill:none;stroke-width:2.2;stroke-linejoin:round;stroke-linecap:round;opacity:.55}
#pcsheet .pcplot .dot{stroke:var(--surface);stroke-width:1.2}
#pcsheet .pcplot .hit{fill:transparent;cursor:crosshair}
#pcsheet .pcplot .dot.on{stroke:var(--fg);stroke-width:2}
#pcsheet .pcplot .tag{font-size:11.5px;font-weight:700;font-variant-numeric:tabular-nums}
#pcsheet .pcplot .xh{stroke:var(--fg2);stroke-width:1;stroke-dasharray:2 3}
#pcsheet .pcplot .htip{position:absolute;z-index:5;min-width:150px;max-width:230px;background:var(--surface);
border:1.5px solid var(--fg2);border-radius:10px;box-shadow:4px 5px 0 rgba(0,0,0,.12);padding:7px 10px;
font-size:12.5px;color:var(--fg);pointer-events:none;line-height:1.5}
#pcsheet .pcplot .htip i{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:6px}
#pcsheet .pcplot .htip b{font-variant-numeric:tabular-nums}
#pcsheet .pctable .up{color:var(--teal-deep)}#pcsheet .pctable .down{color:#c4553b}
#pcsheet .pcpop{margin:6px 0 2px}
@media (max-width:520px){#pcsheet .pctabs button small{display:none}#pcsheet .pctabs button{padding:5px 9px}}
"""


PC_JS = r"""
(function(){
var COL=['#2f7d6d','#b07c2a','#4a6fa5','#8a5a9a','#c4553b','#6b7280'];
var WARM='#d0722a',COLD='#3f6fa8',FLAT='#9a917e';
var RANGES=[3,6,9,12],DAY=864e5;
function fmt(v,sym){if(v==null)return '\u2014';var d=Math.abs(v)>=1000?0:2;
 return sym+Number(v).toLocaleString('en-US',{minimumFractionDigits:d,maximumFractionDigits:d})}
function el(n,a,t){var x=document.createElementNS('http://www.w3.org/2000/svg',n);for(var k in a)x.setAttribute(k,a[k]);if(t!=null)x.textContent=t;return x}
function h(tag,a,t){var x=document.createElement(tag);for(var k in a)x.setAttribute(k,a[k]);if(t!=null)x.textContent=t;return x}
function nice(lo,hi,n){var span=hi-lo||Math.abs(hi)||1,step=Math.pow(10,Math.floor(Math.log10(span/n))),e=span/n/step;
 step*=e>=5?10:e>=2?5:e>=1?2:1;var t=[],v=Math.ceil(lo/step)*step;for(;v<=hi+1e-9;v+=step)t.push(v);return t}
function day(t,long){var o={timeZone:'UTC',month:'short',day:'numeric'};if(long)o.year='numeric';
 return new Date(t).toLocaleDateString('en-US',o)}
function heat(d){return d>0.005?WARM:d<-0.005?COLD:FLAT}
document.querySelectorAll('[data-pcsheet]').forEach(function(root){
 var d=JSON.parse(root.querySelector('.pcjson').textContent),sym=d.sym||'$';
 var gs=d.grades.filter(function(g){return g.pts.length||g.p!=null}),on={},months=12;
 gs.forEach(function(g,i){g.color=COL[i%COL.length];
  g.pts.sort(function(a,b){return a[0]-b[0]})});
 var def=gs.filter(function(g){return g.g==='Ungraded'||g.g==='PSA 10'}),pick=def.length?def:[gs[0]];
 pick.forEach(function(g){on[g.g]=true});
 var tabs=root.querySelector('.pctabs'),rng=root.querySelector('.pcrng'),plot=root.querySelector('.pcplot');
 var tip=h('div',{'class':'htip',hidden:''});plot.appendChild(tip);
 gs.forEach(function(g){var b=h('button',{type:'button','aria-pressed':String(!!on[g.g]),
   title:(g.vol?g.vol+' recently. ':'')+'Click to draw or hide this grade.'});
  b.appendChild(h('i',{style:'background:'+g.color}));b.appendChild(document.createTextNode(g.g));
  if(g.p!=null)b.appendChild(h('small',{},fmt(g.p,sym)));
  b.onclick=function(){on[g.g]=!on[g.g];b.setAttribute('aria-pressed',String(!!on[g.g]));if(!gs.some(function(q){return on[q.g]})){on[g.g]=true;b.setAttribute('aria-pressed','true')}draw()};
  tabs.appendChild(b)});
 RANGES.forEach(function(m){var b=h('button',{type:'button','aria-pressed':String(m===months)},m+'\u202fmo');
  b.onclick=function(){months=m;setPressed();draw()};rng.appendChild(b)});
 function setPressed(){[].forEach.call(rng.children,function(b,i){b.setAttribute('aria-pressed',String(RANGES[i]===months))})}
 function cutOf(){var last=0;gs.forEach(function(g){if(on[g.g]&&g.pts.length)last=Math.max(last,g.pts[g.pts.length-1][0])});
  return last?last-months*30.44*DAY:0}
 function draw(){
  var cut=cutOf();
  var vis=gs.filter(function(g){return on[g.g]&&g.pts.length});
  vis.forEach(function(g){g.view=g.pts.filter(function(p){return p[0]>=cut&&p[1]>0})});
  vis=vis.filter(function(g){return g.view.length});
  plot.querySelectorAll('svg').forEach(function(n){n.remove()});
  setChangeTable();
  if(!vis.length){plot.insertBefore(h('p',{'class':'muted'},'No sold prices in the last '+months+' months for the grades on. Turn another grade on, or widen the range.'),tip);return}
  var narrow=(plot.clientWidth||640)<560,W=Math.max(300,plot.clientWidth||640),H=narrow?250:340,
   L=narrow?50:64,R=narrow?18:74,T=16,B=30;
  var x0=Infinity,x1=-Infinity,lo=Infinity,hi=-Infinity;
  vis.forEach(function(g){g.view.forEach(function(p){x0=Math.min(x0,p[0]);x1=Math.max(x1,p[0]);lo=Math.min(lo,p[1]);hi=Math.max(hi,p[1])})});
  if(x1-x0<7*DAY)x0=x1-7*DAY;
  if(!(hi>lo)){hi=lo+1;lo=Math.max(0,lo-.2)}
  var pad=(hi-lo)*.1||1;lo=Math.max(0,lo-pad);hi+=pad;
  var X=function(t){return L+(W-L-R)*(t-x0)/((x1-x0)||1)},Y=function(v){return T+(H-T-B)*(1-(v-lo)/(hi-lo))};
  var svg=el('svg',{viewBox:'0 0 '+W+' '+H,role:'img',
   'aria-label':'PriceCharting weekly sold prices by grade, last '+months+' months; every dot is one week, the table below carries the numbers'});
  nice(lo,hi,narrow?4:6).forEach(function(v){
   svg.appendChild(el('line',{x1:L,x2:W-(narrow?R-8:R)+8,y1:Y(v),y2:Y(v),'class':'ax'}));
   svg.appendChild(el('text',{x:L-7,y:Y(v)+4,'text-anchor':'end','class':'tk'},fmt(v,sym)))});
  var nt=narrow?3:5;for(var i=0;i<=nt;i++){var t=x0+(x1-x0)*i/nt;
   svg.appendChild(el('text',{x:X(t),y:H-8,'text-anchor':i===0?'start':i===nt?'end':'middle','class':'tk'},day(t)))}
  var dots=[];
  vis.forEach(function(g){
   var pts=g.view,dd=pts.map(function(p,i){return (i?'L':'M')+X(p[0]).toFixed(1)+' '+Y(p[1]).toFixed(1)}).join(' ');
   svg.appendChild(el('path',{d:dd,'class':'line',stroke:g.color}));
   pts.forEach(function(p,i){var prev=i?pts[i-1][1]:p[1],mv=prev?((p[1]-prev)/prev)*100:0;
    var c=el('circle',{cx:X(p[0]).toFixed(1),cy:Y(p[1]).toFixed(1),r:i===pts.length-1?4:3,'class':'dot',fill:heat(mv)});
    svg.appendChild(c);dots.push({g:g,x:X(p[0]),y:Y(p[1]),t:p[0],v:p[1],prev:prev,mv:mv,c:c})})});
  var order=vis.slice().sort(function(a,b){return Y(a.view[a.view.length-1][1])-Y(b.view[b.view.length-1][1])}),ly=-99;
  order.forEach(function(g){var lp=g.view[g.view.length-1],y=Math.max(Y(lp[1]),ly+14);ly=y;
   svg.appendChild(el('text',{x:W-R+10,y:y+4,'class':'tag',fill:g.color},fmt(lp[1],sym)))});
  var xh=el('line',{y1:T,y2:H-B,'class':'xh',visibility:'hidden'});svg.appendChild(xh);
  var hot=null;
  plot.insertBefore(svg,tip);
  function stop(dt){if(hot){hot.c.classList.remove('on');hot=null}
   if(!dt){tip.hidden=true;xh.setAttribute('visibility','hidden');return}
   hot=dt;dt.c.classList.add('on');xh.setAttribute('x1',dt.x);xh.setAttribute('x2',dt.x);xh.setAttribute('visibility','visible');
   tip.textContent='';
   tip.appendChild(h('b',{},day(dt.t,true)));
   var row=h('div',{});var sw=h('i',{style:'background:'+dt.g.color});row.appendChild(sw);
   row.appendChild(h('b',{style:'color:'+dt.g.color},fmt(dt.v,sym)));row.appendChild(document.createTextNode(' '+dt.g.g));
   tip.appendChild(row);
   var mv=h('div',{style:'color:'+(dt.mv>0.5?WARM:dt.mv<-0.5?COLD:'var(--muted)')},
    (dt.mv>0?'+':'')+dt.mv.toFixed(1)+'% vs the week before');
   tip.appendChild(mv);
   var r=svg.getBoundingClientRect(),px=(dt.x/W)*r.width,left=px+16;
   if(left+230>r.width)left=Math.max(0,px-246);
   tip.style.left=left+'px';tip.style.top=Math.max(0,(dt.y/H)*r.height-14)+'px';tip.hidden=false}
  function move(cx,cy){var r=svg.getBoundingClientRect(),sx=(cx-r.left)*W/r.width,syv=(cy-r.top)*H/r.height;
   if(sx<L||sx>W-R){stop(null);return}
   var best=null,bd=Infinity;
   dots.forEach(function(dt){var dx=Math.abs(dt.x-sx),dy=Math.abs(dt.y-syv),dist=dx+dy*1.6;
    if(dx<30&&dist<bd){bd=dist;best=dt}});
   if(best&&bd<46)stop(best);else stop(null)}
  svg.onmousemove=function(ev){move(ev.clientX,ev.clientY)};
  svg.onmouseleave=function(){stop(null)};
  svg.ontouchstart=svg.ontouchmove=function(ev){if(ev.touches[0])move(ev.touches[0].clientX,ev.touches[0].clientY)};
  svg.ontouchend=function(){stop(null)}}
 function setChangeTable(){
  var cut=cutOf();
  [].forEach.call(root.querySelectorAll('.pctable tr[data-g]'),function(tr){
   var g=null,key=tr.getAttribute('data-g');
   gs.forEach(function(q){if(q.g===key)g=q});
   var td=tr.querySelector('td.py');if(!td||!g)return;
   var v=g.pts.filter(function(p){return p[0]>=cut&&p[1]>0});
   if(v.length<2||!v[0][1]){td.textContent='\u2014';return}
   var ch=(v[v.length-1][1]-v[0][1])/v[0][1]*100;
   td.textContent=(ch>0?'+':'')+ch.toFixed(0)+'%';td.className='n py'+(ch>0?' up':ch<-0?' down':'')});
  var th=root.querySelector('.prh');if(th)th.textContent='Change \u00b7 '+months+' mo'}
 var rt=null;window.addEventListener('resize',function(){clearTimeout(rt);rt=setTimeout(draw,120)});
 draw()});
})();
"""
