"""The bell: under-typical asks as they land. A poll against /alerts.json, a badge, a dropdown, and a toast the
moment a new signal appears while the app is open. Desktop notifications only after an explicit click."""
from __future__ import annotations

BELL = ('<div class="awrap"><button class="abell" id="abell" type="button" aria-haspopup="true" aria-expanded="false" '
        'aria-controls="apop" title="Under-typical asks as they land">'
        '<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">'
        '<path fill="currentColor" d="M12 3a6 6 0 0 0-6 6v3.2l-1.6 3.1A1 1 0 0 0 5.3 17h13.4a1 1 0 0 0 .9-1.7L18 12.2V9a6 6 0 0 0-6-6Zm0 18a2.6 2.6 0 0 0 2.5-2h-5A2.6 2.6 0 0 0 12 21Z"/>'
        '</svg><b class="abn" id="abn" hidden></b></button>'
        '<div class="apop" id="apop" hidden></div></div>')

CSS = """
.awrap{position:relative;display:flex;align-items:center}
.abell{display:inline-flex;align-items:center;justify-content:center;width:34px;height:34px;border:1.5px solid var(--line);
border-radius:10px;background:var(--surface);color:var(--fg2);cursor:pointer;margin:0;padding:0}
.abell:hover{color:var(--fg);border-color:var(--fg2)}
.abell[aria-expanded=true]{color:var(--fg);border-color:var(--fg)}
.abn{position:absolute;top:-5px;right:-6px;min-width:17px;height:17px;padding:0 4px;border-radius:999px;background:#c4553b;
color:#fff;font-size:10.5px;font-weight:800;line-height:17px;text-align:center;font-variant-numeric:tabular-nums}
.abell{position:relative}
.apop{position:absolute;right:0;top:calc(100% + 9px);width:min(360px,88vw);max-height:min(430px,70vh);overflow:auto;
background:var(--surface);border:1.5px solid var(--fg2);border-radius:12px 10px 13px 9px/10px 13px 9px 12px;
box-shadow:6px 8px 0 rgba(0,0,0,.12);z-index:45;padding:6px}
.apop .ahead{padding:7px 10px 6px;font-size:10.5px;letter-spacing:.14em;text-transform:uppercase;color:var(--muted)}
.ait{display:flex;flex-direction:column;gap:1px;padding:8px 10px;border-radius:8px;text-decoration:none;color:var(--fg)}
.ait:hover{background:color-mix(in srgb,var(--soft) 60%,var(--surface))}
.ait b{font-size:13px;line-height:1.25}
.ait .av{font-size:12px;color:var(--teal-deep);font-variant-numeric:tabular-nums}
.ait small{font-size:11px;color:var(--muted)}
.aempty{padding:10px;font-size:12.5px;color:var(--muted);margin:0}
.afoot{display:flex;justify-content:space-between;gap:8px;align-items:center;padding:7px 8px 3px;border-top:1px solid var(--line);margin-top:4px}
.afoot a,.afoot button{font-size:12px;color:var(--teal-deep);background:none;border:0;padding:0;cursor:pointer;text-decoration:none;font-weight:700}
.afoot a:hover,.afoot button:hover{text-decoration:underline}
.atoasts{position:fixed;right:16px;bottom:16px;z-index:80;display:flex;flex-direction:column;gap:8px;max-width:min(340px,86vw)}
.atoast{background:var(--surface);border:1.5px solid var(--fg2);border-left:5px solid #c4553b;
border-radius:11px 9px 12px 8px/9px 12px 8px 11px;box-shadow:5px 7px 0 rgba(0,0,0,.13);padding:10px 12px;
font-size:13px;color:var(--fg);text-decoration:none;display:block;animation:ain .22s ease-out}
.atoast b{display:block;font-size:13.5px;margin-bottom:2px}
.atoast span{color:var(--teal-deep);font-variant-numeric:tabular-nums}
@keyframes ain{from{transform:translateY(8px);opacity:0}to{transform:none;opacity:1}}
.awrap .abell:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
@media (max-width:640px){.apop{right:-70px}}
"""

