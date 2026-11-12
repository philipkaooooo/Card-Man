"""Price history section of the card page: summary chips and a data table rendered on the server (works with no
script), and an interactive chart drawn by CHART_JS from /card/<passcode>/history.json. One component everywhere."""
from __future__ import annotations

import html
from datetime import datetime
from urllib.parse import quote

from . import charts


def e(v) -> str:
    return html.escape("" if v is None else str(v), quote=True)


def _f(v, cur):
    return "—" if v is None else charts.fmt(float(v), cur)


def chips(s: dict, cur: str) -> str:
    sm = s["summary"]
    last = sm.get("last")
    ch = sm["change_90"]
    tr = sm["trend"]
    items = [
        ("Last sale", f'{_f(last["p"], cur)} <span class="muted">{e(last["t"][:10])} · {e(last["site"])}</span>' if last else "none yet"),
        ("30-day median", f'{_f(sm["median_30"], cur)} <span class="muted">n={sm["n_30"]}</span>' if sm["median_30"] is not None
         else f'not enough sales <span class="muted">({sm["n_30"]} in 30 days, need 3)</span>'),
        ("90-day change", (f'<b class="{"good" if ch["pct"] >= 0 else "bad"}">{ch["pct"]:+.1f}%</b>' if ch["pct"] is not None else "not enough sales")
         + f'<span class="muted tiny" title="{e(ch["why"])}"> ⓘ</span>'),
        ("Trend", f'{e(tr["label"])}<span class="muted tiny" title="{e(tr["why"])}"> ⓘ</span>'),
    ]
    return '<div class="hchips">' + "".join(f'<div class="hchip"><span>{e(k)}</span><b>{v}</b></div>' for k, v in items) + '</div>'


def table(s: dict, cur: str) -> str:
    rows = "".join(f'<tr><td>{e(b["start"])}</td><td class="n">{b["n"]}</td><td class="n">{_f(b["median"], cur)}</td>'
                   f'<td class="n">{_f(b["p25"], cur) + " – " + _f(b["p75"], cur) if b["p25"] is not None else "—"}</td>'
                   f'<td class="n">{_f(b["low"], cur)} – {_f(b["high"], cur)}</td></tr>' for b in reversed(s["month"]))
    sales = "".join(f'<tr><td>{e(d["t"][:10])}</td><td>{e(d["site"])}</td><td>{e(d["cond"])}</td><td class="n">{_f(d["p"], cur)}'
                    f'{" <span class=muted>(" + e(d["orig"]) + ")</span>" if d.get("orig") else ""}</td></tr>' for d in reversed(s["sales"]))
    return ('<details class="htable"><summary>Data table</summary><div data-htable>'
            + (f'<h4>By month</h4><div class="wrap"><table class="compact"><tr><th>Month</th><th class="n">Sales</th><th class="n">Median</th>'
               f'<th class="n">P25 – P75 (3+ sales)</th><th class="n">Low – high</th></tr>{rows}</table></div>' if rows else "")
            + (f'<h4>Every sale</h4><div class="wrap"><table class="compact"><tr><th>Date</th><th>Site</th><th>Condition</th>'
               f'<th class="n">Price</th></tr>{sales}</table></div>' if sales else '<p class="muted">No dated sales in this series.</p>')
            + '</div></details>')


def section(passcode: str, h: dict, backfill: list[tuple[str, str]]) -> str:
    cur = h["currency"]
    depth = h["depth"]
    dl = (" · ".join(f"{e(k)} since {e(v)}" for k, v in sorted(depth.items(), key=lambda kv: kv[1])) if depth else "no dated sales yet")
    links = " · ".join(f'<a href="{e(u)}" target="_blank" rel="noreferrer noopener">{e(t)}</a>' for t, u in backfill)
    fill = (f'<div class="backfill"><b>Backfill 3 months:</b> {links} <span class="muted small">Open each, scroll to the end of the '
            'results, then click your "Send to CardMan" bookmark. eBay sold and 130point go back about 90 days, Yahoo! 落札相場 about '
            '120 days; Mercari shows no sale dates, so its sales are counted but not charted.</span></div>' if links else "")
    head = (f'<section id="history"><h2>Price history</h2><p class="why small">Data goes back: {dl}'
            + (f' · {h["undated"]} sale(s) without a sale date are counted, not charted' if h["undated"] else "")
            + (f' · {h["printing_open"]} sale(s) not charted until their printing is confirmed (see Recently sold)' if h.get("printing_open") else "")
            + '. Each printing and grade is its own series; sales are converted once, at their own date\'s rate.</p>')
    if not h["series"]:
        why = (f'{h["printing_open"]} real sale(s) are recorded, but none is confirmed to one printing yet, and prices are never '
               'pooled across printings.' if h.get("printing_open") else 'No real sale is recorded yet.')
        return (head + f'<div class="hempty"><p><b>No price history for this card yet.</b> {e(why)} No live listing is certain enough '
                'of its printing to track its asks either. Backfill below, or confirm the printing of a recorded sale.</p></div>' + fill + '</section>')
    s0 = next(s for s in h["series"] if s["key"] == h["default"])
    return (head + f'<div data-hchart="/card/{quote(passcode)}/history.json" data-default="{e(h["default"])}" data-cur="{e(cur)}">'
            + f'<div data-hchips>{chips(s0, cur)}</div><noscript><p class="muted small">Turn on scripts for the interactive chart; '
            'the table below has the same numbers.</p></noscript>'
            + f'<p class="muted small" data-hseries>{e(s0["label"])} · {e(s0["grade"])}</p>' + table(s0, cur) + '</div>' + fill + '</section>')


