"""'Price over time' on the card page, in plain words. One line by default (what it sells for, or failing that the
lowest listed price); sales, range and the price guide sit behind 'More detail'. The table below the chart has every
number and needs no script. The chart is drawn by CHART_JS from /card/<id>/history.json."""
from __future__ import annotations

import html
from datetime import date
from urllib.parse import quote

from . import charts


def e(v) -> str:
    return html.escape("" if v is None else str(v), quote=True)


def _f(v, cur):
    return "—" if v is None else charts.fmt(float(v), cur)


def _day(iso: str) -> str:
    d = date.fromisoformat(iso[:10])
    return f"{d:%b} {d.day}, {d.year}"


def chips(s: dict, cur: str) -> str:
    """One plain sentence for the shown version and condition: last sale and the trend when it is real."""
    from .history import plain_trend
    last = s["summary"].get("last")
    bits = []
    if last:
        bits.append(f'Last sold {_day(last["t"])} for {_f(last["p"], cur)}')
    t = plain_trend(s["summary"])
    if t:
        bits.append(t)
    return f'<div data-hchips><p class="hline">{e(" · ".join(bits))}</p></div>' if bits else '<div data-hchips></div>'


def table(s: dict, cur: str) -> str:
    rows = "".join(f'<tr><td>{e(date.fromisoformat(b["start"]).strftime("%b %Y"))}</td>'
                   f'<td class="n">{b["n"]}</td><td class="n">{_f(b["median"], cur)}</td>'
                   f'<td class="n">{_f(b["low"], cur)} – {_f(b["high"], cur)}</td></tr>' for b in reversed(s["month"]))
    sales = "".join(f'<tr><td>{e(_day(d["t"]))}</td><td>{e(d["site"])}</td><td>{e(d["cond"].replace("raw", "Ungraded"))}</td>'
                    f'<td class="n">{_f(d["p"], cur)}</td></tr>' for d in reversed(s["sales"]))
    asks = "".join(f'<tr><td>{e(_day(a["d"]))}</td><td class="n">{_f(a["low"], cur)}</td><td class="n">{a["n"]}</td></tr>'
                   for a in reversed(s["asks"][-30:]))
    return ('<details class="htable"><summary>See the numbers</summary>'
            + (f'<h4>By month</h4><div class="wrap"><table class="compact"><tr><th>Month</th><th class="n">Sales</th><th class="n">Typical price</th>'
               f'<th class="n">Price range</th></tr>{rows}</table></div>' if rows else "")
            + (f'<h4>Every sale</h4><div class="wrap"><table class="compact"><tr><th>Date</th><th>Where</th><th>Condition</th>'
               f'<th class="n">Price</th></tr>{sales}</table></div>' if sales else '<p class="muted">No sales of this version seen yet.</p>')
            + (f'<h4>Lowest listed price</h4><div class="wrap"><table class="compact"><tr><th>Day</th><th class="n">Lowest price</th>'
               f'<th class="n">For sale</th></tr>{asks}</table></div>' if asks else "")
            + '</details>')


def section(passcode: str, h: dict, printing_id: str | None) -> str:
    """The chart for one version (each of its grades); nothing about where data comes from."""
    from .history import first_seen
    cur = h["currency"]
    mine = [s for s in h["series"] if s["printing_id"] == printing_id]
    head = '<section id="history"><h2>Price over time</h2>'
    if not mine:
        return (head + '<p class="hnote">No sales of this version seen yet. The chart fills in as it sells.</p></section>')
    since = first_seen(mine)
    s0 = max(mine, key=lambda s: (len(s["sales"]), len(s["asks"]), s["grade"] == "Raw"))
    note = (f'<p class="hnote">CardMan started tracking this card on {e(_day(since))}. The chart fills in as it sells.</p>'
            if len(s0["sales"]) < 3 and since else "")
    return (head + f'<div data-hchart="/card/{quote(passcode)}/history.json" data-printing="{e(printing_id)}" data-default="{e(s0["key"])}" '
            f'data-cur="{e(cur)}">' + chips(s0, cur) + note
            + '<noscript><p class="muted small">Turn on scripts to see the chart; the numbers are below.</p></noscript>'
            + table(s0, cur) + '</div></section>')


