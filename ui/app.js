"use strict";
/* ======================= trạng thái ======================= */
let ST={queue:[],selected:null,running:false,progress:{}};
let PR=null;            // dự án hiện tại
let JID=null;
let TAB="sub";
let ACT=null;           // vùng đang chọn {kind,index}
let CFG={translation:{},tts:{}};
let MANUAL={text:"",name:"",engine:"edge",voice:"",pitch:"+0Hz",rate:"+0%",
            audio_path:"",audio_duration:0,output_path:"",status:"Sẵn sàng",error:"",
            nhac_bai:"",nhac_db:-38,nhac_duck:true,nhac_ten:"",
            anh:"",slide_kieu:"chuyen_dong",writer_title:"",
            script_path:"",script_words:0};
let NHAC_LIST=[];
let saveTimer=null;
let configTimer=null;
let _lastRev=-1;
let _manualRev=-1;
let _manualWorking=false;
let _lastScriptPath="";
let _queuePrev={};
const V=()=>document.getElementById("video");

const COLORS=["#FFFFFF","#FFD700","#7DD3FC","#FDA4AF","#86EFAC","#C4B5FD"];
const GRID=[["top-left","top-center","top-right"],["mid-left","mid-center","mid-right"],
            ["bottom-left","bottom-center","bottom-right"]];

/* ======================= tiện ích ======================= */
function toast(msg,kind){
  const d=document.createElement("div");
  d.className="tst "+(kind||"");
  d.textContent=msg;
  document.getElementById("toast").appendChild(d);
  setTimeout(()=>d.remove(),4200);
}
function showEmpty(title,msg){
  const e=document.getElementById("empty");
  e.innerHTML="";
  const b=document.createElement("b");
  b.textContent=title;
  e.appendChild(b);
  e.appendChild(document.createTextNode(msg||""));
  e.style.display="";
  document.getElementById("vwrap").style.display="none";
}
function fmt(s){
  s=Math.max(0,s||0);
  const h=Math.floor(s/3600),m=Math.floor(s%3600/60),x=Math.floor(s%60);
  return String(h).padStart(2,"0")+":"+String(m).padStart(2,"0")+":"+String(x).padStart(2,"0");
}
function parseTime(v){
  if(v==null) return null;
  const raw=String(v).trim().toLowerCase().replace(",",".");
  if(!raw) return null;
  const clean=raw.endsWith("s")?raw.slice(0,-1):raw;
  if(clean.includes(":")){
    const parts=clean.split(":").map(x=>Number(x.trim()));
    if(parts.some(x=>!Number.isFinite(x))) return null;
    return parts.reduce((acc,x)=>acc*60+x,0);
  }
  const n=Number(clean);
  return Number.isFinite(n)?n:null;
}
function dur(){ return V().duration||(PR&&PR.duration)||0; }
function trimBounds(){
  const d=Math.max(.1,dur()||0);
  const op=(PR&&PR.options)||{};
  let s=Math.max(0,Number(op.trim_start)||0);
  let e=(op.trim_end===""||op.trim_end==null)?d:Number(op.trim_end);
  if(!Number.isFinite(e)) e=d;
  s=Math.min(Math.max(0,s),Math.max(0,d-.1));
  e=Math.min(Math.max(s+.1,e),d);
  return {on:!!op.trim_enabled,start:s,end:e,duration:Math.max(.1,e-s),full:d};
}
function setTrimEnabled(on){
  if(!PR) return;
  const b=trimBounds();
  PR.options.trim_enabled=!!on;
  PR.options.trim_start=Math.round(b.start*10)/10;
  PR.options.trim_end=Math.round(b.end*10)/10;
  save(); drawTracks(true); renderPanel();
}
function setTrimPoint(k,v,quiet){
  if(!PR) return;
  const d=Math.max(.1,dur()||PR.duration||0), b=trimBounds();
  let s=b.start, e=b.end, n=parseTime(v);
  if(n==null) n=k==="end"?d:0;
  if(k==="start") s=Math.min(Math.max(0,n),Math.max(0,e-.1));
  else e=Math.min(Math.max(s+.1,n),d);
  PR.options.trim_enabled=true;
  PR.options.trim_start=Math.round(s*10)/10;
  PR.options.trim_end=Math.round(e*10)/10;
  if(!quiet){ save(); drawTracks(true); renderPanel(); }
}
function setTrimFromNow(k){ setTrimPoint(k,V().currentTime||0); }
function trimFull(){
  if(!PR) return;
  PR.options.trim_enabled=false;
  PR.options.trim_start=0;
  PR.options.trim_end=null;
  save(); drawTracks(true); renderPanel();
}
function seekTrim(k){
  const b=trimBounds();
  V().currentTime=k==="end"?Math.max(0,b.end-.15):b.start;
  tick();
}
async function api(path,body){
  const o=body?{method:"POST",headers:{"Content-Type":"application/json"},
                body:JSON.stringify(body)}:{};
  const r=await fetch(path,o);
  let j={}; try{j=await r.json()}catch(e){}
  if(j&&j.error) throw new Error(j.error);
  return j;
}
async function loadConfig(){
  try{
    const r=await api("/api/config");
    CFG.translation=r.translation||{};
    CFG.tts=r.tts||{};
    const t=CFG.tts;
    MANUAL.engine=t.engine||"edge";
    MANUAL.voice=t.voice||"";
    MANUAL.pitch=t.pitch||"+0Hz";
    MANUAL.rate=t.rate||"+0%";
  }catch(e){
    toast("Không đọc được config.yaml: "+e.message,"warn");
  }
}
async function saveTrCfg(showToast){
  clearTimeout(configTimer);
  try{
    const r=await api("/api/config",{translation:CFG.translation||{}});
    CFG.translation=r.translation||CFG.translation||{};
    if(showToast) toast("Đã lưu cấu hình dịch","ok");
    return true;
  }catch(e){
    toast("Lỗi lưu cấu hình dịch: "+e.message,"err");
    return false;
  }
}
function setTrCfg(k,v,rerender){
  CFG.translation=CFG.translation||{};
  CFG.translation[k]=v;
  clearTimeout(configTimer);
  configTimer=setTimeout(()=>saveTrCfg(false),250);
  if(rerender) renderPanel();
}
async function testTrCfg(){
  clearTimeout(configTimer);
  try{
    const r=await api("/api/test_translation",{translation:CFG.translation||{}});
    toast(r.message||"API hoạt động","ok");
  }catch(e){
    toast("Test API lỗi: "+e.message,"err");
  }
}
function save(){
  if(!PR||!JID) return;
  clearTimeout(saveTimer);
  saveTimer=setTimeout(()=>{
    api("/api/project",{id:JID,regions:PR.regions,logo:PR.logo,
        sub_style:PR.sub_style,segments:PR.segments,options:PR.options}).catch(()=>{});
  },400);
}
async function saveNow(){
  if(!PR||!JID) return;
  clearTimeout(saveTimer);
  await api("/api/project",{id:JID,regions:PR.regions,logo:PR.logo,
      sub_style:PR.sub_style,segments:PR.segments,options:PR.options});
}

/* ======================= app desktop ======================= */
const DESK=()=>!!(window.pywebview&&window.pywebview.api);
function winCmd(c){
  if(!DESK()) return;
  if(c==="min") pywebview.api.win_minimize();
  else if(c==="max") pywebview.api.win_maximize();
  else pywebview.api.win_close();
}
async function openOut(p){
  if(DESK()) await pywebview.api.open_folder(p);
  else toast(p);
}

/* ======================= hàng đợi ======================= */
async function pickFile(){
  let paths=[];
  if(DESK()){
    const r=await pywebview.api.pick_video();
    if(!r||r.error){ if(r&&r.error) toast(r.error,"err"); return; }
    paths=Array.isArray(r)?r:[r];
  }else{
    const p=prompt("Dán ĐƯỜNG DẪN video trên máy:\n(ví dụ E:\\Video\\phim.mp4)");
    if(!p) return; paths=[p];
  }
  if(!paths.length) return;
  let last=null;
  for(const p of paths){
    try{ const r=await api("/api/queue/add",{path:p}); last=r.id; }
    catch(e){ toast(e.message,"err"); }
  }
  if(last!=null) await selectJob(last);
  refresh();
  if(paths.length>1) toast(`Đã thêm ${paths.length} video vào hàng đợi`,"ok");
}
async function addUrl(){
  const u=document.getElementById("url").value.trim();
  if(!u) return;
  toast("Đã thêm vào hàng đợi tải…");
  try{ const r=await api("/api/queue/add",{url:u});
       document.getElementById("url").value="";
       if(r.async){
         await api("/api/queue/select",{id:r.id});
         JID=null; PR=null; _lastRev=-1;
         showEmpty("Đang tải video", "Khi tải xong, video sẽ tự hiện ở đây.");
         renderPanel();
         await refresh();
         toast("Đang tải trong nền…","ok");
       }else{
         await selectJob(r.id); refresh(); toast("Tải xong","ok");
       } }
  catch(e){ toast(e.message,"err"); }
}
async function delJob(id,ev){
  ev.stopPropagation();
  await api("/api/queue/remove",{id}); if(JID===id){JID=null;PR=null;} refresh();
}
async function clearDone(){
  for(const j of ST.queue.filter(x=>x.status==="xong"))
    await api("/api/queue/remove",{id:j.id});
  refresh();
}
async function selectJob(id){
  const queued=(ST.queue||[]).find(x=>x.id===id);
  if(queued&&(!queued.path||queued.status==="đang tải")){
    JID=null; PR=null; _lastRev=-1;
    await api("/api/queue/select",{id});
    showEmpty("Đang tải video", queued.note||"Vui lòng chờ file tải xong.");
    renderPanel();
    refresh();
    return;
  }
  JID=id; await api("/api/queue/select",{id});
  PR=await api("/api/project?id="+id);
  _lastRev=-1; _ciHint=0; _trkSig="";
  const v=V();
  v.src="/api/video?id="+id;
  document.getElementById("vwrap").style.display="";
  document.getElementById("empty").style.display="none";
  v.onloadedmetadata=()=>{applyZoom();draw();document.getElementById("tdur").textContent=fmt(v.duration)};
  renderPanel(); draw(); loadVoices(false);
}
function saveProject(){ save(); toast("Đã lưu cấu hình dự án","ok"); }

/* ======================= vùng phủ (3 lớp) ======================= */
function addRegion(type){
  if(!PR) return toast("Chưa chọn video","warn");
  const w=Math.round(PR.w*0.5), h=Math.round(PR.h*0.13);
  PR.regions.push({x:Math.round((PR.w-w)/2),
                   y:type==="delogo"?Math.round(PR.h*0.05):Math.round(PR.h*0.8),
                   w,h,type,strength:20,start:null,end:null});
  ACT={kind:"rgn",i:PR.regions.length-1}; save(); draw();
}
function clearRegions(){ if(!PR)return; PR.regions=[]; ACT=null; save(); draw(); }
function addLogoBox(){
  if(!PR) return toast("Chưa chọn video","warn");
  PR.logo={x:Math.round(PR.w*0.03),y:Math.round(PR.h*0.04),
           w:Math.round(PR.w*0.15),h:Math.round(PR.h*0.09),opacity:1,path:""};
  ACT={kind:"logo"}; setTab("logo"); save(); draw();
}
async function autoDetect(){
  if(!JID) return toast("Chưa chọn video","warn");
  toast("Đang dò vùng phụ đề gốc…");
  try{
    const r=await api("/api/detect_sub",{id:JID});
    PR=await api("/api/project?id="+JID);
    ACT={kind:"rgn",i:PR.regions.length-1};
    draw(); renderPanel();
    toast(`Đã dò: ${r.region.w}×${r.region.h} tại (${r.region.x},${r.region.y})`,"ok");
  }catch(e){ toast(e.message,"err"); }
}

/* ---------- vẽ các lớp lên video ---------- */
function scale(){
  const v=V();
  if(!v.videoWidth) return 1;
  return v.clientWidth/v.videoWidth;
}
function draw(){
  const L=document.getElementById("layers");
  if(!PR||!V().videoWidth){L.innerHTML="";return;}
  const k=scale(), prev=document.getElementById("prev").checked;
  L.innerHTML="";

  (PR.regions||[]).forEach((r,i)=>{
    const isD=r.type==="delogo";
    const el=mkBox(r,k,isD?"delogo":"blur",String(i+1),
                   isD?"Xoá logo gốc":"Vùng làm mờ (lớp 2)",()=>{
      PR.regions.splice(i,1); ACT=null; save(); draw();
    });
    if(prev&&!isD){
      if(regionActiveAt(r,V().currentTime||0))
        el.style.backdropFilter=`blur(${Math.max(2,(r.strength||20)*k*0.55)}px)`;
      el.style.background="rgba(0,0,0,.04)";
    }
    el.dataset.rgn=i;
    if(ACT&&ACT.kind==="rgn"&&ACT.i===i) el.classList.add("act");
    el.onpointerdown=e=>startDrag(e,el,r,k,()=>{ACT={kind:"rgn",i};renderPanel();});
    L.appendChild(el);
  });

  if(PR.logo){
    const el=mkBox(PR.logo,k,"logo","L","Logo của bạn",()=>{PR.logo=null;save();draw();});
    if(ACT&&ACT.kind==="logo") el.classList.add("act");
    el.onpointerdown=e=>startDrag(e,el,PR.logo,k,()=>{ACT={kind:"logo"};renderPanel();});
    L.appendChild(el);
  }

  const st=PR.sub_style||{}, box=st.box;
  if(box){
    const el=mkBox(box,k,"sub","3","phụ đề Việt · kéo để di chuyển",null);
    if(ACT&&ACT.kind==="sub") el.classList.add("act");
    if(prev){
      const t=document.createElement("div");
      t.id="subtext";
      t.textContent=currentLineText()||"Phụ đề tiếng Việt sẽ hiện ở đây";
      t.style.font=`${st.bold?"700":"400"} ${Math.max(9,(st.size||30)*k)}px "${st.font||"Be Vietnam Pro"}",sans-serif`;
      t.style.color=st.color||"#fff";
      t.style.whiteSpace=st.single_line===false?"normal":"nowrap";
      t.style.overflow="visible";
      t.style.left="0";
      t.style.right="0";
      t.style.width="100%";
      t.style.maxWidth="100%";
      t.style.margin="0 auto";
      const ow=Math.max(1,(st.outline||2)*k);
      t.style.textShadow=`0 0 ${ow}px ${st.outline_color||"#000"},`+
        [[-1,-1],[1,-1],[-1,1],[1,1]].map(([a,b])=>`${a*ow}px ${b*ow}px 0 ${st.outline_color||"#000"}`).join(",");
      const al=st.align||"mid-center";
      t.style.textAlign=al.includes("left")?"left":al.includes("right")?"right":"center";
      if(al.startsWith("top")){t.style.top="0";t.style.bottom="auto";}
      else if(al.startsWith("mid")){t.style.top="50%";t.style.bottom="auto";t.style.transform="translateY(-50%)";}
      el.appendChild(t);
    }
    el.onpointerdown=e=>startDrag(e,el,box,k,()=>{ACT={kind:"sub"};renderPanel();});
    L.appendChild(el);
  }
  drawTracks();
  const a=activeRegion();
  document.getElementById("rgninfo").textContent = a
    ? `Vùng ${a.type==="delogo"?"xoá logo":a.type==="sub"?"phụ đề Việt":"làm mờ"} · ${Math.round(a.w)}×${Math.round(a.h)} @ (${Math.round(a.x)},${Math.round(a.y)})`
    : "Chưa chọn vùng nào";
}