CHART_CSS = """
#history .hchips{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:8px;margin:6px 0 10px}
.hchip{border:1px solid var(--line);border-radius:10px;padding:8px 10px;background:var(--surface)}.hchip span{display:block;font-size:11px;color:var(--fg2)}
.hchip b{font-size:15px}.hchip .muted{font-weight:400;font-size:11px}.tiny{font-size:11px;cursor:help}
.hctl{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:0 0 8px}.hctl select{max-width:min(460px,100%)}
.seg{display:inline-flex;border:1px solid var(--axis);border-radius:7px;overflow:hidden}.seg button{border:0;background:none;padding:3px 10px;
font-size:12px;color:var(--fg2);cursor:pointer;margin:0;border-radius:0}.seg button[aria-pressed=true]{background:var(--fg);color:var(--surface)}
.hplot{position:relative;width:100%;touch-action:pan-y}.hplot svg{display:block;width:100%;height:auto;overflow:visible}
.hplot .ax{stroke:var(--line);stroke-width:1}.hplot .tk{fill:var(--fg2);font-size:11px}.hplot .dot{fill:var(--s1);fill-opacity:.55;stroke:var(--s1)}
.hplot .med{stroke:var(--s1);stroke-width:2.2;fill:none}.hplot .medpt{fill:var(--s1)}.hplot .band{fill:var(--s1);fill-opacity:.14}
.hplot .ask{stroke:var(--s2);stroke-width:1.6;fill:none;stroke-opacity:.7}.hplot .guide{stroke:var(--s3);stroke-width:1.6;fill:none;stroke-dasharray:5 4}
.hplot .xh{stroke:var(--fg2);stroke-width:1;stroke-dasharray:2 3}.hplot .hit{fill:none;stroke:var(--fg);stroke-width:2}
.htip{position:absolute;pointer-events:none;background:var(--surface);border:1px solid var(--axis);border-radius:8px;padding:6px 9px;
font-size:12px;line-height:1.45;box-shadow:0 4px 14px rgba(0,0,0,.12);max-width:240px;z-index:3}.htip b{font-size:13px}
.hleg{display:flex;flex-wrap:wrap;gap:12px;font-size:12px;color:var(--fg2);margin:6px 0}.hleg i{display:inline-block;width:14px;height:3px;
vertical-align:middle;margin-right:5px}.hleg .l-dot{width:8px;height:8px;border-radius:50%;background:var(--s1);opacity:.7}
.hleg .l-med{background:var(--s1)}.hleg .l-band{height:9px;background:var(--s1);opacity:.18}.hleg .l-ask{background:var(--s2);opacity:.7}
.hleg .l-guide{background:repeating-linear-gradient(90deg,var(--s3) 0 5px,transparent 5px 9px)}
.hempty{border:1px dashed var(--axis);border-radius:10px;padding:14px;margin:6px 0}.backfill{margin:10px 0 0;font-size:13px;line-height:1.6}
.htable{margin:8px 0}.htable h4{margin:10px 0 4px;font-size:13px}.spark{vertical-align:middle;fill:none;stroke:var(--s1);stroke-width:1.5}
.spark circle{fill:var(--s1);stroke:none}
"""