JS = r"""
(function(){
var b=document.getElementById('abell'),pop=document.getElementById('apop'),n=document.getElementById('abn');
if(!b||!pop)return;
var SYM={USD:'$',TWD:'NT$',JPY:'\u00a5',EUR:'\u20ac',GBP:'\u00a3'},SEEN='cm_alerts_seen',first=true,cur=[];
function known(){try{return JSON.parse(localStorage.getItem(SEEN)||'[]')}catch(e){return []}}
function see(ids){try{localStorage.setItem(SEEN,JSON.stringify(known().concat(ids).slice(-600)))}catch(e){}}
function h(tag,cls,txt){var x=document.createElement(tag);if(cls)x.className=cls;if(txt!=null)x.textContent=txt;return x}
function money(v,c){if(v==null)return '\u2014';var d=Math.abs(v)>=1000?0:2;return (SYM[c]||'')+Number(v).toLocaleString('en-US',{minimumFractionDigits:d,maximumFractionDigits:d})}
function ago(iso){var t=Date.parse(iso);if(isNaN(t))return '';var s=(Date.now()-t)/1000;
 if(s<90)return 'just now';if(s<3600)return Math.round(s/60)+' min ago';if(s<86400)return Math.round(s/3600)+' h ago';return Math.round(s/86400)+' d ago'}
function tail(){var c=document.getElementById('atoasts');if(!c){c=document.createElement('div');c.id='atoasts';c.className='atoasts';document.body.appendChild(c)}return c}
var flash=null,base=document.title;
function unread(items){var k=known();return items.filter(function(i){return k.indexOf(i.id)<0})}
function item(it){
 var a=h('a','ait');a.href=it.href||'/market#under';
 a.appendChild(h('b',null,it.label));
 a.appendChild(h('span','av',money(it.ask,it.cur)+' \u00b7 '+Math.round(it.pct||0)+'% under '+money(it.typical,it.cur)));
 a.appendChild(h('small',null,ago(it.at)));
 a.onclick=function(){see([it.id]);setTimeout(function(){paint(cur)},0)};
 return a}
function paint(items){
 cur=items;var u=unread(items);n.hidden=!u.length;n.textContent=u.length>9?'9+':u.length;
 if(!document.hidden){document.title=base;clearInterval(flash);flash=null}
 pop.textContent='';
 if(!items.length){pop.appendChild(h('p','aempty','Nothing under typical yet. It fills the moment an ask lands below the typical sale price.'));return}
 pop.appendChild(h('div','ahead','Under typical \u00b7 newest first'));
 items.slice(0,12).forEach(function(it){pop.appendChild(item(it))});
 var f=h('div','afoot');
 var m=h('a',null,'Open the market \u2192');m.href='/market#under';f.appendChild(m);
 if(window.Notification){
  if(Notification.permission==='default'){var q=h('button',null,'Desktop alerts');q.type='button';
   q.onclick=function(){Notification.requestPermission().then(function(){paint(cur)})};f.appendChild(q)}
  else if(Notification.permission==='granted'){var on=h('button',null,'Desktop alerts on');on.type='button';on.disabled=true;
   on.style.opacity='.6';on.style.cursor='default';f.appendChild(on)}
 }
 pop.appendChild(f)}
function toast(it){
 var t=h('a','atoast');t.href=it.href||'/market#under';
 t.appendChild(h('b',null,it.label));
 t.appendChild(h('span',null,money(it.ask,it.cur)+' \u00b7 '+Math.round(it.pct||0)+'% under typical'));
 t.onclick=function(){see([it.id])};
 tail().appendChild(t);setTimeout(function(){t.remove()},15000)}
function alert_(it){
 if(window.Notification&&Notification.permission==='granted'){try{var nt=new Notification('Under typical: '+it.label,{body:money(it.ask,it.cur)+' \u00b7 '+Math.round(it.pct||0)+'% under '+money(it.typical,it.cur),tag:it.id});nt.onclick=function(){location.href=it.href||'/market#under'}}catch(e){}}
 if(document.hidden&&!flash){var on=false;flash=setInterval(function(){on=!on;document.title=(on?('\u25cf '+n.textContent+' '):'')+base},1200)}}
function poll(){
 fetch('/alerts.json',{cache:'no-store'}).then(function(r){return r.json()}).then(function(d){
  var items=d.items||[];
  if(!first){unread(items).forEach(function(it){toast(it);alert_(it)})}
  paint(items);first=false;
 }).catch(function(){})}
b.onclick=function(){
 var open=pop.hidden;pop.hidden=!open;b.setAttribute('aria-expanded',String(open));
 if(open){see(cur.map(function(i){return i.id}));paint(cur)}};
document.addEventListener('click',function(ev){if(!pop.hidden&&!ev.target.closest('.awrap')){pop.hidden=true;b.setAttribute('aria-expanded','false')}});
document.addEventListener('keydown',function(ev){if(ev.key==='Escape'&&!pop.hidden){pop.hidden=true;b.setAttribute('aria-expanded','false')}});
document.addEventListener('visibilitychange',function(){if(!document.hidden){document.title=base;clearInterval(flash);flash=null}});
poll();setInterval(poll,40000);
})();
"""