function regionActiveAt(r,t){
  const s=(r.start===""||r.start==null)?null:Number(r.start);
  const e=(r.end===""||r.end==null)?null:Number(r.end);
  if(Number.isFinite(s)&&t<s) return false;
  if(Number.isFinite(e)&&t>e) return false;
  return true;
}
function updateRegionPreview(){
  if(!PR||!document.getElementById("prev").checked) return;
  const k=scale(), t=V().currentTime||0;
  document.querySelectorAll(".box.blur[data-rgn]").forEach(el=>{
    const r=PR.regions[+el.dataset.rgn]; if(!r) return;
    el.style.backdropFilter=regionActiveAt(r,t)
      ? `blur(${Math.max(2,(r.strength||20)*k*0.55)}px)` : "";
  });
}
function mkBox(r,k,cls,num,label,onRemove){
  const el=document.createElement("div");
  el.className="box "+cls;
  el.style.left=(r.x*k)+"px"; el.style.top=(r.y*k)+"px";
  el.style.width=(r.w*k)+"px"; el.style.height=(r.h*k)+"px";
  const tag=document.createElement("div");
  tag.className="tag"; tag.textContent=num+(label?" · "+label:"");
  el.appendChild(tag);
  if(onRemove){
    const x=document.createElement("div");
    x.className="rm"; x.textContent="×";
    x.onpointerdown=e=>{e.stopPropagation();onRemove();};
    el.appendChild(x);
  }
  ["nw","ne","sw","se","n","s","w","e"].forEach(p=>{
    const h=document.createElement("div"); h.className="h "+p; h.dataset.p=p;
    el.appendChild(h);
  });
  return el;
}
function activeRegion(){
  if(!PR||!ACT) return null;
  if(ACT.kind==="rgn") return PR.regions[ACT.i];
  if(ACT.kind==="logo") return PR.logo&&{...PR.logo,type:"logo"};
  if(ACT.kind==="sub") return PR.sub_style.box&&{...PR.sub_style.box,type:"sub"};
  return null;
}
/* ---------- KÉO & ĐỔI KÍCH CỠ (mượt) ----------
   - pointer capture: chuột đi ra ngoài khung vẫn kéo tiếp, không rớt.
   - gộp theo khung hình (rAF): chuột bắn ra ~120 sự kiện/giây, chỉ vẽ 1 lần/khung.
   - hít vào mép và tâm khung hình, cùng các vùng khác; giữ Alt để tắt hít.
   - Shift khi đổi cỡ = giữ nguyên tỉ lệ.
   - chỉ ghi lại DOM khi thả tay, lúc kéo không dựng lại gì. */
const SNAP=9;          // ngưỡng hít, tính bằng pixel màn hình
let _guides=null;

/* Toán học của kéo/đổi cỡ - HÀM THUẦN nên test được, không đụng DOM.
   Trả {x,y,w,h,gx,gy} với gx/gy là mốc đang hít (null nếu không hít).
   MINW/MINH: không cho vùng nhỏ quá mức bấm được. */
const MINW=24, MINH=18;
function computeDrag({o,handle,ratio,maxW,maxH,xs,ys,dx,dy,tol,noSnap,keepRatio}){
  let x=o.x,y=o.y,w=o.w,h=o.h, gx=null, gy=null;

  if(!handle){ x=o.x+dx; y=o.y+dy; }
  else{
    if(handle.includes("w")){ x=o.x+dx; w=o.w-dx; }
    if(handle.includes("e")){ w=o.w+dx; }
    if(handle.includes("n")){ y=o.y+dy; h=o.h-dy; }
    if(handle.includes("s")){ h=o.h+dy; }
    if(keepRatio&&handle.length===2){          // giữ tỉ lệ khi kéo ở góc
      h=w/ratio;
      if(handle.includes("n")) y=o.y+o.h-h;
    }
  }
  w=Math.max(MINW,w); h=Math.max(MINH,h);

  if(!noSnap){
    const trySnap=(vals,cands)=>{              // trả [lệch, mốc] gần nhất
      let best=null;
      for(const v of vals) for(const c of cands){
        const d=c-v;
        if(Math.abs(d)<tol&&(!best||Math.abs(d)<Math.abs(best[0]))) best=[d,c];
      }
      return best;
    };
    if(!handle){
      const bx=trySnap([x,x+w/2,x+w],xs), by=trySnap([y,y+h/2,y+h],ys);
      if(bx){ x+=bx[0]; gx=bx[1]; }
      if(by){ y+=by[0]; gy=by[1]; }
    }else{
      if(handle.includes("w")){const b=trySnap([x],xs); if(b){x+=b[0];w-=b[0];gx=b[1];}}
      if(handle.includes("e")){const b=trySnap([x+w],xs); if(b){w+=b[0];gx=b[1];}}
      if(handle.includes("n")){const b=trySnap([y],ys); if(b){y+=b[0];h-=b[0];gy=b[1];}}
      if(handle.includes("s")){const b=trySnap([y+h],ys); if(b){h+=b[0];gy=b[1];}}
    }
  }

  // ép nằm gọn trong khung hình
  w=Math.max(MINW,Math.min(w,maxW)); h=Math.max(MINH,Math.min(h,maxH));
  x=Math.max(0,Math.min(x,maxW-w));  y=Math.max(0,Math.min(y,maxH-h));
  return {x:Math.round(x),y:Math.round(y),w:Math.round(w),h:Math.round(h),gx,gy};
}
if(typeof module!=="undefined") module.exports={computeDrag};

function showGuides(vx,vy,k){
  if(!_guides){
    _guides=document.createElement("div");
    _guides.style.cssText="position:absolute;inset:0;pointer-events:none;z-index:5";
    document.getElementById("layers").appendChild(_guides);
  }
  _guides.innerHTML="";
  const mk=(css)=>{const d=document.createElement("div");
    d.style.cssText="position:absolute;background:#22d3ee;opacity:.85;"+css;
    _guides.appendChild(d);};
  if(vx!=null) mk(`left:${vx*k}px;top:0;bottom:0;width:1px`);
  if(vy!=null) mk(`top:${vy*k}px;left:0;right:0;height:1px`);
}
function hideGuides(){ if(_guides){_guides.remove(); _guides=null;} }

function startDrag(e,el,r,k,onPick){
  if(e.button!==0) return;
  e.preventDefault(); e.stopPropagation();
  if(onPick) onPick();

  const handle=(e.target.dataset&&e.target.dataset.p)||null;
  const sx=e.clientX, sy=e.clientY;
  const o={x:r.x,y:r.y,w:r.w,h:r.h};
  const ratio=o.w/Math.max(1,o.h);
  const maxW=PR.w, maxH=PR.h;
  const info=document.getElementById("rgninfo");

  // các mốc để hít vào (mép + tâm khung hình + mép các vùng khác)
  const xs=[0,maxW/2,maxW], ys=[0,maxH/2,maxH];
  for(const q of (PR.regions||[])) if(q!==r){xs.push(q.x,q.x+q.w);ys.push(q.y,q.y+q.h);}
  const sb=PR.sub_style&&PR.sub_style.box;
  if(sb&&sb!==r){xs.push(sb.x,sb.x+sb.w);ys.push(sb.y,sb.y+sb.h);}

  el.style.willChange="left,top,width,height";
  el.classList.add("act");
  document.body.style.cursor=handle?getComputedStyle(e.target).cursor:"move";
  try{ el.setPointerCapture(e.pointerId); }catch(_){}

  let raf=null, last=null, gx=null, gy=null;

  const apply=()=>{
    raf=null;
    if(!last) return;
    const res=computeDrag({
      o, handle, ratio, maxW, maxH, xs, ys,
      dx:(last.clientX-sx)/k, dy:(last.clientY-sy)/k,
      tol:SNAP/k, noSnap:last.altKey, keepRatio:last.shiftKey,
    });
    gx=res.gx; gy=res.gy;
    r.x=res.x; r.y=res.y; r.w=res.w; r.h=res.h;
    el.style.left=(r.x*k)+"px"; el.style.top=(r.y*k)+"px";
    el.style.width=(r.w*k)+"px"; el.style.height=(r.h*k)+"px";
    if(gx!=null||gy!=null) showGuides(gx,gy,k); else hideGuides();
    info.textContent=`${Math.round(r.w)}×${Math.round(r.h)} @ (${Math.round(r.x)},${Math.round(r.y)})`
      +(gx!=null||gy!=null?"  · hít mốc":"")+"   [Alt: tắt hít · Shift: giữ tỉ lệ]";
  };

  const move=ev=>{ last=ev; if(raf===null) raf=requestAnimationFrame(apply); };
  const up=ev=>{
    window.removeEventListener("pointermove",move);
    window.removeEventListener("pointerup",up);
    window.removeEventListener("pointercancel",up);
    if(raf!==null){ cancelAnimationFrame(raf); apply(); }
    try{ el.releasePointerCapture(ev.pointerId); }catch(_){}
    el.style.willChange="";
    document.body.style.cursor="";
    hideGuides();
    save(); draw(); renderPanel();
  };
  window.addEventListener("pointermove",move,{passive:true});
  window.addEventListener("pointerup",up);
  window.addEventListener("pointercancel",up);
}
function applyZoom(){
  const z=document.getElementById("zoom").value/100;
  const st=document.getElementById("stage");
  V().style.maxHeight=(st.clientHeight*z-4)+"px";
  V().style.maxWidth=(st.clientWidth*z-4)+"px";
  setTimeout(draw,30);
}

/* ======================= timeline ======================= */
function segs(){ return (PR&&PR.segments)||[]; }
/* Tìm nhị phân + nhớ vị trí lần trước: gọi mỗi khung hình nên không được quét
   tuyến tính qua hàng nghìn dòng. */
let _ciHint=0;
function curIndex(){
  const s=segs(); if(!s.length) return -1;
  const t=V().currentTime||0;
  const hit=i=>i>=0&&i<s.length&&t>=s[i].start-0.05&&t<=s[i].end+0.05;
  if(hit(_ciHint)) return _ciHint;
  if(hit(_ciHint+1)){ _ciHint++; return _ciHint; }
  let lo=0, hi=s.length-1, best=-1;
  while(lo<=hi){
    const m=(lo+hi)>>1;
    if(s[m].start-0.05<=t){ best=m; lo=m+1; } else hi=m-1;
  }
  if(hit(best)){ _ciHint=best; return best; }
  return -1;
}
function currentLineText(){
  const i=curIndex(); if(i<0) return "";
  return segs()[i].vi||segs()[i].src||"";
}
/* Dựng lại thanh thời gian là việc NẶNG (phim 3 tiếng có hàng nghìn dòng).
   Chỉ dựng khi dữ liệu thật sự đổi; còn kim thời gian thì di riêng mỗi khung
   hình. Trước đây hàm này chạy 4 lần/giây và tạo lại toàn bộ DOM -> treo app. */
let _trkSig="", _needles=[], _chips=[], _curChip=null;
const MAX_CHIPS=400;      // phim dài chỉ vẽ các dòng quanh chỗ đang xem

function trackSignature(){
  const s=segs(), d=Math.round(V().duration||(PR&&PR.duration)||0);
  const win=Math.floor((V().currentTime||0)/60);   // đổi cửa sổ mỗi phút
  const b=trimBounds();
  return `${s.length}|${(PR&&PR.regions||[]).length}|${d}|${
    s.length>MAX_CHIPS?win:0}|${b.on?1:0}|${Math.round(b.start*10)}|${Math.round(b.end*10)}`;
}
function buildTracks(){
  const d=(V().duration||PR&&PR.duration||1);
  const cut=document.getElementById("trkCut"),
        sub=document.getElementById("trkSub"), tts=document.getElementById("trkTts"),
        rgn=document.getElementById("trkRgn");
  if(cut) cut.innerHTML="";
  sub.innerHTML=""; tts.innerHTML=""; rgn.innerHTML="";
  _chips=[]; _curChip=null;

  if(cut){
    const b=trimBounds();
    const c=document.createElement("div");
    c.className="tchip cut"+(b.on?"":" off");
    c.style.left=(b.start/d*100)+"%";
    c.style.width=Math.max(.4,(b.end-b.start)/d*100)+"%";
    c.textContent=b.on?`${fmt(b.start)} → ${fmt(b.end)}`:"Toàn bộ video";
    c.title=b.on?`Giữ ${fmt(b.duration)} từ ${fmt(b.start)} đến ${fmt(b.end)}`:"Chưa bật cắt";
    ["a","b"].forEach((side,idx)=>{
      const h=document.createElement("div");
      h.className="trimH "+side;
      h.title=idx?"Kéo điểm cuối":"Kéo điểm đầu";
      h.onpointerdown=e=>startTrimDrag(e,idx?"end":"start");
      c.appendChild(h);
    });
    cut.onclick=e=>{
      if(e.target.classList.contains("trimH")) return;
      const r=cut.getBoundingClientRect();
      V().currentTime=Math.max(0,Math.min(d,(e.clientX-r.left)/r.width*d)); tick();
    };
    cut.appendChild(c);
  }

  const all=segs();
  let from=0, to=all.length;
  if(all.length>MAX_CHIPS){          // chỉ vẽ quanh vị trí đang xem
    const ci=Math.max(0,curIndex());
    from=Math.max(0,ci-MAX_CHIPS/2); to=Math.min(all.length,from+MAX_CHIPS);
  }
  const fs=document.createDocumentFragment(), ft=document.createDocumentFragment();
  for(let i=from;i<to;i++){
    const s=all[i];
    const c=document.createElement("div");
    c.className="tchip vi";
    c.style.left=(s.start/d*100)+"%";
    c.style.width=Math.max(.4,(s.end-s.start)/d*100)+"%";
    c.textContent=(s.vi||s.src||"").slice(0,22);
    c.title=(s.vi||s.src||"");
    c.dataset.i=i;
    c.onclick=()=>{V().currentTime=s.start;tick();};
    fs.appendChild(c); _chips[i]=c;
    const b=document.createElement("div");
    b.className="tbar";
    b.style.left=((s.placed!=null?s.placed:s.start)/d*100)+"%";
    ft.appendChild(b);
  }
  sub.appendChild(fs); tts.appendChild(ft);

  (PR&&PR.regions||[]).forEach((r,i)=>{
    const c=document.createElement("div");
    c.className="tchip rg";
    const rs=(r.start===""||r.start==null)?0:Math.max(0,Number(r.start)||0);
    const re=(r.end===""||r.end==null)?d:Math.min(d,Math.max(rs+.1,Number(r.end)||d));
    c.style.left=(rs/d*100)+"%"; c.style.width=Math.max(.4,(re-rs)/d*100)+"%";
    c.textContent=(r.type==="delogo"?"Xoá logo":"Vùng mờ")+" "+(i+1);
    if(i>0) c.style.opacity=.65;
    c.onclick=()=>{ACT={kind:"rgn",i};draw();renderPanel();};
    rgn.appendChild(c);
  });

  _needles=[cut,sub,tts,rgn].filter(Boolean).map(el=>{
    const n=document.createElement("div"); n.className="tneedle";
    el.appendChild(n); return n;
  });
}
function startTrimDrag(e,side){
  if(!PR) return;
  e.preventDefault(); e.stopPropagation();
  const line=document.getElementById("trkCut"), d=Math.max(.1,dur());
  const apply=ev=>{
    const r=line.getBoundingClientRect();
    const t=Math.max(0,Math.min(d,(ev.clientX-r.left)/r.width*d));
    setTrimPoint(side,t,true);
    drawTracks(true);
    const b=trimBounds();
    document.getElementById("rgninfo").textContent=
      `Đoạn giữ ${fmt(b.start)} → ${fmt(b.end)} · dài ${fmt(b.duration)}`;
  };
  const move=ev=>apply(ev);
  const up=ev=>{
    window.removeEventListener("pointermove",move);
    window.removeEventListener("pointerup",up);
    window.removeEventListener("pointercancel",up);
    apply(ev); save(); renderPanel();
  };
  window.addEventListener("pointermove",move,{passive:true});
  window.addEventListener("pointerup",up);
  window.addEventListener("pointercancel",up);
}
function drawTracks(force){
  const sig=trackSignature();
  if(force||sig!==_trkSig){ _trkSig=sig; buildTracks(); }
  moveNeedle();
}
function moveNeedle(){
  const d=(V().duration||PR&&PR.duration||1);
  const p=((V().currentTime||0)/d*100)+"%";
  for(const n of _needles) n.style.left=p;
  const ci=curIndex();
  if(_curChip!==_chips[ci]){
    if(_curChip) _curChip.classList.remove("cur");
    _curChip=_chips[ci]||null;
    if(_curChip) _curChip.classList.add("cur");
  }
}
/* tick() chạy theo mỗi khung hình video (~4 lần/giây) nên phải THẬT NHẸ:
   chỉ di kim + đổi vài dòng chữ, tuyệt đối không dựng lại DOM. */