CHART_JS = r"""
(function(){
var SYM={USD:'$',TWD:'NT$',JPY:'¥',EUR:'€',GBP:'£'};
function fmt(v,c){if(v==null)return '—';var s=SYM[c]||'',d=(c==='JPY'||c==='TWD'||Math.abs(v)>=1000)?0:2;
 return s+Number(v).toLocaleString('en-US',{minimumFractionDigits:d,maximumFractionDigits:d})+(s?'':' '+c)}
function el(n,a,t){var x=document.createElementNS('http://www.w3.org/2000/svg',n);for(var k in a)x.setAttribute(k,a[k]);if(t)x.textContent=t;return x}
function h(tag,a,t){var x=document.createElement(tag);for(var k in a)x.setAttribute(k,a[k]);if(t!=null)x.textContent=t;return x}
function day(s){return Date.parse(s.length<=10?s+'T12:00:00Z':s)}
var RANGES=[['1W',7],['1M',30],['3M',91],['1Y',365],['All',0]],DAY=864e5;
function nice(lo,hi,n){var span=hi-lo||Math.abs(hi)||1,step=Math.pow(10,Math.floor(Math.log10(span/n))),e=span/n/step;
 step*=e>=5?10:e>=2?5:e>=1?2:1;var t=[],v=Math.ceil(lo/step)*step;for(;v<=hi+1e-9;v+=step)t.push(v);return t}
document.querySelectorAll('[data-hchart]').forEach(function(root){
 var cur=root.getAttribute('data-cur'),state={key:root.getAttribute('data-default'),range:'3M',unit:'week'},data=null;
 var saved=null;try{saved=localStorage.getItem('cm-h-range');state.unit=localStorage.getItem('cm-h-unit')||'week'}catch(e){}
 function fit(){if(saved)return saved;var s=series(),now=day(data.now),first=now;s.sales.forEach(function(p){first=Math.min(first,day(p.t))});
  s.asks.forEach(function(a){first=Math.min(first,day(a.d))});var span=(now-first)/DAY;
  for(var i=0;i<RANGES.length-1;i++)if(span<=RANGES[i][1]*0.95)return RANGES[i][0];return 'All'}
 var ctl=h('div',{'class':'hctl'}),plot=h('div',{'class':'hplot'}),leg=h('div',{'class':'hleg'}),tip=h('div',{'class':'htip',hidden:''});
 var chipsBox=root.querySelector('[data-hchips]'),label=root.querySelector('[data-hseries]');
 root.insertBefore(ctl,label);root.insertBefore(plot,label);root.insertBefore(leg,label);plot.appendChild(tip);
 [['l-dot','Each sale'],['l-med','Median per bucket'],['l-band','P25–P75 (3+ sales)'],['l-ask','Lowest ask per day (asking, not sold)'],
  ['l-guide','Guide estimate (not a sale)']].forEach(function(x){var sp=h('span',{}),i=h('i',{'class':x[0]});sp.appendChild(i);
  sp.appendChild(document.createTextNode(x[1]));leg.appendChild(sp)});
 fetch(root.getAttribute('data-hchart')+'?c='+encodeURIComponent(cur),{credentials:'same-origin'}).then(function(r){return r.json()}).then(function(d){data=d;state.range=fit();controls();draw()})
  .catch(function(){plot.textContent='Could not load the chart; the table below has the numbers.'});
 function series(){for(var i=0;i<data.series.length;i++)if(data.series[i].key===state.key)return data.series[i];return data.series[0]}
 function seg(items,on,cb,name){var s=h('span',{'class':'seg',role:'group','aria-label':name});items.forEach(function(it){
  var b=h('button',{type:'button','aria-pressed':String(it[0]===on)},it[1]);b.onclick=function(){cb(it[0]);
  s.querySelectorAll('button').forEach(function(x){x.setAttribute('aria-pressed',String(x===b))});draw()};s.appendChild(b)});return s}
 function controls(){ctl.textContent='';
  if(data.series.length>1){var sel=h('select',{'aria-label':'Printing and grade'});data.series.forEach(function(s){
   var o=h('option',{value:s.key},s.label+' · '+s.grade+' · '+s.summary.n+' sale'+(s.summary.n===1?'':'s'));if(s.key===state.key)o.selected=true;sel.appendChild(o)});
   sel.onchange=function(){state.key=sel.value;draw();refresh()};ctl.appendChild(sel)}
  ctl.appendChild(seg(RANGES.map(function(r){return[r[0],r[0]]}),state.range,function(v){state.range=v;saved=v;try{localStorage.setItem('cm-h-range',v)}catch(e){}},'Time range'));
  ctl.appendChild(seg([['week','Weekly'],['month','Monthly']],state.unit,function(v){state.unit=v;try{localStorage.setItem('cm-h-unit',v)}catch(e){}},'Bucket'))}
 function refresh(){var s=series();label.textContent=s.label+' · '+s.grade;
  fetch(root.getAttribute('data-hchart').replace('.json','.html')+'?key='+encodeURIComponent(s.key)+'&c='+encodeURIComponent(cur),{credentials:'same-origin'})
  .then(function(r){return r.text()}).then(function(t){var doc=new DOMParser().parseFromString(t,'text/html');
   var c=doc.querySelector('[data-hchips]'),tb=doc.querySelector('.htable');if(c){chipsBox.textContent='';
   Array.prototype.slice.call(c.childNodes).forEach(function(n){chipsBox.appendChild(document.importNode(n,true))})}
   var old=root.querySelector('.htable');if(tb&&old)old.parentNode.replaceChild(document.importNode(tb,true),old)})}
 function draw(){if(!data)return;var s=series(),now=day(data.now),W=Math.max(300,plot.clientWidth||600),H=W<520?220:280,
  L=W<520?44:58,R=12,T=12,B=26,days=0;RANGES.forEach(function(r){if(r[0]===state.range)days=r[1]});
  var bk=s[state.unit],first=now;s.sales.forEach(function(p){first=Math.min(first,day(p.t))});s.asks.forEach(function(a){first=Math.min(first,day(a.d))});
  s.guide.forEach(function(g){first=Math.min(first,day(g.d))});
  var x0=days?now-days*DAY:Math.min(first,now-7*DAY)-DAY,x1=now+DAY*0.5,inR=function(t){return t>=x0&&t<=x1};
  var vals=[];s.sales.forEach(function(p){if(inR(day(p.t)))vals.push(p.p)});s.asks.forEach(function(a){if(inR(day(a.d)))vals.push(a.low)});
  s.guide.forEach(function(g){if(inR(day(g.d)))vals.push(g.v)});bk.forEach(function(b){if(inR(day(b.start))&&b.p75!=null)vals.push(b.p25,b.p75)});
  var svg=el('svg',{viewBox:'0 0 '+W+' '+H,role:'img','aria-label':'Price history for '+s.label+', '+s.grade+': '+s.summary.n+' sales; the data table below has every number'});
  plot.querySelectorAll('svg,.hnone').forEach(function(n){n.remove()});
  if(!vals.length){var p=h('p',{'class':'hnone muted'},'No sales, asks or guide prices in this range. Try a longer range or backfill.');plot.insertBefore(p,tip);return}
  var lo=Math.min.apply(null,vals),hi=Math.max.apply(null,vals),pad=(hi-lo)*0.1||hi*0.1||1;lo=Math.max(0,lo-pad);hi+=pad;
  var X=function(t){return L+(W-L-R)*(t-x0)/(x1-x0)},Y=function(v){return T+(H-T-B)*(1-(v-lo)/(hi-lo))};
  nice(lo,hi,W<520?4:5).forEach(function(v){svg.appendChild(el('line',{x1:L,x2:W-R,y1:Y(v),y2:Y(v),'class':'ax'}));
   svg.appendChild(el('text',{x:L-6,y:Y(v)+4,'text-anchor':'end','class':'tk'},fmt(v,cur)))});
  var nt=W<520?3:6;for(var i=0;i<=nt;i++){var t=x0+(x1-x0)*i/nt,d=new Date(t);
   svg.appendChild(el('text',{x:X(t),y:H-6,'text-anchor':i===0?'start':i===nt?'end':'middle','class':'tk'},
    d.toLocaleDateString('en-US',(x1-x0)>300*DAY?{month:'short',year:'2-digit',timeZone:'UTC'}:{month:'short',day:'numeric',timeZone:'UTC'})))}
  var width=state.unit==='week'?7*DAY:30.4*DAY,mid=function(b){return day(b.start)+width/2-DAY/2};
  bk.forEach(function(b){if(b.p25!=null&&inR(mid(b))){var a=X(day(b.start)-DAY/2),z=X(day(b.start)+width-DAY/2);
   svg.appendChild(el('rect',{x:a,width:Math.max(2,z-a-1),y:Y(b.p75),height:Math.max(1,Y(b.p25)-Y(b.p75)),'class':'band'}))}});
  function runs(list,t,v,gap){var out=[],cur_=[],prev=null;list.forEach(function(it){var tt=t(it);if(!inR(tt)||v(it)==null)return;
   if(prev!=null&&tt-prev>gap){out.push(cur_);cur_=[]}cur_.push([X(tt),Y(v(it))]);prev=tt});if(cur_.length)out.push(cur_);return out}
  function path(rs,cls){rs.forEach(function(r){if(r.length===1)svg.appendChild(el('circle',{cx:r[0][0],cy:r[0][1],r:2.5,'class':cls+'pt'}));
   else svg.appendChild(el('polyline',{points:r.map(function(p){return p[0].toFixed(1)+','+p[1].toFixed(1)}).join(' '),'class':cls}))})}
  path(runs(s.guide,function(g){return day(g.d)},function(g){return g.v},2.5*DAY),'guide');
  // A daily value is flat across its day: steps, not slopes between days.
  runs(s.asks,function(a){return day(a.d)},function(a){return a.low},1.5*DAY).forEach(function(r){var hw=(X(x0+DAY)-X(x0))/2,pts=[];
   r.forEach(function(p,i){if(i)pts.push([p[0]-hw,r[i-1][1]]);pts.push([p[0]-hw,p[1]],[p[0]+hw,p[1]])});
   svg.appendChild(el('polyline',{points:pts.map(function(p){return p[0].toFixed(1)+','+p[1].toFixed(1)}).join(' '),'class':'ask'}))});
  path(runs(bk,mid,function(b){return b.median},width*1.6),'med');
  s.sales.forEach(function(p){var t=day(p.t);if(inR(t))svg.appendChild(el('circle',{cx:X(t),cy:Y(p.p),r:3.6,'class':'dot'}))});
  var xh=el('line',{y1:T,y2:H-B,'class':'xh',visibility:'hidden'}),ring=el('circle',{r:6,'class':'hit',visibility:'hidden'});svg.appendChild(xh);svg.appendChild(ring);
  plot.insertBefore(svg,tip);
  function move(cx,cy){var r=svg.getBoundingClientRect(),sx=(cx-r.left)*W/r.width,sy=(cy-r.top)*H/r.height;
   if(sx<L||sx>W-R){hide();return}var t=x0+(sx-L)/(W-L-R)*(x1-x0),best=null,bd=1e9;
   s.sales.forEach(function(p){var tt=day(p.t);if(!inR(tt))return;var dd=Math.hypot(X(tt)-sx,Y(p.p)-sy);if(dd<bd){bd=dd;best=p}});
   var lines=[];xh.setAttribute('x1',sx);xh.setAttribute('x2',sx);xh.setAttribute('visibility','visible');
   if(best&&bd<14){ring.setAttribute('cx',X(day(best.t)));ring.setAttribute('cy',Y(best.p));ring.setAttribute('visibility','visible');
    lines.push([fmt(best.p,cur),' sold '+best.t.slice(0,10)],['',best.site+' · '+best.cond+(best.orig?' · '+best.orig:'')])}else ring.setAttribute('visibility','hidden');
   var b=null;bk.forEach(function(q){if(t>=day(q.start)-DAY/2&&t<day(q.start)+width-DAY/2)b=q});
   lines.push(['',(state.unit==='week'?'Week of ':'Month of ')+(b?b.start:new Date(t).toISOString().slice(0,10))+': '+
    (b?b.n+' sale'+(b.n===1?'':'s')+', median '+fmt(b.median,cur)+(b.p25!=null?', P25–P75 '+fmt(b.p25,cur)+' – '+fmt(b.p75,cur):''):'no sales')]);
   var ds=new Date(t).toISOString().slice(0,10);s.asks.forEach(function(a){if(a.d===ds)lines.push(['','Lowest ask '+fmt(a.low,cur)+' ('+a.n+' listed) · asking, not sold'])});
   s.guide.forEach(function(g){if(g.d===ds)lines.push(['','Guide estimate '+fmt(g.v,cur)])});
   while(tip.firstChild)tip.removeChild(tip.firstChild);lines.forEach(function(l,i){if(i)tip.appendChild(h('br',{}));
    if(l[0])tip.appendChild(h('b',{},l[0]));tip.appendChild(document.createTextNode(l[1]))});tip.hidden=false;var px=(sx/W)*r.width,left=px+14;if(left+240>r.width)left=Math.max(0,px-250);
   tip.style.left=left+'px';tip.style.top=Math.max(0,(sy/H)*r.height-10)+'px'}
  function hide(){tip.hidden=true;xh.setAttribute('visibility','hidden');ring.setAttribute('visibility','hidden')}
  svg.onmousemove=function(ev){move(ev.clientX,ev.clientY)};svg.onmouseleave=hide;
  svg.ontouchstart=svg.ontouchmove=function(ev){if(ev.touches[0])move(ev.touches[0].clientX,ev.touches[0].clientY)}}
 var rt=null;window.addEventListener('resize',function(){clearTimeout(rt);rt=setTimeout(draw,120)})})})();
"""
