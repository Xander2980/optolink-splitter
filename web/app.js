"use strict";
const el=id=>document.getElementById(id);
const labels={uptime_seconds:"Laufzeit (s)",cpu_pct:"CPU (%)",cpu_temperature_c:"CPU (°C)",ram_mib:"RAM (MiB)",ram_peak_mib:"RAM Spitze (MiB)",success_rate_pct:"Erfolgsquote (%)",response_mean_ms:"Ø Antwortzeit (ms)",response_max_ms:"Max. Antwortzeit (ms)",transactions:"Transaktionen",success:"Erfolgreich",restarts:"Neustarts",serial_bytes_tx:"Bytes gesendet",serial_bytes_rx:"Bytes empfangen",serial_write_calls:"Serielle Schreibaufrufe",poll_estimated_utilization_pct:"Geschätzte Poll-Last (%)",id:"Gerätekennung",profile:"Profil",firmware:"Firmware (Profil)",missing_configured_points:"Fehlende Konfiguration"};
const metrics=[["burner_active","Brenner",""],["boiler_c","Kessel Ist","°C"],["boiler_target_c","Kessel Soll","°C"],["flow_c","Vorlauf","°C"],["return_c","Rücklauf","°C"],["pump_speed_pct","Pumpe","%"],["outside_c","Außen","°C"],["start_attempts_total","Startversuche gesamt",""],["burner_hours_total","Brennerstunden","h"],["modulation_pct","Modulation","%"],["spread_k","Spreizung","K"],["boiler_overshoot_k","Sollwertabweichung","K"],["starts_last_hour","Starts / Stunde",""],["starts_today","Starts heute",""],["mean_run_seconds","Ø Laufzeit","s"],["mean_pause_seconds","Ø Pause","s"],["burner_duty_pct","Einschaltdauer","%"],["mean_modulation_pct","Ø Modulation","%"],["boiler_rise_k_min","Kesselanstieg","K/min"],["short_cycle_warning","Kurztaktung",""]];
let data={};
function fmt(v){if(v===null||v===undefined)return "—";if(typeof v==="boolean")return v?"Ja":"Nein";if(typeof v==="number")return v.toLocaleString("de-DE",{maximumFractionDigits:2});if(typeof v==="object")return JSON.stringify(v);return String(v)}
function when(v){return v?new Date(v*1000).toLocaleString("de-DE"):"—"}
function append(parent,tag,value,cls){const n=document.createElement(tag);n.textContent=value;if(cls)n.className=cls;parent.append(n);return n}
function list(id,items){const root=el(id);root.replaceChildren();for(const [k,v] of Object.entries(items||{})){append(root,"dt",labels[k]||k);append(root,"dd",fmt(v))}}
function rows(id,items){const root=el(id);root.replaceChildren();for(const row of items){const tr=append(root,"tr","");for(const v of row)append(tr,"td",fmt(v))}if(!items.length){const tr=append(root,"tr","");append(tr,"td","Keine Daten")}}
function renderPoints(){const q=el("search").value.toLowerCase();rows("points",Object.entries(data.datapoints||{}).filter(([k])=>k.toLowerCase().includes(q)).sort(([a],[b])=>a.localeCompare(b)).map(([k,p])=>[k,p.value,when(p.last_success),p.valid?(p.unchanged_warning?"Unverändert – prüfen":"Gültig"):"Ungültig"]))}
function chart(){const c=el("chart"),r=c.getBoundingClientRect(),dpr=window.devicePixelRatio||1;c.width=Math.max(1,r.width*dpr);c.height=240*dpr;const ctx=c.getContext("2d");ctx.scale(dpr,dpr);const w=r.width,h=240;const series=data.history||[];const keys=[["boiler","#f1a45e"],["flow","#48cfb1"],["return","#77a8ff"]];const values=series.flatMap(p=>keys.map(([k])=>p[k]).filter(Number.isFinite));ctx.font="11px system-ui";ctx.fillStyle="#9aaac0";if(values.length<2){ctx.fillText("Noch keine Temperaturhistorie – Datenpunkt-Zuordnung prüfen",12,25);return}const min=Math.floor(Math.min(...values)/5)*5,max=Math.max(min+5,Math.ceil(Math.max(...values)/5)*5),left=38,right=w-12,top=12,bottom=h-26,t0=series[0].time,t1=series[series.length-1].time;for(let i=0;i<=4;i++){const y=top+(bottom-top)*i/4;ctx.strokeStyle="#304052";ctx.beginPath();ctx.moveTo(left,y);ctx.lineTo(right,y);ctx.stroke();ctx.fillText(Math.round(max-(max-min)*i/4)+"°",2,y+4)}for(const [key,color] of keys){ctx.strokeStyle=color;ctx.lineWidth=2;ctx.beginPath();let open=false;for(const p of series){if(!Number.isFinite(p[key])){open=false;continue}const x=left+(right-left)*(p.time-t0)/Math.max(1,t1-t0),y=bottom-(bottom-top)*(p[key]-min)/(max-min);if(open)ctx.lineTo(x,y);else ctx.moveTo(x,y);open=true}ctx.stroke()}ctx.fillStyle="#9aaac0";ctx.fillText(new Date(t0*1000).toLocaleTimeString("de-DE",{hour:"2-digit",minute:"2-digit"}),left,h-6);ctx.fillText(new Date(t1*1000).toLocaleTimeString("de-DE",{hour:"2-digit",minute:"2-digit"}),Math.max(left,right-40),h-6)}
function render(){const s=el("status");s.replaceChildren();append(s,"div","Optolink: "+(data.optolink_ready?"Online":"Offline"),data.optolink_ready?"ok":"bad");append(s,"div","MQTT: "+(data.mqtt_connected?"Online":"Offline"),data.mqtt_connected?"ok":"warn");const cards=el("cards");cards.replaceChildren();for(const [key,label,unit] of metrics){let v=(data.heating||{})[key];if(key==="burner_active"&&v!=null)v=v?"An":"Aus";const card=append(cards,"div","","card");append(card,"div",label,"label");append(card,"strong",fmt(v)+(v==null||!unit?"":" "+unit),key==="short_cycle_warning"&&v?"warn":"")}renderPoints();list("sys",data.system);list("comm",data.communication);list("device",data.device);list("mapping",data.heating_mapping);list("queues",data.queues);list("polling",Object.fromEntries(Object.entries(data.polling||{}).map(([k,v])=>[k,v.interval+" s · "+v.failures+" Fehler"])));rows("errors",[...(data.errors||[])].reverse().map(x=>[when(x.time),x.source,x.code,x.data_hex]));rows("writes",[...(data.writes||[])].reverse().map(x=>[when(x.time),x.address,x.status,(x.before||"—")+" → "+(x.after||x.requested||"—")]));el("updated").textContent="Letzte Aktualisierung: "+when(data.time);chart()}
async function refresh(){try{const response=await fetch("/api/status",{cache:"no-store"});if(!response.ok)throw Error("HTTP "+response.status);data=await response.json();render()}catch(e){el("status").textContent="Dashboard-Verbindung unterbrochen";el("status").className="bad"}finally{setTimeout(refresh,3000)}}
el("search").addEventListener("input",renderPoints);window.addEventListener("resize",chart);refresh();
let catalogOffset=0,catalogTotal=0,catalogSequence=0;
async function catalogRows(){
 const seq=++catalogSequence,model=el("catalog-model").value;
 if(!model)return;
 const params=new URLSearchParams({model,q:el("catalog-search").value,access:el("catalog-access").value,offset:String(catalogOffset)});
 try{
  const res=await fetch("/api/catalog?"+params);if(!res.ok)throw Error(res.status);
  const result=await res.json();if(seq!==catalogSequence)return;
  catalogTotal=result.count;
  rows("catalog-rows",result.rows.map(x=>[x.name,x.address,x.length,x.conversion,x.access,x.unit]));
  el("catalog-info").textContent=catalogTotal? (catalogOffset+1)+"–"+Math.min(catalogOffset+100,catalogTotal)+" von "+catalogTotal+" · "+model:"Keine Treffer";
  el("catalog-prev").disabled=catalogOffset===0;el("catalog-next").disabled=catalogOffset+100>=catalogTotal;
 }catch(e){if(seq===catalogSequence)el("catalog-info").textContent="Katalog nicht verfügbar"}
}
async function catalogInit(){
 try{
  const res=await fetch("/api/catalog");if(!res.ok)throw Error(res.status);
  const result=await res.json();for(const entry of result.lists){const opt=append(el("catalog-model"),"option",entry.name+" ("+entry.count+")");opt.value=entry.name}
  if(!result.lists.length)el("catalog-info").textContent="Keine Listen vorhanden";
  else catalogRows();
 }catch(e){el("catalog-info").textContent="Katalog nicht verfügbar"}
}
let catalogTimer;
function resetCatalog(){catalogOffset=0;clearTimeout(catalogTimer);catalogTimer=setTimeout(catalogRows,180)}
for(const id of ["catalog-model","catalog-search","catalog-access"])el(id).addEventListener(id==="catalog-search"?"input":"change",resetCatalog);
el("catalog-prev").addEventListener("click",()=>{catalogOffset=Math.max(0,catalogOffset-100);catalogRows()});
el("catalog-next").addEventListener("click",()=>{if(catalogOffset+100<catalogTotal){catalogOffset+=100;catalogRows()}});
catalogInit();