let _lastSec=-1, _tickPend=false;
function tick(){
  if(_tickPend) return;
  _tickPend=true;
  requestAnimationFrame(()=>{
    _tickPend=false;
    const v=V(); if(!v.duration) return;
    document.querySelector("#scrub i").style.width=(v.currentTime/v.duration*100)+"%";
    drawTracks();                       // chỉ dựng lại khi chữ ký đổi
    updateRegionPreview();
    const sec=Math.floor(v.currentTime);
    if(sec!==_lastSec){                 // chữ chỉ đổi mỗi giây, không phải mỗi khung
      _lastSec=sec;
      document.getElementById("tcur").textContent=fmt(v.currentTime);
      document.getElementById("fnum").textContent=
        String(Math.round(v.currentTime*25)).padStart(5,"0");
      if(document.getElementById("prev").checked){
        const t=document.getElementById("subtext");
        const txt=currentLineText()||"Phụ đề tiếng Việt sẽ hiện ở đây";
        if(t&&t.textContent!==txt) t.textContent=txt;
      }
      renderLinesHighlight();
    }
  });
}
function seekBar(e){
  const v=V(); if(!v.duration) return;
  const r=e.currentTarget.getBoundingClientRect();
  v.currentTime=(e.clientX-r.left)/r.width*v.duration; tick();
}
function jump(d){ const v=V(); v.currentTime=Math.max(0,v.currentTime+d); tick(); }
function togglePlay(){
  const v=V(); if(v.paused){v.play();document.getElementById("play").textContent="❚❚";}
  else{v.pause();document.getElementById("play").textContent="▶";}
}
function stepLine(dir){
  const s=segs(); if(!s.length) return;
  const t=V().currentTime;
  let target=null;
  if(dir>0){ for(const x of s) if(x.start>t+0.05){target=x.start;break;} }
  else{ for(let i=s.length-1;i>=0;i--) if(s[i].start<t-0.25){target=s[i].start;break;} }
  if(target!=null){ V().currentTime=target; tick(); }
}