CHART_CSS = """
#history .hline{font-size:15px;margin:2px 0 8px}#history .hnote{color:var(--fg2);font-size:13px;margin:4px 0 8px}
.hctl{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:0 0 8px}
.seg{display:inline-flex;border:1px solid var(--axis);border-radius:7px;overflow:hidden}.seg button{border:0;background:none;padding:4px 12px;
font-size:13px;color:var(--fg2);cursor:pointer;margin:0;border-radius:0}.seg button[aria-pressed=true]{background:var(--fg);color:var(--surface)}
.hmore{border:1px solid var(--axis);background:none;border-radius:7px;padding:4px 12px;font-size:13px;color:var(--fg2);cursor:pointer;margin:0}
.hmore[aria-pressed=true]{background:var(--fg);color:var(--surface)}
.hplot{position:relative;width:100%;touch-action:pan-y}.hplot svg{display:block;width:100%;height:auto;overflow:visible}
.hplot .ax{stroke:var(--line);stroke-width:1}.hplot .tk{fill:var(--fg2);font-size:11px}.hplot .dot{fill:var(--s1);fill-opacity:.55;stroke:var(--s1)}
.hplot .med{stroke:var(--s1);stroke-width:2.4;fill:none}.hplot .medpt{fill:var(--s1)}.hplot .band{fill:var(--s1);fill-opacity:.14}
.hplot .ask{stroke:var(--s2);stroke-width:1.6;fill:none;stroke-opacity:.75}.hplot .ask.main{stroke-width:2.4;stroke-opacity:1}
.hplot .guide{stroke:var(--s3);stroke-width:1.6;fill:none;stroke-dasharray:5 4}
.hplot .xh{stroke:var(--fg2);stroke-width:1;stroke-dasharray:2 3}.hplot .hit{fill:none;stroke:var(--fg);stroke-width:2}
.htip{position:absolute;pointer-events:none;background:var(--surface);border:1px solid var(--axis);border-radius:8px;padding:6px 9px;
font-size:12px;line-height:1.45;box-shadow:0 4px 14px rgba(0,0,0,.12);max-width:260px;z-index:3}.htip b{font-size:13px}
.hleg{display:flex;flex-wrap:wrap;gap:12px;font-size:12px;color:var(--fg2);margin:6px 0}.hleg i{display:inline-block;width:14px;height:3px;
vertical-align:middle;margin-right:5px}.hleg .l-dot{width:8px;height:8px;border-radius:50%;background:var(--s1);opacity:.7}
.hleg .l-med{background:var(--s1)}.hleg .l-band{height:9px;background:var(--s1);opacity:.18}.hleg .l-ask{background:var(--s2)}
.hleg .l-guide{background:repeating-linear-gradient(90deg,var(--s3) 0 5px,transparent 5px 9px)}
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
function nice_day(t){return new Date(t).toLocaleDateString('en-US',{month:'short',day:'numeric',year:'numeric',timeZone:'UTC'})}
function nice_month(t){return new Date(t).toLocaleDateString('en-US',{month:'long',year:'numeric',timeZone:'UTC'})}
var RANGES=[['1M',30],['3M',91],['1Y',365],['All',0]],DAY=864e5;
function nice(lo,hi,n){var span=hi-lo||Math.abs(hi)||1,step=Math.pow(10,Math.floor(Math.log10(span/n))),e=span/n/step;
 step*=e>=5?10:e>=2?5:e>=1?2:1;var t=[],v=Math.ceil(lo/step)*step;for(;v<=hi+1e-9;v+=step)t.push(v);return t}
document.querySelectorAll('[data-hchart]').forEach(function(root){
 var cur=root.getAttribute('data-cur'),pid=root.getAttribute('data-printing'),state={key:root.getAttribute('data-default'),range:'3M',more:false},data=null;
 try{state.range=localStorage.getItem('cm-h-range2')||'3M'}catch(e){}
 var ctl=h('div',{'class':'hctl'}),plot=h('div',{'class':'hplot'}),leg=h('div',{'class':'hleg'}),tip=h('div',{'class':'htip',hidden:''});
 var anchor=root.querySelector('.htable');root.insertBefore(ctl,anchor);root.insertBefore(plot,anchor);root.insertBefore(leg,anchor);plot.appendChild(tip);
 fetch(root.getAttribute('data-hchart'),{credentials:'same-origin'}).then(function(r){return r.json()}).then(function(d){
  data=d;data.series=data.series.filter(function(s){return s.printing_id===pid});controls();draw()})
  .catch(function(){plot.textContent='The chart could not load; the numbers are below.'});
 function series(){for(var i=0;i<data.series.length;i++)if(data.series[i].key===state.key)return data.series[i];return data.series[0]}
 function seg(items,on,cb,name){var s=h('span',{'class':'seg',role:'group','aria-label':name});items.forEach(function(it){
  var b=h('button',{type:'button','aria-pressed':String(it[0]===on)},it[1]);b.onclick=function(){cb(it[0]);
  s.querySelectorAll('button').forEach(function(x){x.setAttribute('aria-pressed',String(x===b))});draw()};s.appendChild(b)});return s}
 function controls(){ctl.textContent='';
  if(data.series.length>1)ctl.appendChild(seg(data.series.map(function(s){return[s.key,s.grade==='Raw'?'Ungraded':s.grade]}),state.key,
   function(v){state.key=v;refresh()},'Condition'));
  ctl.appendChild(seg(RANGES.map(function(r){return[r[0],r[0]]}),state.range,function(v){state.range=v;try{localStorage.setItem('cm-h-range2',v)}catch(e){}},'Time range'));
  var m=h('button',{type:'button','class':'hmore','aria-pressed':String(state.more)},'More detail');
  m.onclick=function(){state.more=!state.more;m.setAttribute('aria-pressed',String(state.more));draw()};ctl.appendChild(m)}
 function refresh(){var s=series();
  fetch(root.getAttribute('data-hchart').replace('.json','.html')+'?key='+encodeURIComponent(s.key),{credentials:'same-origin'})
  .then(function(r){return r.text()}).then(function(t){var doc=new DOMParser().parseFromString(t,'text/html');
   var c=doc.querySelector('[data-hchips]'),tb=doc.querySelector('.htable'),oc=root.querySelector('[data-hchips]'),old=root.querySelector('.htable');
   if(c&&oc)oc.parentNode.replaceChild(document.importNode(c,true),oc);if(tb&&old)old.parentNode.replaceChild(document.importNode(tb,true),old)})}
 function legend(items){leg.textContent='';items.forEach(function(x){var sp=h('span',{}),i=h('i',{'class':x[0]});sp.appendChild(i);
  sp.appendChild(document.createTextNode(x[1]));leg.appendChild(sp)})}
 function draw(){if(!data||!data.series.length)return;var s=series(),now=day(data.now),W=Math.max(300,plot.clientWidth||600),H=W<520?220:280,
  L=W<520?48:62,R=12,T=12,B=26,days=0;RANGES.forEach(function(r){if(r[0]===state.range)days=r[1]});
  var unit=(days&&days<=91)?'week':'month',bk=s[unit],sold=s.sales.length>=3,first=now;
  s.sales.forEach(function(p){first=Math.min(first,day(p.t))});s.asks.forEach(function(a){first=Math.min(first,day(a.d))});
  var x0=days?now-days*DAY:Math.min(first,now-30*DAY)-DAY,x1=now+DAY*0.5,inR=function(t){return t>=x0&&t<=x1};
  var showAsk=!sold||state.more,vals=[];
  if(sold)bk.forEach(function(b){if(inR(day(b.start)))vals.push(b.median)});
  if(state.more){s.sales.forEach(function(p){if(inR(day(p.t)))vals.push(p.p)});s.guide.forEach(function(g){if(inR(day(g.d)))vals.push(g.v)});
   bk.forEach(function(b){if(inR(day(b.start))&&b.p75!=null)vals.push(b.p25,b.p75)})}
  if(showAsk)s.asks.forEach(function(a){if(inR(day(a.d)))vals.push(a.low)});
  var svg=el('svg',{viewBox:'0 0 '+W+' '+H,role:'img','aria-label':'Price over time; the numbers are in the table below'});
  plot.querySelectorAll('svg,.hnone').forEach(function(n){n.remove()});
  legend((sold?[['l-med','Typical selling price']]:[]).concat(showAsk?[['l-ask','Lowest listed price']]:[])
   .concat(state.more&&s.sales.length?[['l-dot','Each sale']]:[]).concat(state.more&&sold?[['l-band','Usual price range']]:[])
   .concat(state.more&&s.guide.length?[['l-guide','Price guide']]:[]));
  if(!vals.length){plot.insertBefore(h('p',{'class':'hnone muted'},'Nothing in this time range yet. Try All.'),tip);return}
  var lo=Math.min.apply(null,vals),hi=Math.max.apply(null,vals),pad=(hi-lo)*0.1||hi*0.1||1;lo=Math.max(0,lo-pad);hi+=pad;
  var X=function(t){return L+(W-L-R)*(t-x0)/(x1-x0)},Y=function(v){return T+(H-T-B)*(1-(v-lo)/(hi-lo))};
  nice(lo,hi,W<520?4:5).forEach(function(v){svg.appendChild(el('line',{x1:L,x2:W-R,y1:Y(v),y2:Y(v),'class':'ax'}));
   svg.appendChild(el('text',{x:L-6,y:Y(v)+4,'text-anchor':'end','class':'tk'},fmt(v,cur)))});
  var nt=W<520?3:6;for(var i=0;i<=nt;i++){var t=x0+(x1-x0)*i/nt,d=new Date(t);
   svg.appendChild(el('text',{x:X(t),y:H-6,'text-anchor':i===0?'start':i===nt?'end':'middle','class':'tk'},
    d.toLocaleDateString('en-US',(x1-x0)>300*DAY?{month:'short',year:'2-digit',timeZone:'UTC'}:{month:'short',day:'numeric',timeZone:'UTC'})))}
  var width=unit==='week'?7*DAY:30.4*DAY,mid=function(b){return day(b.start)+width/2-DAY/2};
  if(state.more&&sold)bk.forEach(function(b){if(b.p25!=null&&inR(mid(b))){var a=X(day(b.start)-DAY/2),z=X(day(b.start)+width-DAY/2);
   svg.appendChild(el('rect',{x:a,width:Math.max(2,z-a-1),y:Y(b.p75),height:Math.max(1,Y(b.p25)-Y(b.p75)),'class':'band'}))}});
  function runs(list,t,v,gap){var out=[],c=[],prev=null;list.forEach(function(it){var tt=t(it);if(!inR(tt)||v(it)==null)return;
   if(prev!=null&&tt-prev>gap){out.push(c);c=[]}c.push([X(tt),Y(v(it))]);prev=tt});if(c.length)out.push(c);return out}
  function path(rs,cls){rs.forEach(function(r){if(r.length===1)svg.appendChild(el('circle',{cx:r[0][0],cy:r[0][1],r:2.8,'class':cls+'pt'}));
   else svg.appendChild(el('polyline',{points:r.map(function(p){return p[0].toFixed(1)+','+p[1].toFixed(1)}).join(' '),'class':cls}))})}
  if(state.more)path(runs(s.guide,function(g){return day(g.d)},function(g){return g.v},2.5*DAY),'guide');
  // A daily price is flat across its day: steps, never slopes invented between days.
  if(showAsk)runs(s.asks,function(a){return day(a.d)},function(a){return a.low},1.5*DAY).forEach(function(r){var hw=(X(x0+DAY)-X(x0))/2,pts=[];
   r.forEach(function(p,i){if(i)pts.push([p[0]-hw,r[i-1][1]]);pts.push([p[0]-hw,p[1]],[p[0]+hw,p[1]])});
   svg.appendChild(el('polyline',{points:pts.map(function(p){return p[0].toFixed(1)+','+p[1].toFixed(1)}).join(' '),'class':'ask'+(sold?'':' main')}))});
  if(sold)path(runs(bk,mid,function(b){return b.median},width*1.6),'med');
  if(state.more)s.sales.forEach(function(p){var t=day(p.t);if(inR(t))svg.appendChild(el('circle',{cx:X(t),cy:Y(p.p),r:3.6,'class':'dot'}))});
  var xh=el('line',{y1:T,y2:H-B,'class':'xh',visibility:'hidden'}),ring=el('circle',{r:6,'class':'hit',visibility:'hidden'});svg.appendChild(xh);svg.appendChild(ring);
  plot.insertBefore(svg,tip);
  function move(cx,cy){var r=svg.getBoundingClientRect(),sx=(cx-r.left)*W/r.width,sy=(cy-r.top)*H/r.height;
   if(sx<L||sx>W-R){hide();return}var t=x0+(sx-L)/(W-L-R)*(x1-x0),best=null,bd=1e9,lines=[];
   if(state.more)s.sales.forEach(function(p){var tt=day(p.t);if(!inR(tt))return;var dd=Math.hypot(X(tt)-sx,Y(p.p)-sy);if(dd<bd){bd=dd;best=p}});
   xh.setAttribute('x1',sx);xh.setAttribute('x2',sx);xh.setAttribute('visibility','visible');
   if(best&&bd<14){ring.setAttribute('cx',X(day(best.t)));ring.setAttribute('cy',Y(best.p));ring.setAttribute('visibility','visible');
    lines.push([fmt(best.p,cur),' sold '+nice_day(day(best.t))],['',best.site+' · '+(best.cond==='raw'?'ungraded':best.cond)])}else ring.setAttribute('visibility','hidden');
   if(sold){var b=null;bk.forEach(function(q){if(t>=day(q.start)-DAY/2&&t<day(q.start)+width-DAY/2)b=q});
    var lab=unit==='week'?'week of '+nice_day(b?day(b.start):t):nice_month(b?day(b.start):t);
    lines.push(b?[fmt(b.median,cur),' typical price, '+lab+' ('+b.n+' sale'+(b.n===1?'':'s')+')']:['','No sales in the '+lab])}
   var ds=new Date(t).toISOString().slice(0,10);if(showAsk)s.asks.forEach(function(a){if(a.d===ds)lines.push([fmt(a.low,cur),' lowest listed price on '+nice_day(t)+' ('+a.n+' for sale)'])});
   if(state.more)s.guide.forEach(function(g){if(g.d===ds)lines.push([fmt(g.v,cur),' price guide'])});
   if(!lines.length)lines.push(['',nice_day(t)]);
   while(tip.firstChild)tip.removeChild(tip.firstChild);lines.forEach(function(l,i){if(i)tip.appendChild(h('br',{}));
    if(l[0])tip.appendChild(h('b',{},l[0]));tip.appendChild(document.createTextNode(l[1]))});
   tip.hidden=false;var px=(sx/W)*r.width,left=px+14;if(left+260>r.width)left=Math.max(0,px-270);
   tip.style.left=left+'px';tip.style.top=Math.max(0,(sy/H)*r.height-10)+'px'}
  function hide(){tip.hidden=true;xh.setAttribute('visibility','hidden');ring.setAttribute('visibility','hidden')}
  svg.onmousemove=function(ev){move(ev.clientX,ev.clientY)};svg.onmouseleave=hide;
  svg.ontouchstart=svg.ontouchmove=function(ev){if(ev.touches[0])move(ev.touches[0].clientX,ev.touches[0].clientY)}}
 var rt=null;window.addEventListener('resize',function(){clearTimeout(rt);rt=setTimeout(draw,120)})})})();
"""