/* ======================= bảng bên phải ======================= */
function setTab(t){
  TAB=t;
  if(t==="tts") loadVoices(false);
  if(t==="manual"){ loadManualVoices(false); loadNhacNen(false); }
  document.querySelectorAll("#tabs .btn").forEach(b=>
    b.classList.toggle("on",b.dataset.t===t));
  renderPanel();
}
function fld(label,html){return `<div class="fld"><label>${label}</label>${html}</div>`;}
function rng(label,val,unit,min,max,step,fn){
  return `<div class="rng"><div class="top"><span>${label}</span><b>${val}${unit||""}</b></div>
    <input type="range" min="${min}" max="${max}" step="${step||1}" value="${val}"
      oninput="${fn}"></div>`;
}
function renderManualPanel(P){
  const eng=MANUAL.engine||"edge", vs=VOICES[eng]||[];
  const voiceOpts=vs.length
    ? vs.map(v=>`<option value="${esc(v.id)}" ${MANUAL.voice===v.id?"selected":""}>${v.status==="ok"?"✓ ":v.status==="failed"?"✕ ":""}${esc(v.name)}</option>`).join("")
    : `<option value="${esc(MANUAL.voice||"")}">${MANUAL.voice?esc(MANUAL.voice):"(đang nạp danh sách giọng…)"}</option>`;
  const audioPath=MANUAL.audio_path||"";
  const outputPath=MANUAL.output_path||"";
  const busy=!!(ST.manual&&ST.manual.working);
  const selectedVideo=PR&&PR.video ? PR.video : "";
  P.innerHTML=`<div class="sect">TẠO ÂM THANH TỪ VĂN BẢN</div>
    ${fld("Nội dung truyện / văn bản",`<textarea id="manualText"
      placeholder="Dán hoặc viết nội dung cần đọc tại đây…"
      oninput="MANUAL.text=this.value;updateManualCharCount()">${esc(MANUAL.text||"")}</textarea>`)}
    <div class="rowbtns" style="align-items:center;margin-top:8px">
      <button class="btn" ${busy?"disabled":""} onclick="pickManualText()">
        T&#7843;i file v&#259;n b&#7843;n</button>
      <span id="manualCharCount" style="color:var(--muted);font-size:11px;white-space:nowrap">
        ${(MANUAL.text||"").length.toLocaleString("vi-VN")} k&#253; t&#7921;</span>
    </div>
    <div class="hint">H&#7895; tr&#7907; file <b>TXT, MD</b> (UTF-8/UTF-16), t&#7889;i &#273;a
      200.000 k&#253; t&#7921; m&#7895;i l&#7847;n t&#7841;o audio.</div>
    <div class="grid2">
      ${fld("Tên file",`<input value="${esc(MANUAL.name||"")}" placeholder="ví dụ: Chuyện ngắn số 1"
        oninput="MANUAL.name=this.value">`)}
      ${fld("Bộ giọng",`<select onchange="setManualEngine(this.value)">
        <option value="edge" ${eng==="edge"?"selected":""}>edge-tts</option>
        <option value="vieneu" ${eng==="vieneu"?"selected":""}>VieNeu-TTS (offline)</option>
        <option value="capcut" ${eng==="capcut"?"selected":""}>CapCut TTS</option>
      </select>`)}
    </div>
    ${fld(`Giọng đọc &nbsp;<span style="color:var(--accent)">${vs.length} giọng</span>`,
      `<select onchange="MANUAL.voice=this.value">${voiceOpts}</select>`)}
    <div style="height:7px"></div>
    ${eng==="edge"?rng("Cao độ giọng",parseInt(MANUAL.pitch||"0")," Hz",-200,200,10,
      "MANUAL.pitch=(this.value>=0?'+':'')+this.value+'Hz';this.previousElementSibling.querySelector('b').textContent=this.value+' Hz'"):""}
    ${rng("Tốc độ đọc",parseInt(MANUAL.rate||"0"),"%",-30,50,5,
      "MANUAL.rate=(this.value>0?'+':'')+this.value+'%';this.previousElementSibling.querySelector('b').textContent=this.value+'%'")}
    ${nutNgheThu("story")}
    <div class="hint">Nghe thử đọc một câu bằng đúng giọng/tốc độ đang chọn (có văn bản
      thì đọc chính đoạn đầu của bạn), khỏi phải tạo cả file mới biết giọng có hợp không.</div>
    <button class="btn pri" style="width:100%;text-align:center;margin-bottom:12px" ${busy?"disabled":""}
      onclick="createManualAudio()">▶ Tạo file MP3</button>
    <div class="hint" id="manualStatus"><b>${esc(MANUAL.status||"Sẵn sàng")}</b>
      ${MANUAL.error?`<br><span style="color:var(--red)">${esc(MANUAL.error)}</span>`:""}
      ${audioPath?`<br>${MANUAL.audio_duration?fmt(MANUAL.audio_duration)+" · ":""}${esc(audioPath)}`:""}</div>
    ${audioPath?`<audio controls preload="metadata" style="width:100%;margin-bottom:12px"
      src="/api/manual/audio?v=${_manualRev}"></audio>`:""}
    <div class="rowbtns">
      <button class="btn" onclick="pickManualAudio()">Chọn audio có sẵn</button>
      <button class="btn" ${audioPath?"":"disabled"} onclick="openManualFolder('audio')">Mở thư mục audio</button>
    </div>

    <div class="sect" style="margin-top:17px">NHẠC NỀN</div>
    <div class="hint">Nhạc chỉ lấy loại <b>CC0 / Public Domain</b> nên dùng thương mại thoải mái.
      Nguồn từng bài ghi trong <b>assets/nhac_nen/nguon.json</b>; muốn dùng nhạc riêng thì
      cứ chép file vào thư mục đó.</div>
    ${fld("Bài nhạc",`<select onchange="MANUAL.nhac_bai=this.value">
      <option value="">— tự chọn ngẫu nhiên trong kho —</option>
      ${NHAC_LIST.map(x=>`<option value="${esc(x.ten)}" ${MANUAL.nhac_bai===x.ten?"selected":""}
        >${esc(x.ten)}${x.giay_phep?" · "+esc(x.giay_phep):""}</option>`).join("")}
    </select>`)}
    <div class="rowbtns" style="margin-bottom:8px">
      <button class="btn" ${busy?"disabled":""} onclick="taiNhacNen()">Tải thêm nhạc CC0</button>
      <button class="btn" onclick="loadNhacNen(true)">Làm mới danh sách</button>
      <span style="color:var(--muted);font-size:11px;align-self:center">${NHAC_LIST.length} bài trong máy</span>
    </div>
    ${rng("Mức nhạc nền",MANUAL.nhac_db," dB",-50,-20,1,
      "MANUAL.nhac_db=+this.value;this.previousElementSibling.querySelector('b').textContent=this.value+' dB'")}
    <div class="hint">-40 chỉ đủ lấp khoảng lặng, -35 nghe rõ hơn chút. Trên -30 là bắt đầu tranh với giọng đọc.</div>
    <label style="display:flex;gap:7px;align-items:center;margin:8px 0">
      <input type="checkbox" ${MANUAL.nhac_duck?"checked":""}
        onchange="MANUAL.nhac_duck=this.checked">
      <span>Nhạc tự nhỏ lại khi có lời (ducking)</span></label>
    <button class="btn pri" style="width:100%;text-align:center;margin-bottom:10px"
      ${(!audioPath||busy)?"disabled":""} onclick="tronNhacNen()">▶ Trộn nhạc nền vào giọng đọc</button>
    ${MANUAL.nhac_ten?`<div class="hint">Đang dùng nhạc: <b>${esc(MANUAL.nhac_ten)}</b></div>`:""}

    <div class="sect" style="margin-top:17px">DỰNG VIDEO TỪ ẢNH</div>
    <div class="hint">Dùng khi không có sẵn video. Ảnh được chia đều theo độ dài giọng đọc nên
      video ra luôn vừa khít. Ảnh lệch tỉ lệ khung hình sẽ đặt giữa, hai bên lấp bằng chính
      ảnh đó làm nền mờ.</div>
    ${fld("Ảnh hoặc thư mục ảnh (mỗi dòng một mục)",`<textarea id="manualAnh" style="min-height:60px"
      placeholder="Ví dụ: E:\\anh\\truyen1"
      oninput="MANUAL.anh=this.value">${esc(MANUAL.anh||"")}</textarea>`)}
    <div class="rowbtns" style="margin-bottom:8px">
      <button class="btn" ${busy?"disabled":""} onclick="pickAnh()">Chọn thư mục ảnh</button>
      ${fld("Kiểu hình",`<select onchange="MANUAL.slide_kieu=this.value">
        <option value="chuyen_dong" ${MANUAL.slide_kieu==="chuyen_dong"?"selected":""}>Ảnh trôi và phóng chậm</option>
        <option value="tinh" ${MANUAL.slide_kieu==="tinh"?"selected":""}>Ảnh đứng yên (dựng nhanh)</option>
      </select>`)}
    </div>
    <button class="btn pri" style="width:100%;text-align:center;margin-bottom:12px"
      ${(!audioPath||busy)?"disabled":""} onclick="dungVideoTuAnh()">▶ Dựng video từ ảnh</button>

    <div class="sect" style="margin-top:17px">GHÉP AUDIO VÀO VIDEO</div>
    <div class="hint">Video đang chọn: <b>${selectedVideo?esc(selectedVideo):"chưa chọn"}</b><br>
      Khi ghép, ứng dụng vẫn áp dụng phần cắt video, vùng mờ, logo, phụ đề và mức âm nền gốc
      trong tab <b>Xuất file</b>. Audio ngắn hơn sẽ được chèn lặng ở cuối; audio dài hơn video sẽ bị cắt.</div>
    <button class="btn pri" style="width:100%;text-align:center;margin-bottom:10px"
      ${(!JID||!audioPath||busy)?"disabled":""} onclick="muxManualAudio()">▶ Ghép audio vào video đang chọn</button>
    ${outputPath?`<button class="btn" style="width:100%;text-align:center"
      onclick="openManualFolder('output')">Mở video đã ghép</button>`:""}`;
}
function renderPanel(){
  const P=document.getElementById("panel");
  if(TAB==="manual"){ renderManualPanel(P); return; }
  if(!PR){ P.innerHTML=`<div class="hint">Chọn một video để bắt đầu chỉnh.</div>`; return; }
  const st=PR.sub_style||{}, op=PR.options||{};

  if(TAB==="sub"){
    const box=st.box||{x:0,y:0,w:PR.w,h:60};
    P.innerHTML=`<div class="sect">PHỤ ĐỀ TIẾNG VIỆT (LỚP 3)</div>
    <div class="grid2">
      ${fld("Font chữ",`<select onchange="setSt('font',this.value)">
        ${["Be Vietnam Pro","Arial","Segoe UI","Roboto","Tahoma","Times New Roman"]
          .map(f=>`<option ${st.font===f?"selected":""}>${f}</option>`).join("")}</select>`)}
      ${fld("Cỡ chữ",`<input type="number" min="8" max="200" value="${st.size||30}"
        onchange="setSt('size',+this.value)">`)}
    </div>
    <div class="grid2">
      ${fld("Màu chữ",`<div class="sw">${COLORS.map(c=>
        `<i class="${(st.color||"").toUpperCase()===c?"on":""}" style="background:${c}"
           onclick="setSt('color','${c}')"></i>`).join("")}</div>`)}
      ${fld("Viền / bóng",`<select onchange="setOutline(this.value)">
        <option value="2,1" ${st.outline==2?"selected":""}>Viền đen 2px + bóng</option>
        <option value="3,0" ${st.outline==3?"selected":""}>Viền đen 3px dày</option>
        <option value="1,0" ${st.outline==1?"selected":""}>Viền mảnh 1px</option>
        <option value="0,0" ${st.outline==0?"selected":""}>Không viền</option>
      </select>`)}
    </div>
    <div class="sect">VỊ TRÍ TRÊN KHUNG HÌNH</div>
    <div class="g9">${GRID.flat().map(g=>
      `<button class="${st.align===g?"on":""}" onclick="setSt('align','${g}')"></button>`).join("")}</div>
    ${rng("Vị trí ngang",Math.round(box.x+box.w/2)," px",0,PR.w,1,"setBoxCenter(+this.value,null)")}
    ${rng("Vị trí dọc",Math.round(box.y+box.h)," px",0,PR.h,1,"setBoxBottom(+this.value)")}
    <button class="btn" style="width:100%;text-align:center;margin-bottom:12px"
      onclick="resetSubBox()">Đưa về đáy giữa (mặc định)</button>
    <label class="chk2"><input type="checkbox" ${op.hardsub?"checked":""}
      onchange="setOpt('hardsub',this.checked)"> Ghi cứng phụ đề vào video</label>
    <label class="chk2"><input type="checkbox" ${op.export_srt?"checked":""}
      onchange="setOpt('export_srt',this.checked)"> Xuất kèm file .srt</label>
    <div id="lines"></div>`;
    renderLines();
  }

  else if(TAB==="blur"){
    const a=ACT&&ACT.kind==="rgn"?PR.regions[ACT.i]:null;
    P.innerHTML=`<div class="sect">LỚP 2 · CHE SUB GỐC / XOÁ LOGO</div>
    <div class="hint">Sub tiếng Trung <b>cháy cứng</b> trong hình (lớp 1) không xoá được,
      nên ta <b>phủ mờ</b> lên. Bấm <b>Tự dò sub cứng</b> để máy tự khoanh vùng,
      hoặc kéo–thả khung đỏ ngay trên video.</div>
    <div class="rowbtns">
      <button class="btn on" onclick="autoDetect()">✦ Tự dò</button>
      <button class="btn" onclick="addRegion('blur')">+ Vùng mờ</button>
      <button class="btn" onclick="addRegion('delogo')">+ Xoá logo</button>
    </div>
    ${(PR.regions||[]).length?"":`<div class="hint">Chưa có vùng nào.</div>`}
    ${(PR.regions||[]).map((r,i)=>`
      <div class="lrow ${ACT&&ACT.kind==='rgn'&&ACT.i===i?'cur':''}"
           onclick="ACT={kind:'rgn',i:${i}};draw();renderPanel()">
        <div class="lmeta"><span class="lid">${i+1}</span>
          ${r.type==="delogo"?"Xoá logo":"Làm mờ"} · ${r.w}×${r.h} @ (${r.x},${r.y})
          <span class="lplay" onclick="event.stopPropagation();PR.regions.splice(${i},1);ACT=null;save();draw();renderPanel()">xoá</span>
        </div>
        ${r.type==="delogo"?"":rng("Độ mờ",r.strength||20,"",4,60,1,
          `PR.regions[${i}].strength=+this.value;save();draw()`)}
        <div class="grid2" style="margin-top:7px">
          ${fld("Tu giay",`<input type="number" min="0" step="0.1" value="${r.start??""}"
            placeholder="0" onchange="setRgnAt(${i},'start',this.value)">`)}
          ${fld("Den giay",`<input type="number" min="0" step="0.1" value="${r.end??""}"
            placeholder="toan video" onchange="setRgnAt(${i},'end',this.value)">`)}
        </div>
        <div class="rowbtns" style="margin-top:7px">
          <button class="btn" onclick="event.stopPropagation();setRgnAt(${i},'start',V().currentTime)">Lay bat dau</button>
          <button class="btn" onclick="event.stopPropagation();setRgnAt(${i},'end',V().currentTime)">Lay ket thuc</button>
        </div>
      </div>`).join("")}
    ${a?`<div class="sect" style="margin-top:8px">TOẠ ĐỘ CHÍNH XÁC</div>
    <div class="grid2">
      ${fld("X",`<input type="number" value="${a.x}" onchange="setRgn('x',+this.value)">`)}
      ${fld("Y",`<input type="number" value="${a.y}" onchange="setRgn('y',+this.value)">`)}
      ${fld("Rộng",`<input type="number" value="${a.w}" onchange="setRgn('w',+this.value)">`)}
      ${fld("Cao",`<input type="number" value="${a.h}" onchange="setRgn('h',+this.value)">`)}
    </div>`:""}`;
  }

  else if(TAB==="logo"){
    const l=PR.logo;
    P.innerHTML=`<div class="sect">CHÈN LOGO CỦA BẠN</div>
    ${l?`${fld("Ảnh logo (.png nền trong)",`<input id="logopath" value="${esc(l.path||"")}"
        placeholder="chưa chọn ảnh" onchange="PR.logo.path=this.value;save()">`)}
      <button class="btn" style="width:100%;text-align:center;margin:7px 0 4px"
        onclick="pickLogo()">📂 Chọn ảnh logo…</button>
      <div style="height:11px"></div>
      ${rng("Độ mờ",Math.round((l.opacity||1)*100),"%",5,100,1,
        "PR.logo.opacity=+this.value/100;save();draw()")}
      <div class="grid2">
        ${fld("X",`<input type="number" value="${l.x}" onchange="PR.logo.x=+this.value;save();draw()">`)}
        ${fld("Y",`<input type="number" value="${l.y}" onchange="PR.logo.y=+this.value;save();draw()">`)}
        ${fld("Rộng",`<input type="number" value="${l.w}" onchange="PR.logo.w=+this.value;save();draw()">`)}
        ${fld("Cao",`<input type="number" value="${l.h}" onchange="PR.logo.h=+this.value;save();draw()">`)}
      </div>
      <button class="btn danger" style="width:100%" onclick="PR.logo=null;save();draw();renderPanel()">Bỏ logo</button>`
    :`<div class="hint">Chưa chèn logo. Bấm nút dưới rồi kéo khung tím tới vị trí mong muốn.</div>
      <button class="btn" style="width:100%;text-align:center" onclick="addLogoBox()">▤ Chèn logo</button>`}`;
  }

  else if(TAB==="cut"){
    const b=trimBounds();
    P.innerHTML=`<div class="sect">CẮT VIDEO THEO ĐOẠN GIỮ</div>
    <label class="chk2"><input type="checkbox" ${b.on?"checked":""}
      onchange="setTrimEnabled(this.checked)"> Bật cắt theo đoạn đang chọn</label>
    <div class="grid2">
      ${fld("Giữ từ",`<input value="${fmt(b.start)}"
        placeholder="00:00:30" onchange="setTrimPoint('start',this.value)">`)}
      ${fld("Giữ đến",`<input value="${fmt(b.end)}"
        placeholder="hết video" onchange="setTrimPoint('end',this.value)">`)}
    </div>
    <div class="rowbtns">
      <button class="btn" onclick="setTrimFromNow('start')">Lấy đầu</button>
      <button class="btn" onclick="setTrimFromNow('end')">Lấy cuối</button>
    </div>
    <div class="rowbtns">
      <button class="btn" onclick="seekTrim('start')">Tới đầu</button>
      <button class="btn" onclick="seekTrim('end')">Tới cuối</button>
    </div>
    <div class="hint">Đoạn xuất: <b>${fmt(b.duration)}</b> / video gốc ${fmt(b.full)}.
      Có thể kéo hai tay nắm ở track <b>ĐOẠN GIỮ</b> bên dưới video.</div>
    <button class="btn" style="width:100%;text-align:center;margin-bottom:9px"
      onclick="trimFull()">Dùng toàn bộ video</button>
    <button class="btn pri" style="width:100%;text-align:center"
      onclick="runAll()">▶ Chạy đoạn đã chọn</button>`;
  }

  else if(TAB==="asr"){
    P.innerHTML=`<div class="sect">NHẬN DẠNG PHỤ ĐỀ GỐC</div>
    <div class="hint">Cấu hình engine nằm trong <b>config.yaml</b> (mục <b>asr</b>).
      Mặc định <b>paraformer</b> cho tiếng Trung, tự dự phòng sang faster-whisper.
      Có sẵn <b>kiểm tra độ phủ</b> và <b>tự vá lỗ hổng</b> chống mất đoạn.</div>
    <button class="btn pri" style="width:100%;text-align:center"
      onclick="run(['asr'])">▶ Chạy nhận dạng</button>
    <div style="height:9px"></div>
    <div class="hint">Số dòng hiện có: <b>${segs().length}</b></div>`;
  }

  else if(TAB==="tr"){
    const tr=CFG.translation||{};
    const provider=tr.provider||"browser";
    const providerFields = provider==="nvidia" ? `
      <div class="grid2">
        ${fld("NVIDIA API key",`<input type="password" autocomplete="off"
          value="${esc(tr.nvidia_api_key||"")}" placeholder="nvapi-..."
          onchange="setTrCfg('nvidia_api_key',this.value)">`)}
        ${fld("Model",`<input value="${esc(tr.nvidia_model||"z-ai/glm-5.2")}"
          onchange="setTrCfg('nvidia_model',this.value)">`)}
      </div>
      ${fld("Base URL",`<input value="${esc(tr.nvidia_base_url||"https://integrate.api.nvidia.com/v1")}"
        onchange="setTrCfg('nvidia_base_url',this.value)">`)}
      ${fld("Timeout mỗi request",`<input type="number" min="60" step="30"
        value="${Number(tr.nvidia_timeout||420)}"
        onchange="setTrCfg('nvidia_timeout',Math.max(60,+this.value||420))">`)}
      <div class="hint">Key <b>nvapi-...</b> tạo FREE tại build.nvidia.com (không cần thẻ),
        MỘT key dùng cho mọi model trong catalog. Nên dùng <b>z-ai/glm-5.2</b> cho
        dịch Trung-Việt; muốn thử model khác chỉ cần đổi ô Model.</div>`
    : provider==="tokenrouter" ? `
      <div class="grid2">
        ${fld("TokenRouter API key",`<input type="password" autocomplete="off"
          value="${esc(tr.tokenrouter_api_key||"")}" placeholder="tr_..."
          onchange="setTrCfg('tokenrouter_api_key',this.value)">`)}
        ${fld("Model",`<input value="${esc(tr.tokenrouter_model||"moonshotai/kimi-k3-free")}"
          onchange="setTrCfg('tokenrouter_model',this.value)">`)}
      </div>
      ${fld("Base URL",`<input value="${esc(tr.tokenrouter_base_url||"https://api.tokenrouter.com/v1")}"
        onchange="setTrCfg('tokenrouter_base_url',this.value)">`)}
      ${fld("Timeout mỗi request",`<input type="number" min="60" step="30"
        value="${Number(tr.tokenrouter_timeout||420)}"
        onchange="setTrCfg('tokenrouter_timeout',Math.max(60,+this.value||420))">`)}
      <div class="hint">TokenRouter dùng chuẩn OpenAI-compatible /v1/chat/completions. Mặc định là <b>moonshotai/kimi-k3-free</b>.</div>`
    : provider==="inferx" ? `
      <div class="grid2">
        ${fld("InferX API key",`<input type="password" autocomplete="off"
          value="${esc(tr.inferx_api_key||"")}" placeholder="ix_..."
          onchange="setTrCfg('inferx_api_key',this.value)">`)}
        ${fld("InferX model",`<input value="${esc(tr.inferx_model||"deepseek-v4-flash")}"
          onchange="setTrCfg('inferx_model',this.value)">`)}
      </div>
      ${fld("InferX Base URL",`<input value="${esc(tr.inferx_base_url||"https://model.inferx.net/endpoints/v1")}"
        onchange="setTrCfg('inferx_base_url',this.value)">`)}
      ${fld("Timeout mỗi request",`<input type="number" min="60" step="30"
        value="${Number(tr.inferx_timeout||420)}"
        onchange="setTrCfg('inferx_timeout',Math.max(60,+this.value||420))">`)}
      <div class="hint">InferX dùng endpoint OpenAI-compatible /chat/completions, model mặc định <b>deepseek-v4-flash</b>.</div>`
    : provider==="tokenrouter_gemini" ? `
      <div class="grid2">
        ${fld("TokenRouter Gemini key",`<input type="password" autocomplete="off"
          value="${esc(tr.tokenrouter_gemini_api_key||"")}" placeholder="sk-..."
          onchange="setTrCfg('tokenrouter_gemini_api_key',this.value)">`)}
        ${fld("Gemini model",`<input value="${esc(tr.tokenrouter_gemini_model||"google/gemini-3.6-flash")}"
          onchange="setTrCfg('tokenrouter_gemini_model',this.value)">`)}
      </div>
      ${fld("Gemini Base URL",`<input value="${esc(tr.tokenrouter_gemini_base_url||"https://api.tokenrouter.com/v1beta/models")}"
        onchange="setTrCfg('tokenrouter_gemini_base_url',this.value)">`)}
      ${fld("Timeout mỗi request",`<input type="number" min="60" step="30"
        value="${Number(tr.tokenrouter_gemini_timeout||420)}"
        onchange="setTrCfg('tokenrouter_gemini_timeout',Math.max(60,+this.value||420))">`)}
      <div class="hint">Dùng endpoint native Gemini của TokenRouter: /v1beta/models/google/gemini-3.6-flash:generateContent.</div>`
    : provider==="gemini" ? `
      <div class="grid2">
        ${fld("Gemini API key",`<input type="password" autocomplete="off"
          value="${esc(tr.gemini_api_key||"")}" placeholder="AIza..."
          onchange="setTrCfg('gemini_api_key',this.value)">`)}
        ${fld("Gemini model",`<input value="${esc(tr.gemini_model||"gemini-3.6-flash")}"
          onchange="setTrCfg('gemini_model',this.value)">`)}
      </div>`
    : `<div class="hint">Dùng Gemini qua trình duyệt Edge/Chrome đã đăng nhập, không cần API key.</div>`;

    P.innerHTML=`<div class="sect">DỊCH SANG TIẾNG VIỆT</div>
    <div class="grid2">
      ${fld("Chế độ dịch",`<select onchange="setTrCfg('provider',this.value,true)">
        <option value="browser" ${provider==="browser"?"selected":""}>browser - Gemini qua trình duyệt</option>
        <option value="gemini" ${provider==="gemini"?"selected":""}>gemini - API key</option>
        <option value="nvidia" ${provider==="nvidia"?"selected":""}>nvidia - GLM-5.2 free (NIM)</option>
        <option value="inferx" ${provider==="inferx"?"selected":""}>inferx - DeepSeek V4 Flash</option>
        <option value="tokenrouter_gemini" ${provider==="tokenrouter_gemini"?"selected":""}>tokenrouter - Gemini 3.6 Flash</option>
        <option value="tokenrouter" ${provider==="tokenrouter"?"selected":""}>tokenrouter - Kimi K3 free</option>
      </select>`)}
      ${fld("Số dòng mỗi lượt",`<input type="number" min="1" step="1"
        value="${Number(tr.chunk_size||80)}"
        onchange="setTrCfg('chunk_size',Math.max(1,+this.value||80))">`)}
      ${fld("Nhịp ký tự / giây",`<input type="number" min="0" step="1"
        value="${Number(tr.chars_per_sec??14)}"
        onchange="setTrCfg('chars_per_sec',Math.max(0,+this.value||0))">`)}
    </div>
    ${providerFields}
    <div class="grid2">
      ${fld("Ép tên nam chính",`<input value="${esc(tr.male_lead_name||"")}"
        placeholder="để trống = không ép"
        onchange="setTrCfg('male_lead_name',this.value)">`)}
      ${fld("Ép tên nữ chính",`<input value="${esc(tr.female_lead_name||"")}"
        placeholder="để trống = không ép"
        onchange="setTrCfg('female_lead_name',this.value)">`)}
    </div>
    <div class="rowbtns">
      <button class="btn" onclick="testTrCfg()">Test API</button>
      <button class="btn" onclick="saveTrCfg(true)">Lưu cấu hình dịch</button>
    </div>
    <button class="btn pri" style="width:100%;text-align:center"
      onclick="run(['translate'])">▶ Chạy dịch</button>
    <div style="height:9px"></div>
    <div class="hint">Đã dịch: <b>${segs().filter(s=>s.vi&&s.vi.trim()).length}</b>
      / ${segs().length} dòng</div>`;
  }

  else if(TAB==="tts"){
    const eng=op.engine||"edge";
    const vs=VOICES[eng]||[];
    const cur=op.narrator_voice||"";
    const opts=vs.length
      ? vs.map(v=>`<option value="${esc(v.id)}" ${cur===v.id?"selected":""}>${esc(v.name)}</option>`).join("")
      : `<option value="">(chưa lấy được danh sách giọng)</option>`;
    P.innerHTML=`<div class="sect">GIỌNG ĐỌC TIẾNG VIỆT</div>
    <div class="grid2">
      ${fld("Bộ giọng (engine)",`<select onchange="setEngine(this.value)">
        <option value="edge" ${eng==="edge"?"selected":""}>edge-tts (nhanh, cần mạng)</option>
        <option value="vieneu" ${eng==="vieneu"?"selected":""}>VieNeu-TTS (offline, tự nhiên hơn)</option>
        <option value="capcut" ${eng==="capcut"?"selected":""}>CapCut TTS (online, nhiều giọng)</option>
      </select>`)}
      ${fld("Kiểu giọng",`<select onchange="setOpt('voice_mode',this.value)">
        <option value="narrator" ${op.voice_mode==="narrator"?"selected":""}>1 giọng kể</option>
        <option value="alternate" ${op.voice_mode==="alternate"?"selected":""}>Nam/nữ luân phiên</option>
        <option value="per-speaker" ${op.voice_mode==="per-speaker"?"selected":""}>Mỗi nhân vật 1 giọng</option>
      </select>`)}
    </div>
    ${fld(`Giọng chính &nbsp;<span style="color:var(--accent)">${vs.length} giọng</span>`,
      `<select onchange="setOpt('narrator_voice',this.value)">${opts}</select>`)}
    <div style="height:8px"></div>
    <div class="rowbtns">
      <button class="btn" onclick="loadVoices(true)">⟳ Nạp lại danh sách giọng</button>
      ${eng==="vieneu"?`<button class="btn on" onclick="prefetch()">⬇ Tải model về máy</button>`:""}
    </div>
    ${nutNgheThu("dub")}
    ${eng==="vieneu"&&!vs.length?`<div class="hint">Chưa thấy giọng nào. Model
      VieNeu-TTS chưa tải xong — bấm <b>Tải model về máy</b> (vài GB, chỉ một
      lần), hoặc chạy <b>tai_model.bat</b>. Trong lúc chờ vẫn dùng được
      <b>edge-tts</b>.</div>`:""}
    ${eng==="capcut"?`<div class="hint">CapCut TTS dùng API online, nhiều giọng hơn nhưng có thể bị queue/rate-limit.
      Nếu mạng hoặc CapCut lỗi, file <b>tts_loi.txt</b> sẽ ghi rõ dòng hỏng.</div>`:""}
    ${rng("Cao độ giọng (pitch)",parseInt(op.narrator_pitch||"0")," Hz",-200,200,10,
      "setOpt('narrator_pitch',(this.value>=0?'+':'')+this.value+'Hz')")}
    <div class="hint">Tăng <b>pitch</b> = giọng cao hơn (giọng trẻ, nữ). Giảm = giọng trầm hơn (giọng già, nam).
      Chỉ có hiệu lực với engine <b>edge-tts</b>.</div>
    ${rng("Tốc độ nói nền",parseInt(op.base_rate||"+0%"),"%",-30,50,5,
      "setOpt('base_rate',(this.value>0?'+':'')+this.value+'%')")}
    ${rng("Trần tăng tốc chống đè",(op.max_speed||1.6).toFixed(2),"×",1,2.5,.05,
      "setOpt('max_speed',+this.value)")}
    ${rng("Khoảng nghỉ giữa câu",(op.min_gap||.08).toFixed(2)," s",0,.6,.01,
      "setOpt('min_gap',+this.value)")}
    ${rng("Được đọc quá cuối câu",(op.max_overhang_seconds??.75).toFixed(2)," s",0,2,.05,
      "setOpt('max_overhang_seconds',+this.value)")}
    <div class="hint">Ở chế độ strict, mỗi câu luôn bắt đầu đúng timestamp gốc. Câu Việt dài
      được tăng tốc vừa đủ và chỉ được đọc quá mốc kết thúc tối đa mức trên; đặt <b>0 giây</b>
      nếu muốn bám chặt hình, nhưng câu quá dài sẽ phải cắt nhiều hơn.</div>
    <button class="btn pri" style="width:100%;text-align:center"
      onclick="run(['tts'])">▶ Dựng giọng đọc</button>`;
  }

  else if(TAB==="export"){
    const legacyVol=op.keep_original_volume==null?null:+op.keep_original_volume;
    const origDb=op.keep_original_db!=null ? +op.keep_original_db
      : legacyVol!=null&&legacyVol>0 ? Math.round(20*Math.log10(legacyVol/10)) : -30;
    const origMuted=!!op.keep_original_muted ||
      (op.keep_original_db==null && (legacyVol==null || legacyVol<=0));
    P.innerHTML=`<div class="sect">XUẤT FILE</div>
    <label class="chk2"><input type="checkbox" ${op.use_gpu?"checked":""}
      onchange="setOpt('use_gpu',this.checked)"> Dùng GPU (NVENC) cho nhanh</label>
    <label class="chk2"><input type="checkbox" ${op.hardsub?"checked":""}
      onchange="setOpt('hardsub',this.checked)"> Ghi cứng phụ đề Việt</label>
    <label class="chk2"><input type="checkbox" ${op.render_chunked?"checked":""}
      onchange="setOpt('render_chunked',this.checked)"> Chia render rồi tự ghép lại</label>
    ${fld("Mỗi phần render",`<input type="number" min="10" step="10"
      value="${op.render_chunk_minutes||120}"
      onchange="setOpt('render_chunk_minutes',Math.max(10,+this.value||120))"> phút`)}
    <label class="chk2"><input type="checkbox" ${op.export_srt?"checked":""}
      onchange="setOpt('export_srt',this.checked)"> Xuất kèm .srt</label>
    <div style="height:8px"></div>
    <label class="chk2"><input type="checkbox" ${origMuted?"checked":""}
      onchange="setOriginalMuted(this.checked)"> Tắt hẳn âm thanh gốc</label>
    ${rng("Âm lượng nền gốc",Math.max(-60,Math.min(0,Math.round(origDb)))," dB",-60,0,1,
      "setOriginalDb(+this.value)")}
    <div class="hint"><b>-60 dB</b> = gần như im, <b>-40 dB</b> = rất nhỏ,
      <b>-30 dB</b> = nền nhỏ (khuyến nghị), <b>-20 dB</b> vẫn có thể nghe khá rõ,
      <b>0 dB</b> = giữ nguyên âm lượng gốc.</div>
    <div style="height:4px"></div>
    ${rng("Chất lượng (thấp = nét hơn)",op.crf||20,"",14,30,1,"setOpt('crf',+this.value)")}
    <div class="hint">Không có vùng phủ nào và tắt ghi cứng phụ đề →
      video được <b>copy nguyên luồng hình</b>, xuất gần như tức thì.</div>
    <button class="btn" style="width:100%;text-align:center;margin-bottom:9px"
      onclick="cleanupTemp()">Dọn file tạm (_tmp)</button>
    <button class="btn pri" style="width:100%;text-align:center"
      onclick="run(['render'])">▶ Xuất video</button>`;
  }
}
async function pickLogo(){
  if(!DESK()) return toast("Dán đường dẫn ảnh vào ô phía trên","warn");
  const p=await pywebview.api.pick_image();
  if(!p||p.error) return;
  PR.logo.path=p; save(); renderPanel(); draw();
  toast("Đã chọn logo","ok");
}
const VOICES={edge:[],vieneu:[],capcut:[]};
const VOICE_RECS={analysis:null,items:[],loading:false,engine:""};
let VOICE_LIBRARY_OPEN=false;
async function loadVoices(force){
  const eng=(PR&&PR.options&&PR.options.engine)||"edge";
  if(!force && VOICES[eng] && VOICES[eng].length) return;
  try{
    const r=await api("/api/voices?engine="+encodeURIComponent(eng));
    VOICES[eng]=r.voices||[];
    if(force) toast(`Có ${VOICES[eng].length} giọng cho ${eng}`, VOICES[eng].length?"ok":"warn");
  }catch(e){ if(force) toast(e.message,"err"); }
  if(TAB==="tts") renderPanel();
}
function setEngine(v){
  PR.options.engine=v;
  const vs=VOICES[v]||[];
  if(vs.length) PR.options.narrator_voice=vs[0].id;   // giọng cũ có thể không thuộc engine mới
  save(); renderPanel(); loadVoices(false);
}
async function loadManualVoices(force){
  const eng=MANUAL.engine||"edge";
  if(!force && VOICES[eng] && VOICES[eng].length){
    if(!VOICES[eng].some(v=>v.id===MANUAL.voice)) MANUAL.voice=VOICES[eng][0].id;
    return;
  }
  try{
    const r=await api("/api/voices?engine="+encodeURIComponent(eng));
    VOICES[eng]=r.voices||[];
    if(VOICES[eng].length && !VOICES[eng].some(v=>v.id===MANUAL.voice))
      MANUAL.voice=VOICES[eng][0].id;
    if(force) toast(`Có ${VOICES[eng].length} giọng cho ${eng}`,
                    VOICES[eng].length?"ok":"warn");
  }catch(e){ if(force) toast(e.message,"err"); }
  if(MODE==="story") renderStory();
}
function setManualEngine(v){
  MANUAL.engine=v;
  const vs=VOICES[v]||[];
  MANUAL.voice=vs.length?vs[0].id:"";
  rerenderMode(); loadManualVoices(false);
}
function voiceRecommendationHtml(eng,vs,busy){
  const a=VOICE_RECS.analysis, items=VOICE_RECS.engine===eng?VOICE_RECS.items:[];
  const summary=a?`<div class="hint" style="margin-top:7px"><b>Phân tích:</b>
    ${esc(a.genre_label||"đời thường")} · ${(a.word_count||0).toLocaleString("vi-VN")} từ
    · khoảng ${a.estimated_minutes||0} phút · ${a.dialogue_ratio||0}% đoạn đối thoại.</div>`:"";
  const cards=items.length?`<div style="display:grid;gap:6px;margin-top:7px">
    ${items.map((r,i)=>`<div style="border:1px solid var(--line);border-radius:7px;padding:7px">
      <div style="display:flex;gap:6px;align-items:center"><b style="flex:1">${i+1}. ${esc(r.name)}</b>
        <button class="btn sm" onclick="chooseRecommendedVoice(${i},false)">Chọn</button>
        <button class="btn sm" ${busy?"disabled":""} onclick="chooseRecommendedVoice(${i},true)">▶ Nghe</button></div>
      <div class="hint" style="margin-top:3px">${esc((r.reasons||[]).join(" · "))}</div>
    </div>`).join("")}</div>`:"";
  const library=VOICE_LIBRARY_OPEN?`<div style="max-height:210px;overflow:auto;display:grid;gap:4px;margin-top:7px">
    ${vs.map((v,i)=>`<button class="btn sm ${MANUAL.voice===v.id?"pri":""}"
      style="text-align:left;${v.status==="failed"?"opacity:.62":""}" ${busy?"disabled":""}
      title="${esc(v.status_error||"")}" onclick="previewVoiceAt(${i})">▶ ${v.status==="ok"?"✓ ":v.status==="failed"?"✕ ":""}${esc(v.name)}</button>`).join("")}
    ${vs.length?"":`<div class="hint">Đang nạp catalog giọng…</div>`}</div>`:"";
  return `<label style="display:flex;gap:7px;align-items:center;margin-top:7px">
      <input type="checkbox" ${STORY.auto_voice?"checked":""}
        onchange="STORY.auto_voice=this.checked;localStorage.setItem('advn_auto_voice',this.checked?'1':'0')">
      <span>Tự phân tích truyện và chọn giọng số 1 khi chạy từ tiêu đề</span></label>
    <div class="rowbtns" style="margin-top:7px">
      <button class="btn" ${busy||VOICE_RECS.loading?"disabled":""} onclick="analyseStoryVoice()">
        ${VOICE_RECS.loading?"Đang phân tích…":"✨ Phân tích & đề xuất giọng"}</button>
      <button class="btn" onclick="VOICE_LIBRARY_OPEN=!VOICE_LIBRARY_OPEN;renderStoryPanel()">
        🎧 ${VOICE_LIBRARY_OPEN?"Đóng":"Mở"} thư viện ${vs.length} giọng</button>
    </div>${summary}${cards}${library}`;
}
async function analyseStoryVoice(){
  const ta=document.getElementById("manualText");
  if(ta) MANUAL.text=ta.value;
  if(!String(MANUAL.text||"").trim()) return toast("Hãy nạp nội dung truyện trước","warn");
  VOICE_RECS.loading=true; renderStoryPanel();
  try{
    const r=await api("/api/story/voice_recommendations",{
      text:MANUAL.text,engine:MANUAL.engine||"capcut"});
    VOICE_RECS.analysis=r.analysis||null;
    VOICE_RECS.items=r.recommendations||[];
    VOICE_RECS.engine=r.engine||MANUAL.engine;
    toast(`Đã phân tích và chọn ra ${VOICE_RECS.items.length} giọng phù hợp`,"ok");
  }catch(e){ toast(e.message||String(e),"err"); }
  finally{ VOICE_RECS.loading=false; renderStoryPanel(); }
}
async function chooseRecommendedVoice(i,play){
  const r=VOICE_RECS.items[i]; if(!r) return;
  MANUAL.engine=r.engine||VOICE_RECS.engine||MANUAL.engine;
  await loadManualVoices(false);
  MANUAL.voice=r.id;
  renderStoryPanel();
  if(play) ngheThuGiong("story");
}
function previewVoiceAt(i){
  const vs=VOICES[MANUAL.engine||"edge"]||[], v=vs[i]; if(!v) return;
  MANUAL.voice=v.id; renderStoryPanel(); ngheThuGiong("story");
}

/* ---------- nghe thử giọng ----------
   Đối tượng Audio giữ ở biến JS, KHÔNG đặt trong panel: panel bị vẽ lại mỗi
   khi trạng thái đổi (1,2 giây một lần), thẻ <audio> nằm trong đó sẽ bị xoá
   giữa lúc đang phát. */
let _NT_AUDIO=null, _NT_URL="", _NT_BUSY=false;
function ngheThuTrangThai(msg,mau){
  document.querySelectorAll(".nthu").forEach(el=>{
    el.textContent=msg||"";
    el.style.color=mau||"var(--muted)";
  });
}
function ngheThuDung(){
  if(_NT_AUDIO){ try{_NT_AUDIO.pause();}catch(e){} }
  if(_NT_URL){ URL.revokeObjectURL(_NT_URL); _NT_URL=""; }
  _NT_AUDIO=null;
}
async function ngheThuGiong(nguon){
  if(_NT_BUSY) return;
  const dub=(nguon==="dub");
  const op=(PR&&PR.options)||{};
  const o=dub
    ? {engine:op.engine||"edge", voice:op.narrator_voice||"",
       pitch:op.narrator_pitch||"+0Hz", rate:op.base_rate||"+0%", text:""}
    : {engine:MANUAL.engine||"edge", voice:MANUAL.voice||"",
       pitch:MANUAL.pitch||"+0Hz", rate:MANUAL.rate||"+0%",
       text:String(MANUAL.text||"").slice(0,400)};
  if(!o.voice) return toast("Chưa chọn giọng đọc","warn");
  ngheThuDung();
  _NT_BUSY=true;
  ngheThuTrangThai("Đang đọc thử… (vài giây)");
  try{
    const r=await fetch("/api/nghe_thu?"+new URLSearchParams(o).toString());
    if(!r.ok){
      let msg="Không tạo được bản nghe thử";
      try{ msg=(await r.json()).error||msg; }catch(e){}
      throw new Error(msg);
    }
    _NT_URL=URL.createObjectURL(await r.blob());
    _NT_AUDIO=new Audio(_NT_URL);
    _NT_AUDIO.onended=()=>ngheThuTrangThai(
      o.text?"Xong — đó là giọng đọc truyện của bạn":"Xong — bấm lại để nghe lần nữa");
    await _NT_AUDIO.play();
    ngheThuTrangThai("Đang phát…","var(--accent)");
  }catch(e){
    toast(e.message||String(e),"err");
    ngheThuTrangThai("");
  }finally{ _NT_BUSY=false; }
}
function nutNgheThu(nguon){
  return `<div class="rowbtns" style="align-items:center;margin:6px 0 4px">
    <button class="btn" onclick="ngheThuGiong('${nguon}')">🔊 Nghe giọng đang chọn</button>
    <button class="btn sm" style="flex:0 0 auto" title="Dừng phát"
      onclick="ngheThuDung();ngheThuTrangThai('')">■</button></div>
  <div class="nthu" style="font-size:11px;color:var(--muted);margin:0 0 8px"></div>`;
}
function updateManualCharCount(){
  const n=(MANUAL.text||"").length;
  const out=document.getElementById("manualCharCount");
  if(out){
    out.textContent=n.toLocaleString("vi-VN")+" k\u00fd t\u1ef1";
    out.style.color=n>200000?"var(--red)":"var(--muted)";
  }
}
function applyManualTextFile(file){
  const text=String((file&&file.text)||"").replace(/^\uFEFF/,"");
  if(!text.trim()) return toast("File v\u0103n b\u1ea3n \u0111ang tr\u1ed1ng","warn");
  if(text.length>200000)
    return toast("V\u0103n b\u1ea3n v\u01b0\u1ee3t qu\u00e1 200.000 k\u00fd t\u1ef1; h\u00e3y chia th\u00e0nh nhi\u1ec1u file","warn");
  MANUAL.text=text;
  if(!String(MANUAL.name||"").trim() && file.name) MANUAL.name=file.name;
  MANUAL.status=`\u0110\u00e3 n\u1ea1p ${file.filename||"file v\u0103n b\u1ea3n"} \u00b7 ${text.length.toLocaleString("vi-VN")} k\u00fd t\u1ef1`;
  MANUAL.error="";
  rerenderMode();
  toast("\u0110\u00e3 n\u1ea1p n\u1ed9i dung t\u1eeb file","ok");
}
async function pickManualText(){
  if(DESK()){
    try{
      const r=await pywebview.api.pick_text();
      if(!r) return;
      if(r.error) return toast(r.error,"err");
      applyManualTextFile(r);
    }catch(e){ toast(e.message||String(e),"err"); }
    return;
  }

  const input=document.createElement("input");
  input.type="file";
  input.accept=".txt,.md,text/plain,text/markdown";
  input.onchange=async()=>{
    const f=input.files&&input.files[0];
    if(!f) return;
    if(f.size>10*1024*1024) return toast("File v\u0103n b\u1ea3n qu\u00e1 l\u1edbn (t\u1ed1i \u0111a 10 MB)","warn");
    try{
      const text=await f.text();
      applyManualTextFile({text,name:f.name.replace(/\.[^.]+$/,""),filename:f.name});
    }catch(e){ toast("Kh\u00f4ng \u0111\u1ecdc \u0111\u01b0\u1ee3c file v\u0103n b\u1ea3n","err"); }
  };
  input.click();
}
async function createManualAudio(){
  const ta=document.getElementById("manualText");
  if(ta) MANUAL.text=ta.value;
  if(!MANUAL.text.trim()) return toast("Hãy nhập văn bản cần đọc","warn");
  try{
    await api("/api/manual/tts",{
      text:MANUAL.text,name:MANUAL.name,engine:MANUAL.engine,
      voice:MANUAL.voice,pitch:MANUAL.pitch,rate:MANUAL.rate
    });
    ST.manual={...(ST.manual||{}),working:true};
    MANUAL.status="Đang tổng hợp giọng…"; MANUAL.error="";
    rerenderMode(); toast("Đã bắt đầu tạo file MP3","ok");
  }catch(e){ toast(e.message,"err"); }
}
async function pickManualAudio(){
  let path="";
  if(DESK()){
    const r=await pywebview.api.pick_audio();
    if(r&&r.error) return toast(r.error,"err");
    path=Array.isArray(r)?(r[0]||""):(r||"");
  }else{
    path=prompt("Dán đường dẫn file MP3/WAV/M4A:")||"";
  }
  if(!path) return;
  try{
    const r=await api("/api/manual/use_audio",{path});
    MANUAL.audio_path=r.path||path;
    MANUAL.audio_duration=+r.duration||0;
    MANUAL.output_path="";
    MANUAL.status="Đã chọn audio có sẵn";
    rerenderMode(); toast("Đã chọn file âm thanh","ok");
  }catch(e){ toast(e.message,"err"); }
}
async function muxManualAudio(){
  if(!JID||!PR) return toast("Hãy chọn video cần ghép","warn");
  if(!MANUAL.audio_path) return toast("Hãy tạo hoặc chọn audio trước","warn");
  try{
    await saveNow();
    await api("/api/manual/mux",{id:JID,audio_path:MANUAL.audio_path});
    ST.manual={...(ST.manual||{}),working:true};
    MANUAL.status="Đang xuất video…"; MANUAL.error="";
    rerenderMode(); toast("Đã bắt đầu ghép audio vào video","ok");
  }catch(e){ toast(e.message,"err"); }
}
async function loadNhacNen(thongBao){
  try{
    const r=await fetch("/api/nhac_nen").then(x=>x.json());
    if(r.error) throw new Error(r.error);
    NHAC_LIST=r.bai||[];
    rerenderMode();
    if(thongBao) toast(`Kho nhạc có ${NHAC_LIST.length} bài`,"ok");
  }catch(e){ if(thongBao) toast(e.message||String(e),"err"); }
}
async function taiNhacNen(){
  try{
    await api("/api/nhac_nen/tai",{so_bai:(NHAC_LIST.length||0)+3});
    toast("Đang tìm và tải nhạc CC0… xem thanh tiến độ dưới cùng","ok");
    setTimeout(()=>loadNhacNen(true),9000);
  }catch(e){ toast(e.message,"err"); }
}
async function tronNhacNen(){
  if(!MANUAL.audio_path) return toast("Hãy tạo giọng đọc trước","warn");
  try{
    await api("/api/manual/nhac_nen",{
      audio_path:MANUAL.audio_path,bai:MANUAL.nhac_bai,
      muc_db:MANUAL.nhac_db,duck:MANUAL.nhac_duck
    });
    ST.manual={...(ST.manual||{}),working:true};
    MANUAL.status="Đang trộn nhạc nền…"; MANUAL.error="";
    rerenderMode(); toast("Đã bắt đầu trộn nhạc nền","ok");
  }catch(e){ toast(e.message,"err"); }
}
async function pickAnh(){
  let path="";
  if(DESK()){
    try{
      const r=await pywebview.api.pick_folder();
      if(r&&r.error) return toast(r.error,"err");
      path=Array.isArray(r)?(r[0]||""):(r||"");
    }catch(e){ return toast(e.message||String(e),"err"); }
  }else{
    path=prompt("Dán đường dẫn thư mục chứa ảnh:")||"";
  }
  if(!path) return;
  MANUAL.anh=(MANUAL.anh?MANUAL.anh.replace(/\s+$/,"")+"\n":"")+path;
  renderPanel();
}
async function dungVideoTuAnh(){
  const ta=document.getElementById("manualAnh");
  if(ta) MANUAL.anh=ta.value;
  const list=String(MANUAL.anh||"").split(/[\r\n]+/).map(s=>s.trim()).filter(Boolean);
  if(!list.length) return toast("Hãy chọn ảnh hoặc thư mục ảnh","warn");
  if(!MANUAL.audio_path) return toast("Hãy tạo giọng đọc trước","warn");
  try{
    await api("/api/manual/slideshow",{
      audio_path:MANUAL.audio_path,anh:list,
      kieu:MANUAL.slide_kieu,name:MANUAL.name
    });
    ST.manual={...(ST.manual||{}),working:true};
    MANUAL.status="Đang dựng video từ ảnh…"; MANUAL.error="";
    rerenderMode(); toast("Đã bắt đầu dựng video từ ảnh","ok");
  }catch(e){ toast(e.message,"err"); }
}
async function openManualFolder(kind){
  const path=kind==="output"?MANUAL.output_path:MANUAL.audio_path;
  if(!path) return;
  await openOut(path);
}
async function prefetch(){
  try{ await api("/api/prefetch",{}); toast("Đang tải model về máy… xem thanh tiến độ dưới cùng"); }
  catch(e){ toast(e.message,"err"); }
}
async function cleanupTemp(){
  try{
    const r=await api("/api/cleanup_temp",{});
    const gb=((r.bytes||0)/1073741824).toFixed(2);
    const free=((r.free||0)/1073741824).toFixed(2);
    toast(`Đã dọn ${r.files||0} file tạm, giải phóng ${gb} GB. Còn trống ${free} GB.`,"ok");
  }catch(e){ toast(e.message,"err"); }
}
function setSt(k,v){ PR.sub_style[k]=v; save(); draw(); renderPanel(); }
function setOutline(v){
  const [o,s]=v.split(",").map(Number);
  PR.sub_style.outline=o; PR.sub_style.shadow=s; save(); draw(); renderPanel();
}
function setOpt(k,v){ PR.options[k]=v; save(); renderPanel(); }
function setOriginalDb(v){
  if(!PR) return;
  PR.options.keep_original_db=Math.max(-60,Math.min(0,+v||-60));
  PR.options.keep_original_muted=false;
  PR.options.keep_original_volume=null;
  save(); renderPanel();
}
function setOriginalMuted(on){
  if(!PR) return;
  PR.options.keep_original_muted=!!on;
  PR.options.keep_original_volume=null;
  if(!on && PR.options.keep_original_db==null) PR.options.keep_original_db=-30;
  save(); renderPanel();
}
function setRgn(k,v){ if(ACT&&ACT.kind==="rgn"){PR.regions[ACT.i][k]=v;save();draw();renderPanel();} }
function setRgnAt(i,k,v){
  if(!PR||!PR.regions||!PR.regions[i]) return;
  if(v===""||v===null||Number.isNaN(Number(v))) delete PR.regions[i][k];
  else PR.regions[i][k]=Math.max(0,Math.round(Number(v)*10)/10);
  save(); draw(); renderPanel();
}
function setBoxCenter(cx){
  const b=PR.sub_style.box; b.x=Math.max(0,Math.min(PR.w-b.w,Math.round(cx-b.w/2)));
  save(); draw(); renderPanel();
}
function setBoxBottom(y){
  const b=PR.sub_style.box; b.y=Math.max(0,Math.min(PR.h-b.h,Math.round(y-b.h)));
  save(); draw(); renderPanel();
}
function resetSubBox(){
  PR.sub_style.box={x:Math.round(PR.w*0.08),y:Math.round(PR.h*0.44),
                    w:Math.round(PR.w*0.84),h:Math.round(PR.h*0.12)};
  PR.sub_style.align="mid-center"; save(); draw(); renderPanel();
}

/* ---------- sửa từng dòng ---------- */
function renderLines(){
  const el=document.getElementById("lines"); if(!el) return;
  const s=segs();
  if(!s.length){ el.innerHTML=`<div class="hint">Chưa có phụ đề.
    Sang thẻ <b>Nhận dạng</b> để bốc sub từ video.</div>`; return; }
  const ci=curIndex(), from=Math.max(0,ci-2);
  el.innerHTML=`<div class="lhd"><div class="sect" style="margin:0">SỬA TỪNG DÒNG</div>
    <span class="stat">${s.length} dòng</span></div>`+
    s.slice(from,from+14).map((x,k)=>{const i=from+k;return `
    <div class="lrow ${i===ci?"cur":""}" data-i="${i}">
      <div class="lmeta"><span class="lid">${String(i+1).padStart(4,"0")}</span>
        ${fmtms(x.start)} — ${fmtms(x.end)}
        <span class="lplay" onclick="V_seek(${x.start})">▶ nghe</span></div>
      ${x.src?`<div class="lsrc" title="${esc(x.src)}">${esc(x.src)}</div>`:""}
      <input value="${esc(x.vi||"")}" placeholder="bản dịch tiếng Việt…"
        onchange="PR.segments[${i}].vi=this.value;save();draw()">
    </div>`}).join("");
}
function renderLinesHighlight(){
  const ci=curIndex();
  document.querySelectorAll("#lines .lrow").forEach(r=>
    r.classList.toggle("cur",+r.dataset.i===ci));
}
function esc(s){return (s||"").replace(/&/g,"&amp;").replace(/"/g,"&quot;").replace(/</g,"&lt;");}
function fmtms(s){
  s=s||0; const m=Math.floor(s/60), x=(s%60).toFixed(3);
  return `${String(Math.floor(m/60)).padStart(2,"0")}:${String(m%60).padStart(2,"0")}:${x.padStart(6,"0")}`;
}
function V_seek(t){ V().currentTime=t; tick(); }

/* ======================= chạy pipeline ======================= */
async function run(steps){
  if(!JID) return toast("Chưa chọn video","warn");
  try{ await saveNow(); }catch(e){ toast("Lỗi lưu project: "+e.message,"err"); return; }
  if(!(await saveTrCfg(false))) return;
  try{ await api("/api/run",{id:JID,steps}); toast("Đã bắt đầu: "+steps.join(" → ")); }
  catch(e){ toast(e.message,"err"); }
}
function runAll(){
  if(MODE==="story") return storyRunAll();
  run(["asr","translate","tts","render"]);
}
async function cancelRun(){ await api("/api/cancel",{}); toast("Đang huỷ…","warn"); }

/* ======================= đồng bộ trạng thái ======================= */
async function refresh(){
  try{ ST=await api("/api/state"); }catch(e){ return; }
  const manualState=ST.manual||{};
  const manualFinished=_manualWorking&&!manualState.working;
  _manualWorking=!!manualState.working;
  if(manualState.rev!=null && manualState.rev!==_manualRev){
    _manualRev=manualState.rev;
    MANUAL.audio_path=manualState.audio_path||MANUAL.audio_path||"";
    MANUAL.audio_duration=+manualState.audio_duration||0;
    MANUAL.output_path=manualState.output_path||"";
    MANUAL.status=manualState.status||"Sẵn sàng";
    MANUAL.error=manualState.error||"";
    MANUAL.script_path=manualState.script_path||MANUAL.script_path||"";
    MANUAL.script_words=+manualState.script_words||0;
    if(manualState.script_title) MANUAL.writer_title=manualState.script_title;
    if(Array.isArray(manualState.voice_recommendations)&&manualState.voice_recommendations.length){
      VOICE_RECS.items=manualState.voice_recommendations;
      VOICE_RECS.analysis=manualState.voice_analysis||null;
      VOICE_RECS.engine=manualState.voice_recommendations[0].engine||MANUAL.engine;
    }
    if(manualState.recommended_voice){
      MANUAL.voice=manualState.recommended_voice;
      if(VOICE_RECS.engine) MANUAL.engine=VOICE_RECS.engine;
    }
    if(manualState.nhac_nen) MANUAL.nhac_ten=manualState.nhac_nen;
    if(MANUAL.script_path && MANUAL.script_path!==_lastScriptPath){
      _lastScriptPath=MANUAL.script_path;
      const generated=await api("/api/story/generated_script").catch(()=>null);
      if(generated&&generated.text){
        MANUAL.text=generated.text;
        MANUAL.script_words=+generated.words||MANUAL.script_words;
        MANUAL.writer_title=generated.title||MANUAL.writer_title;
        if(!MANUAL.name) MANUAL.name=generated.title||"";
        toast(`Đã tự nạp kịch bản ${MANUAL.script_words.toLocaleString("vi-VN")} từ`,"ok");
      }
    }
    rerenderMode();
  }
  if(manualFinished){
    toast(manualState.error||manualState.status||"Đã xử lý xong",
          manualState.error?"err":"ok");
  }
  const readyDownloads=[];
  const failedDownloads=[];
  for(const j of (ST.queue||[])){
    const old=_queuePrev[j.id];
    if(old&&old.status==="đang tải"&&j.status!=="đang tải"){
      if(j.status==="lỗi") failedDownloads.push(j);
      else if(j.path) readyDownloads.push(j);
    }
  }
  document.getElementById("cuda").textContent = ST.nvenc?"· CUDA sẵn sàng":"· chạy CPU";
  document.getElementById("qcount").textContent=ST.queue.length+" mục";
  const q=document.getElementById("queue");
  q.innerHTML=ST.queue.map(j=>{
    const cls=j.status==="xong"?"done":j.status==="lỗi"?"err":
              (j.status==="dang chay"||j.status==="đang tải")?"run":"";
    const jpct=Number(j.progress||0);
    const pct=j.status==="xong"?100:
      (j.status==="đang tải"?Math.max(5,jpct):
       (j.id===ST.selected&&ST.running?(ST.progress.pct||0):jpct));
    const note=esc(j.note||j.status);
    return `<div class="qitem ${j.id===ST.selected?"sel":""}" onclick="selectJob(${j.id})">
      <div class="qx" onclick="delJob(${j.id},event)">×</div>
      <div class="qtop"><span class="qdot ${cls}"></span>
        <span class="qname" title="${esc(j.name)}">${esc(j.name)}</span></div>
      <div class="qbar"><i style="width:${pct}%"></i></div>
      <div class="qfoot">${j.output?`<span class="lplay" style="margin-right:auto"
         onclick="event.stopPropagation();openOut('${esc(j.output).replace(/\\/g,"\\\\")}')"
         >📂 mở thư mục</span>`:""}<span class="qstatus" title="${note}">${note}</span></div></div>`;
  }).join("")||`<div class="hint" style="margin:8px 4px">Hàng đợi trống.</div>`;

  const p=ST.progress||{};
  document.getElementById("pstep").textContent= ST.busy ? ST.busy :
    (ST.running?`Bước ${p.sub||1}/${p.total||6} · ${p.step||""}`:(p.step||"Sẵn sàng"));
  document.getElementById("pdetail").textContent=p.detail||"";
  document.getElementById("ppct").textContent=Math.round(p.pct||0)+"%";
  document.querySelector("#pbar i").style.width=(p.pct||0)+"%";
  document.getElementById("runbtn").disabled=!!ST.running;
  document.getElementById("gpu").textContent=ST.nvenc?"NVENC":"CPU";

  /* Chỉ tải lại dự án khi máy chủ báo có thay đổi THẬT (số hiệu phiên bản đổi).
     Trước đây kéo cả dự án về mỗi 1.2 giây - phim dài thì vài MB JSON mỗi nhịp,
     đó là một trong các lý do cửa sổ treo. */
  if(JID && ST.rev!=null && ST.rev!==_lastRev){
    _lastRev=ST.rev;
    const np=await api("/api/project?id="+JID).catch(()=>null);
    if(np&&np.w){
      const box=PR&&PR.sub_style&&PR.sub_style.box;
      PR=np;
      if(box) PR.sub_style.box=box;      // giữ hộp người dùng đang kéo
      _ciHint=0; drawTracks(true); renderPanel(); draw();
    }
  }
  (ST.log||[]).slice(-1).forEach(l=>{
    if(l.kind==="err"&&l.msg!==window._lastErr){window._lastErr=l.msg;toast(l.msg,"err");}
  });
  for(const j of readyDownloads){
    toast("Tải xong: "+j.name,"ok");
    if(j.id===ST.selected) await selectJob(j.id);
  }
  for(const j of failedDownloads){
    toast(j.note||"Tải video lỗi","err");
  }
  _queuePrev=Object.fromEntries((ST.queue||[]).map(j=>[j.id,{
    status:j.status,path:j.path,note:j.note
  }]));
}

/* ======================= khởi động ======================= */
V().addEventListener("timeupdate",tick);
V().addEventListener("seeked",tick);
window.addEventListener("resize",()=>{applyZoom();});
document.addEventListener("keydown",e=>{
  if(e.target.tagName==="INPUT"||e.target.tagName==="SELECT"||
     e.target.tagName==="TEXTAREA"||e.target.isContentEditable) return;
  if(e.code==="Space"){e.preventDefault();togglePlay();}
  if(e.key==="ArrowLeft")jump(-5);
  if(e.key==="ArrowRight")jump(5);
  if(e.key==="Delete"&&ACT&&ACT.kind==="rgn"){PR.regions.splice(ACT.i,1);ACT=null;save();draw();renderPanel();}
});
/* Chạy trong trình duyệt (không phải app desktop) thì ẩn nút cửa sổ */
function initDesktop(){
  if(!DESK()){
    const w=document.getElementById("wbtns"); if(w) w.style.display="none";
    document.getElementById("titlebar").style.height="30px";
  }
}
window.addEventListener("pywebviewready",initDesktop);
setTimeout(initDesktop,900);

/* ======================= CHẾ ĐỘ 2: VIDEO KỂ CHUYỆN ======================= */
let MODE=localStorage.getItem("advn_mode")||"dub";
const STORY={
  imgs:[],                 // đường dẫn ảnh / thư mục, đúng thứ tự cảnh
  sel:-1,                  // ảnh đang xem trước
  aspect:localStorage.getItem("advn_aspect")||"16:9",
  fps:30, kieu:"chuyen_dong",
  nhac_enabled:true, sub_enabled:true,
  auto_voice:localStorage.getItem("advn_auto_voice")!=="0",
  sub:{size:48,color:"#FFFFFF",outline:2,bold:true,
       align:"bottom-center",margin_v:90},
};
function rerenderMode(){ if(MODE==="story") renderStory(); else renderPanel(); }
function setMode(m){
  MODE=(m==="story")?"story":"dub";
  localStorage.setItem("advn_mode",MODE);
  document.body.dataset.mode=MODE;
  document.getElementById("storyMain").style.display=MODE==="story"?"flex":"none";
  document.getElementById("mDub").classList.toggle("on",MODE==="dub");
  document.getElementById("mStory").classList.toggle("on",MODE==="story");
  if(MODE==="story"){ loadManualVoices(false); loadNhacNen(false); renderStory(); }
  else renderPanel();
}
function storyDims(){
  return STORY.aspect==="9:16"?{w:1080,h:1920}:{w:1920,h:1080};
}
const _IMG_EXT=/\.(jpe?g|png|webp|bmp|gif|tiff?)$/i;
function storyThumb(p,i){
  const sel=i===STORY.sel?" sel":"";
  const mv=`<div class="smv">
      <b onclick="event.stopPropagation();storyMove(${i},-1)" title="Lên">↑</b>
      <b onclick="event.stopPropagation();storyMove(${i},1)" title="Xuống">↓</b></div>`;
  const del=`<div class="sdel" title="Bỏ khỏi danh sách"
      onclick="event.stopPropagation();storyDelImg(${i})">✕</div>`;
  if(_IMG_EXT.test(p))
    return `<div class="simg${sel}" onclick="storySel(${i})" title="${esc(p)}">
      <img loading="lazy" src="/api/local_image?path=${encodeURIComponent(p)}">
      <div class="sno">${i+1}</div>${del}${mv}</div>`;
  return `<div class="simg${sel}" onclick="storySel(${i})" title="${esc(p)}">
    <div class="sfolder">📁</div><div class="sno">${i+1}</div>
    <div class="sfname">${esc(p.split(/[\\/]/).pop()||p)}</div>${del}${mv}</div>`;
}
function renderStoryImgs(){
  const el=document.getElementById("simgs"); if(!el) return;
  document.getElementById("simgcount").textContent=STORY.imgs.length+" mục";
  el.innerHTML=STORY.imgs.map((p,i)=>storyThumb(p,i)).join("")
    ||`<div class="hint" style="grid-column:1/-1">Chưa có ảnh nào.<br>
       Bấm <b>+ Thêm ảnh</b> (chọn được nhiều ảnh một lúc) hoặc
       <b>+ Thư mục ảnh</b>. Ảnh được chia đều theo độ dài giọng đọc.</div>`;
}
function renderStoryStage(){
  const st=document.getElementById("sstage"); if(!st) return;
  st.classList.toggle("doc",STORY.aspect==="9:16");
  const firstImg=STORY.imgs.find(p=>_IMG_EXT.test(p));
  const sel=(STORY.sel>=0&&STORY.imgs[STORY.sel]&&_IMG_EXT.test(STORY.imgs[STORY.sel]))
            ?STORY.imgs[STORY.sel]:firstImg;
  let inner="";
  if(MANUAL.output_path){
    inner=`<video controls preload="metadata" src="/api/manual/output?v=${_manualRev}"></video>`;
  }else if(sel){
    inner=`<img src="/api/local_image?path=${encodeURIComponent(sel)}">`;
  }else{
    inner=`<div class="sempty"><b>Khung xem trước ${STORY.aspect}</b>
      Thêm ảnh ở cột trái để xem bố cục</div>`;
  }
  if(!MANUAL.output_path && STORY.sub_enabled){
    const firstLine=(MANUAL.text||"Phụ đề mẫu sẽ hiển thị như thế này")
      .trim().split(/[\r\n]+/)[0].slice(0,90)||"Phụ đề mẫu";
    const s=STORY.sub, d=storyDims();
    const pos=s.align==="top-center"?"top:5.5%":
              s.align==="mid-center"?"top:50%;transform:translateY(-50%)":
              `bottom:${Math.round(s.margin_v/d.h*100)}%`;
    const px=Math.max(11,Math.round(s.size/d.h*
      (STORY.aspect==="9:16"?document.getElementById("sstage").clientHeight||520
                            :document.getElementById("sstage").clientWidth*9/16||300)));
    inner+=`<div class="ssub" style="${pos};color:${s.color};
      font-size:${px}px;${s.bold?"":"font-weight:400;"}">${esc(firstLine)}</div>`;
  }
  st.innerHTML=inner;
  const info=document.getElementById("sinfo");
  if(info){
    const n=(MANUAL.text||"").length;
    const estMin=n?(n/18/60):0;   // giọng Việt đọc ~18-20 ký tự/giây
    info.innerHTML=`Khổ <b>${STORY.aspect}</b> · ${STORY.imgs.length} mục ảnh
      · ${n.toLocaleString("vi-VN")} ký tự${n?` · giọng đọc ước ~<b>${estMin.toFixed(1)} phút</b>`:""}
      ${MANUAL.audio_duration?` · audio hiện có <b>${fmt(MANUAL.audio_duration)}</b>`:""}
      ${MANUAL.output_path?` · <span class="lplay" onclick="openManualFolder('output')">📂 mở thư mục video</span>`:""}`;
  }
}
function renderStoryPanel(){
  const P=document.getElementById("spanel"); if(!P) return;
  const eng=MANUAL.engine||"edge", vs=VOICES[eng]||[];
  const voiceOpts=vs.length
    ? vs.map(v=>`<option value="${esc(v.id)}" ${MANUAL.voice===v.id?"selected":""}>${v.status==="ok"?"✓ ":v.status==="failed"?"✕ ":""}${esc(v.name)}</option>`).join("")
    : `<option value="${esc(MANUAL.voice||"")}">${MANUAL.voice?esc(MANUAL.voice):"(đang nạp giọng…)"}</option>`;
  const busy=!!(ST.manual&&ST.manual.working);
  const s=STORY.sub;
  P.innerHTML=`
  <div class="sstep"><div class="shd"><span class="sn">0</span>Tự viết truyện từ tiêu đề</div>
    ${fld("Tiêu đề/lời hứa của video",`<input value="${esc(MANUAL.writer_title||"")}"
      placeholder="Ví dụ: Con dâu bỏ đi 10 năm, ngày về…"
      oninput="MANUAL.writer_title=this.value">`)}
    <button class="btn pri" style="width:100%;text-align:center;height:38px"
      ${busy?"disabled":""} onclick="storyGenerateAndRun()">
      ✨ TẠO TRUYỆN → TỰ NẠP → RA VIDEO</button>
    <div class="hint">Công cụ <b>Tạo kịch bản</b> sẽ viết từng chương, xuất đúng
      <b>KICH_BAN_DOC.txt</b>, rồi AutoDub tự tạo giọng, nhạc, phụ đề và video.
      Hãy thêm ảnh ở cột trái trước khi bấm.</div>
    ${MANUAL.script_path?`<div class="hint" title="${esc(MANUAL.script_path)}">
      ✓ Đã nạp ${MANUAL.script_words.toLocaleString("vi-VN")} từ ·
      ${esc(MANUAL.script_path.split(/[\\/]/).pop())}</div>`:""}
  </div>
  <div class="sstep"><div class="shd"><span class="sn">1</span>Nội dung truyện</div>
    ${fld("",`<textarea id="manualText" style="min-height:110px"
      placeholder="Dán nội dung cần đọc, hoặc tải file TXT/MD…"
      oninput="MANUAL.text=this.value;storySubLive()">${esc(MANUAL.text||"")}</textarea>`)}
    <div class="rowbtns" style="align-items:center">
      <button class="btn" ${busy?"disabled":""} onclick="pickManualText()">Tải file văn bản</button>
      ${fld("",`<input value="${esc(MANUAL.name||"")}" placeholder="Tên video…"
        oninput="MANUAL.name=this.value">`)}
    </div>
  </div>
  <div class="sstep"><div class="shd"><span class="sn">2</span>Giọng đọc</div>
    <div class="grid2">
      ${fld("Bộ giọng",`<select onchange="setManualEngine(this.value)">
        <option value="edge" ${eng==="edge"?"selected":""}>edge-tts</option>
        <option value="vieneu" ${eng==="vieneu"?"selected":""}>VieNeu (offline)</option>
        <option value="capcut" ${eng==="capcut"?"selected":""}>CapCut TTS</option>
      </select>`)}
      ${fld("Giọng",`<select onchange="MANUAL.voice=this.value">${voiceOpts}</select>`)}
    </div>
    ${rng("Tốc độ đọc",parseInt(MANUAL.rate||"0"),"%",-30,50,5,
      "MANUAL.rate=(this.value>0?'+':'')+this.value+'%';this.previousElementSibling.querySelector('b').textContent=this.value+'%'")}
    ${nutNgheThu("story")}
    <div class="hint">Đã nạp <b>${vs.length}</b> giọng cho ${esc(eng)}. Đổi giọng trong
      danh sách rồi bấm nghe, hoặc mở thư viện để mỗi giọng có nút nghe riêng.</div>
    ${voiceRecommendationHtml(eng,vs,busy)}
    <div class="rowbtns">
      <button class="btn" ${busy?"disabled":""} onclick="createManualAudio()">Tạo riêng audio</button>
      <button class="btn" onclick="pickManualAudio()">Dùng audio có sẵn</button>
    </div>
    ${MANUAL.audio_path?`<audio controls preload="metadata" style="width:100%;margin-top:8px"
      src="/api/manual/audio?v=${_manualRev}"></audio>`:""}
  </div>
  <div class="sstep"><div class="shd"><span class="sn">3</span>Nhạc nền
    <label class="sonoff"><input type="checkbox" ${STORY.nhac_enabled?"checked":""}
      onchange="STORY.nhac_enabled=this.checked;renderStoryPanel()"> bật</label></div>
    ${STORY.nhac_enabled?`
      ${fld("Bài nhạc (CC0)",`<select onchange="MANUAL.nhac_bai=this.value">
        <option value="">— ngẫu nhiên trong kho (${NHAC_LIST.length} bài) —</option>
        ${NHAC_LIST.map(x=>`<option value="${esc(x.ten)}" ${MANUAL.nhac_bai===x.ten?"selected":""}>${esc(x.ten)}</option>`).join("")}
      </select>`)}
      ${rng("Mức nhạc",MANUAL.nhac_db," dB",-50,-20,1,
        "MANUAL.nhac_db=+this.value;this.previousElementSibling.querySelector('b').textContent=this.value+' dB'")}
      <label style="display:flex;gap:7px;align-items:center;margin-top:4px">
        <input type="checkbox" ${MANUAL.nhac_duck?"checked":""}
          onchange="MANUAL.nhac_duck=this.checked">
        <span>Nhạc tự nhỏ khi có lời (ducking)</span></label>
      <div class="rowbtns" style="margin-top:6px">
        <button class="btn sm" ${busy?"disabled":""} onclick="taiNhacNen()">Tải thêm nhạc</button>
        <button class="btn sm" onclick="loadNhacNen(true)">Làm mới</button>
      </div>`:`<div class="hint">Video sẽ chỉ có giọng đọc, không nhạc nền.</div>`}
  </div>
  <div class="sstep"><div class="shd"><span class="sn">4</span>Phụ đề cứng
    <label class="sonoff"><input type="checkbox" ${STORY.sub_enabled?"checked":""}
      onchange="STORY.sub_enabled=this.checked;renderStoryPanel();renderStoryStage()"> bật</label></div>
    ${STORY.sub_enabled?`
      <div class="grid2">
        ${fld("Cỡ chữ",`<input type="number" min="20" max="120" value="${s.size}"
          onchange="STORY.sub.size=Math.max(20,+this.value||48);renderStoryStage()">`)}
        ${fld("Vị trí",`<select onchange="STORY.sub.align=this.value;renderStoryStage()">
          <option value="bottom-center" ${s.align==="bottom-center"?"selected":""}>Dưới đáy</option>
          <option value="mid-center" ${s.align==="mid-center"?"selected":""}>Giữa hình</option>
          <option value="top-center" ${s.align==="top-center"?"selected":""}>Trên đỉnh</option>
        </select>`)}
      </div>
      <div class="grid2">
        ${fld("Màu chữ",`<input type="color" value="${s.color}" style="height:31px;padding:2px"
          onchange="STORY.sub.color=this.value;renderStoryStage()">`)}
        ${fld("Viền",`<input type="number" min="0" max="6" value="${s.outline}"
          onchange="STORY.sub.outline=Math.max(0,+this.value||0)">`)}
      </div>
      <div class="hint">Phụ đề tự khớp mốc thời gian với giọng đọc, tách câu dễ đọc, tối đa 2 dòng.</div>`
      :`<div class="hint">Video sẽ không ghi chữ lên hình.</div>`}
  </div>
  <div class="sstep"><div class="shd"><span class="sn">5</span>Khung hình</div>
    <div class="grid2">
      ${fld("Khổ video",`<select onchange="STORY.aspect=this.value;
          localStorage.setItem('advn_aspect',this.value);renderStoryStage()">
        <option value="16:9" ${STORY.aspect==="16:9"?"selected":""}>Ngang 16:9 · 1920×1080 (YouTube)</option>
        <option value="9:16" ${STORY.aspect==="9:16"?"selected":""}>Dọc 9:16 · 1080×1920 (TikTok/Shorts)</option>
      </select>`)}
      ${fld("Kiểu hình",`<select onchange="STORY.kieu=this.value">
        <option value="chuyen_dong" ${STORY.kieu==="chuyen_dong"?"selected":""}>Ảnh trôi + phóng chậm</option>
        <option value="tinh" ${STORY.kieu==="tinh"?"selected":""}>Ảnh đứng yên (nhanh)</option>
      </select>`)}
    </div>
  </div>
  <button class="btn pri" style="width:100%;text-align:center;height:38px;font-size:13px"
    ${busy?"disabled":""} onclick="storyRunAll()">▶ CHẠY TẤT CẢ — RA VIDEO HOÀN CHỈNH</button>
  <div class="hint" style="margin-top:8px"><b>${esc(MANUAL.status||"Sẵn sàng")}</b>
    ${MANUAL.error?`<br><span style="color:var(--red)">${esc(MANUAL.error)}</span>`:""}</div>
  ${MANUAL.output_path?`<button class="btn" style="width:100%;text-align:center;margin-top:6px"
    onclick="openManualFolder('output')">📂 Mở thư mục video kết quả</button>`:""}
  <div class="hint" style="margin-top:10px">Muốn ghép audio vào một video có sẵn thay vì dựng
    từ ảnh: chuyển sang chế độ <b>Lồng tiếng</b>, chọn video rồi quay lại đây
    <span class="lplay" onclick="muxManualAudio()">▶ ghép vào video đang chọn</span>.</div>`;
}
function storySubLive(){
  /* Cập nhật NHẸ khi đang gõ: chỉ đổi chữ phụ đề mẫu + số ký tự,
     không dựng lại <img> để ảnh xem trước khỏi nháy theo từng phím. */
  const el=document.querySelector("#sstage .ssub");
  if(el) el.textContent=(MANUAL.text||"").trim().split(/[\r\n]+/)[0].slice(0,90)
                        ||"Phụ đề mẫu";
  const n=(MANUAL.text||"").length;
  const info=document.getElementById("sinfo");
  if(info&&n) info.innerHTML=`Khổ <b>${STORY.aspect}</b> · ${STORY.imgs.length} mục ảnh
    · ${n.toLocaleString("vi-VN")} ký tự · giọng đọc ước ~<b>${(n/18/60).toFixed(1)} phút</b>`;
}
function renderStory(){ renderStoryImgs(); renderStoryStage(); renderStoryPanel(); }
function storySel(i){ STORY.sel=i; MANUAL.output_path=""; renderStoryImgs(); renderStoryStage(); }
function storyDelImg(i){ STORY.imgs.splice(i,1); if(STORY.sel>=STORY.imgs.length)STORY.sel=-1; renderStory(); }
function storyClearImgs(){ STORY.imgs=[]; STORY.sel=-1; renderStory(); }
function storyMove(i,d){
  const j=i+d; if(j<0||j>=STORY.imgs.length) return;
  [STORY.imgs[i],STORY.imgs[j]]=[STORY.imgs[j],STORY.imgs[i]];
  if(STORY.sel===i)STORY.sel=j; else if(STORY.sel===j)STORY.sel=i;
  renderStoryImgs(); renderStoryStage();
}
async function storyAddImgs(){
  if(DESK()){
    try{
      const r=await pywebview.api.pick_images();
      if(r&&r.error) return toast(r.error,"err");
      const list=Array.isArray(r)?r:(r?[r]:[]);
      if(list.length){ STORY.imgs.push(...list); renderStory(); toast(`Đã thêm ${list.length} ảnh`,"ok"); }
    }catch(e){ toast(e.message||String(e),"err"); }
    return;
  }
  const p=prompt("Dán đường dẫn ảnh (mỗi lần một ảnh):")||"";
  if(p.trim()){ STORY.imgs.push(p.trim()); renderStory(); }
}
async function storyAddFolder(){
  let path="";
  if(DESK()){
    try{
      const r=await pywebview.api.pick_folder();
      if(r&&r.error) return toast(r.error,"err");
      path=Array.isArray(r)?(r[0]||""):(r||"");
    }catch(e){ return toast(e.message||String(e),"err"); }
  }else path=prompt("Dán đường dẫn thư mục ảnh:")||"";
  if(path.trim()){ STORY.imgs.push(path.trim()); renderStory(); toast("Đã thêm thư mục ảnh","ok"); }
}
function storyRequestPayload(includeText){
  const ta=document.getElementById("manualText");
  if(ta) MANUAL.text=ta.value;
  const d=storyDims();
  const payload={
    name:MANUAL.name,
    engine:MANUAL.engine, voice:MANUAL.voice, pitch:MANUAL.pitch, rate:MANUAL.rate,
    anh:STORY.imgs, w:d.w, h:d.h, fps:STORY.fps, kieu:STORY.kieu,
    nhac:{enabled:STORY.nhac_enabled, bai:MANUAL.nhac_bai,
          muc_db:MANUAL.nhac_db, duck:MANUAL.nhac_duck},
    sub:{enabled:STORY.sub_enabled, style:{
      size:STORY.sub.size, color:STORY.sub.color, outline:STORY.sub.outline,
      bold:STORY.sub.bold, align:STORY.sub.align, margin_v:STORY.sub.margin_v}},
    voice_auto:STORY.auto_voice,
  };
  if(includeText) payload.text=MANUAL.text;
  return payload;
}
async function storyGenerateAndRun(){
  const title=String(MANUAL.writer_title||"").trim();
  if(!title) return toast("Hãy nhập tiêu đề truyện","warn");
  if(!STORY.imgs.length) return toast("Hãy thêm ảnh ở cột trái trước khi chạy","warn");
  try{
    const payload=storyRequestPayload(false);
    payload.story_title=title;
    await api("/api/story/generate_and_run",payload);
    MANUAL.output_path=""; MANUAL.status="Đang tạo kịch bản từ tiêu đề…"; MANUAL.error="";
    MANUAL.script_path=""; MANUAL.script_words=0;
    ST.manual={...(ST.manual||{}),working:true};
    renderStory();
    toast("Đã bắt đầu: viết truyện → tự nạp → giọng → video","ok");
  }catch(e){ toast(e.message,"err"); }
}
async function storyRunAll(){
  storyRequestPayload(true);
  if(!String(MANUAL.text||"").trim()) return toast("Hãy nhập nội dung truyện (bước 1)","warn");
  if(!STORY.imgs.length) return toast("Hãy thêm ảnh ở cột trái","warn");
  try{
    await api("/api/manual/run_all",storyRequestPayload(true));
    MANUAL.output_path=""; MANUAL.status="Bắt đầu làm video kể chuyện…"; MANUAL.error="";
    ST.manual={...(ST.manual||{}),working:true};
    renderStory(); toast("Đã bắt đầu: giọng đọc → nhạc nền → phụ đề → video","ok");
  }catch(e){ toast(e.message,"err"); }
}

/* ======================= khởi động ======================= */
async function init(){
  await loadConfig();
  setMode(MODE);
  refresh();
  setInterval(refresh,1200);
}
init();
