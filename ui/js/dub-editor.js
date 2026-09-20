"use strict";
/* ---------- vẽ các lớp lên video ---------- */
function scale(){
  const v=V();
  return PR&&PR.w ? v.clientWidth/PR.w : 1;
}
const _editorDebug=typeof location!=="undefined"&&new URLSearchParams(location.search).has("editorDebug");
function editorTrace(type,detail={}){
  if(!_editorDebug) return;
  const events=window.editorDebugEvents||(window.editorDebugEvents=[]);
  events.push({type,time:V().currentTime,selection:ACT&&{...ACT},...detail});
  if(events.length>2500) events.shift();
}
let _previewRaf=null;
function requestPreviewRender(reason="EDITOR_STATE_CHANGED"){
  if(_previewRaf!==null) return;
  _previewRaf=requestAnimationFrame(()=>{
    _previewRaf=null;
    // Pointer capture belongs to this DOM node until the gesture finishes.
    // pointerup schedules the complete composite after committing its local paint.
    if(_isDragging) return;
    renderEditorPreview();
    editorTrace("render",{reason});
  });
}
function draw(){requestPreviewRender();}
function renderEditorPreview(){
  const L=document.getElementById("layers");
  if(!PR||!PR.w||!V().clientWidth){L.innerHTML="";return;}
  const k=scale(), prev=document.getElementById("prev").checked;
  L.innerHTML="";

  (PR.regions||[]).forEach((r,i)=>{
    const isD=r.type==="delogo";
    const el=mkBox(r,k,isD?"delogo":"blur",String(i+1),
                   isD?"Xoá logo gốc":"Vùng làm mờ (lớp 2)",()=>{
      PR.regions.splice(i,1); ACT=null; save(); draw(); renderPanel();
    });
    if(prev&&!isD){
      if(regionActiveAt(r,V().currentTime||0))
        el.style.backdropFilter=`blur(${Math.max(2,(r.strength||20)*k*0.55)}px)`;
      el.style.background="rgba(0,0,0,.04)";
    }
    el.dataset.rgn=i;
    if(ACT&&ACT.kind==="rgn"&&ACT.i===i) el.classList.add("act");
    el.onpointerdown=e=>startDrag(e,el,r,k,()=>{ACT={kind:"rgn",i};setTab("blur");});
    L.appendChild(el);
  });

  if(PR.logo){
    const el=mkBox(PR.logo,k,"logo","L","Logo của bạn",()=>{PR.logo=null;ACT=null;save();draw();renderPanel();});
    if(prev&&PR.logo.path){
      const img=document.createElement("img");
      img.className="logo-image"; img.alt=""; img.draggable=false;
      img.src="/api/local_image?path="+encodeURIComponent(PR.logo.path);
      img.style.opacity=PR.logo.opacity??1;
      el.prepend(img);
    }
    if(ACT&&ACT.kind==="logo") el.classList.add("act");
    el.onpointerdown=e=>startDrag(e,el,PR.logo,k,()=>{ACT={kind:"logo"};setTab("logo");});
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
      const ow=Math.max(0,(st.outline??2)*k);
      t.style.textShadow=`0 0 ${ow}px ${st.outline_color||"#000"},`+
        [[-1,-1],[1,-1],[-1,1],[1,1]].map(([a,b])=>`${a*ow}px ${b*ow}px 0 ${st.outline_color||"#000"}`).join(",");
      const al=st.align||"mid-center";
      t.style.textAlign=al.includes("left")?"left":al.includes("right")?"right":"center";
      if(al.startsWith("top")){t.style.top="0";t.style.bottom="auto";}
      else if(al.startsWith("mid")){t.style.top="50%";t.style.bottom="auto";t.style.transform="translateY(-50%)";}
      el.appendChild(t);
    }
    el.onpointerdown=e=>startDrag(e,el,box,k,()=>{ACT={kind:"sub"};setTab("sub");});
    L.appendChild(el);
  }
  drawTracks();
  const a=activeRegion();
  document.getElementById("rgninfo").textContent = a
    ? `${a.type==="logo"?"Logo":"Vùng "+(a.type==="delogo"?"xoá logo":a.type==="sub"?"phụ đề Việt":"làm mờ")} · ${Math.round(a.w)}×${Math.round(a.h)} @ (${Math.round(a.x)},${Math.round(a.y)})`
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
  sizeHandleTargets(el,r,k);
  const tag=document.createElement("div");
  tag.className="tag"; tag.textContent=num+(label?" · "+label:"");
  el.appendChild(tag);
  if(onRemove){
    const x=document.createElement("div");
    x.className="rm"; x.textContent="×";
    x.onpointerdown=e=>{
      if(e.button!==0) return;
      e.preventDefault();e.stopPropagation();
      _editorClickPointer=e.pointerId; _suppressPlayClick=true; onRemove();
    };
    el.appendChild(x);
  }
  ["nw","ne","sw","se","n","s","w","e"].forEach(p=>{
    const h=document.createElement("div"); h.className="h "+p; h.dataset.p=p;
    el.appendChild(h);
  });
  return el;
}
function sizeHandleTargets(el,r,k){
  // Oversized invisible handles used to consume the entire body of small logos.
  el.style.setProperty("--handle-slop",Math.max(0,Math.min(12,Math.min(r.w*k,r.h*k)/2-18))+"px");
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
const HANDLE_CURSOR={nw:"nwse-resize",se:"nwse-resize",ne:"nesw-resize",sw:"nesw-resize",
  n:"ns-resize",s:"ns-resize",w:"ew-resize",e:"ew-resize"};
let _isDragging=false;
let _editorClickPointer=null;
let _suppressPlayClick=false;
let _guides=null;
let _dragMeta=null;

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

function previewRect(){
  return V().getBoundingClientRect();
}
function clientToSource(clientX, clientY){
  const rect=previewRect();
  if(!PR||!PR.w||rect.width<1||rect.height<1) return null;
  return {
    x:(clientX-rect.left)*PR.w/rect.width,
    y:(clientY-rect.top)*PR.h/rect.height,
    rect
  };
}
function handleAtClient(r, clientX, clientY, rect){
  if(!r||!rect||rect.width<1) return null;
  const k=rect.width/PR.w;
  const left=rect.left+r.x*k, top=rect.top+r.y*k, w=r.w*k, h=r.h*k;
  const slop=Math.max(0,Math.min(12,Math.min(w,h)/2-18));
  const rad=7+slop;
  const pts=[["nw",left,top],["ne",left+w,top],["sw",left,top+h],["se",left+w,top+h],
             ["n",left+w/2,top],["s",left+w/2,top+h],["w",left,top+h/2],["e",left+w,top+h/2]];
  for(const [p,hx,hy] of pts){
    if(Math.abs(clientX-hx)<=rad&&Math.abs(clientY-hy)<=rad) return p;
  }
  return null;
}
function removeHitAtClient(r, clientX, clientY, rect){
  if(!r||!rect) return false;
  const k=rect.width/PR.w;
  const left=rect.left+(r.x+r.w)*k-17;
  const top=rect.top+r.y*k-19;
  return clientX>=left&&clientX<=left+18&&clientY>=top&&clientY<=top+18;
}
function pointInSourceRect(x,y,r){
  return r&&x>=r.x&&y>=r.y&&x<=r.x+r.w&&y<=r.y+r.h;
}
function parseEditorBox(box, extra){
  if(!box||!PR) return null;
  extra=extra||{};
  if(box.classList.contains("logo"))
    return PR.logo?{kind:"logo",r:PR.logo,el:box,...extra}:null;
  if(box.classList.contains("sub"))
    return PR.sub_style&&PR.sub_style.box?{kind:"sub",r:PR.sub_style.box,el:box,...extra}:null;
  const i=+box.dataset.rgn;
  const region=PR.regions&&PR.regions[i];
  return region?{kind:"rgn",i,r:region,el:box,...extra}:null;
}
function editorBoxEl(hit){
  if(!hit) return null;
  if(hit.el&&hit.el.isConnected) return hit.el;
  if(hit.kind==="logo") return document.querySelector("#layers .box.logo");
  if(hit.kind==="sub") return document.querySelector("#layers .box.sub");
  if(hit.kind==="rgn") return document.querySelector("#layers .box[data-rgn=\""+hit.i+"\"]");
  return null;
}
function hitTestEditorDom(clientX, clientY){
  const stack=(document.elementsFromPoint&&document.elementsFromPoint(clientX,clientY))||[];
  for(const n of stack){
    if(!n||n.id==="video"||n.id==="vwrap"||n.id==="layers") continue;
    if(n.id==="playOverlay"||(n.closest&&n.closest("#playOverlay"))) continue;
    if(n.classList&&n.classList.contains("rm")){
      const hit=parseEditorBox(n.closest(".box"),{remove:true}); if(hit) return hit;
    }
    if(n.dataset&&n.dataset.p){
      const hit=parseEditorBox(n.closest(".box"),{handle:n.dataset.p}); if(hit) return hit;
    }
    const box=n.closest&&n.closest("#layers .box");
    if(box){ const found=parseEditorBox(box,{handle:null}); if(found) return found; }
  }
  return null;
}
function hitTestEditorState(clientX, clientY){
  const mapped=clientToSource(clientX,clientY);
  if(!mapped) return null;
  const {x,y,rect}=mapped;
  const ordered=[];
  if(PR.logo) ordered.push({kind:"logo",r:PR.logo});
  if(PR.sub_style&&PR.sub_style.box) ordered.push({kind:"sub",r:PR.sub_style.box});
  const regions=PR.regions||[];
  for(let i=regions.length-1;i>=0;i--) ordered.push({kind:"rgn",i,r:regions[i]});
  for(const item of ordered){
    const r=item.r;
    if(item.kind!=="sub"&&removeHitAtClient(r,clientX,clientY,rect))
      return {...item,remove:true};
    const handle=handleAtClient(r,clientX,clientY,rect);
    if(handle) return {...item,handle};
    if(pointInSourceRect(x,y,r)) return {...item,handle:null};
  }
  return null;
}
function hitTestEditor(clientX, clientY){
  const state=hitTestEditorState(clientX,clientY);
  if(state&&state.remove) return state;
  return hitTestEditorDom(clientX,clientY)||state;
}
function pickHit(hit){
  if(hit.kind==="logo"){ ACT={kind:"logo"}; if(TAB!=="logo") setTab("logo"); }
  else if(hit.kind==="sub"){ ACT={kind:"sub"}; if(TAB!=="sub") setTab("sub"); }
  else { ACT={kind:"rgn",i:hit.i}; if(TAB!=="blur") setTab("blur"); }
}
function removeHit(hit){
  if(hit.kind==="logo") PR.logo=null;
  else if(hit.kind==="rgn") PR.regions.splice(hit.i,1);
  else return;
  ACT=null; save(); draw(); renderPanel();
}
function onPreviewPointerDown(e){
  if(e.button!==0||_isDragging||!PR||!PR.w) return;
  const hit=hitTestEditor(e.clientX,e.clientY);
  if(!hit) return;
  _editorClickPointer=e.pointerId;
  _suppressPlayClick=true;
  e.preventDefault();
  e.stopImmediatePropagation();
  if(hit.remove){ removeHit(hit); return; }
  const el=editorBoxEl(hit);
  startDrag(e,el,hit.r,scale(),()=>pickHit(hit),hit.handle,hit);
}
window.hitTestEditor=hitTestEditor;
if(typeof module!=="undefined") module.exports={computeDrag,hitTestEditor,handleAtClient,clientToSource};

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

function startDrag(e,el,r,k,onPick,forcedHandle,hit){
  if(e.button!==0||_isDragging||!r) return;
  e.preventDefault(); e.stopPropagation();
  _editorClickPointer=e.pointerId;
  _suppressPlayClick=true;
  _isDragging=true;
  _dragMeta=hit||null;
  if(onPick) onPick();
  document.querySelectorAll("#layers .box.act").forEach(box=>box.classList.remove("act"));
  editorTrace("pointerdown",{
    target:e.target&&e.target.className, x:e.clientX, y:e.clientY,
    hit:hit&&{kind:hit.kind,i:hit.i,handle:hit.handle||null}
  });

  const handle=forcedHandle!==undefined?forcedHandle:((e.target.dataset&&e.target.dataset.p)||null);
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

  const bindEl=node=>{
    if(!node) return;
    node.style.willChange="left,top,width,height";
    node.classList.add("act","dragging");
    try{ node.setPointerCapture(e.pointerId); }catch(_){}
  };
  bindEl(el);
  document.body.style.cursor=handle?(HANDLE_CURSOR[handle]||"move"):"move";

  let raf=null, last=null, gx=null, gy=null;

  const apply=()=>{
    raf=null;
    if(!last) return;
    const rect=previewRect();
    if(rect.width<1||rect.height<1) return;
    k=rect.width/PR.w;
    const res=computeDrag({
      o, handle, ratio, maxW, maxH, xs, ys,
      // Deltas cancel the letterbox offset. Re-read scale without interpreting
      // a layout shift as extra pointer travel (including the delayed titlebar).
      dx:(last.clientX-sx)*PR.w/rect.width, dy:(last.clientY-sy)*PR.h/rect.height,
      tol:SNAP*PR.w/rect.width, noSnap:last.altKey, keepRatio:last.shiftKey,
    });
    gx=res.gx; gy=res.gy;
    // Dub logos are positioned by this editor; the shared story exporter also
    // supports drifting logos, which must not override a manual placement.
    if(r===PR.logo&&(r.x!==res.x||r.y!==res.y||r.w!==res.w||r.h!==res.h)) r.motion=false;
    r.x=res.x; r.y=res.y; r.w=res.w; r.h=res.h;
    if(!el||!el.isConnected) el=editorBoxEl(_dragMeta);
    if(el){
      el.style.left=(r.x*k)+"px"; el.style.top=(r.y*k)+"px";
      el.style.width=(r.w*k)+"px"; el.style.height=(r.h*k)+"px";
      el.classList.add("act","dragging");
      sizeHandleTargets(el,r,k);
    }
    editorTrace("pointermove",{x:r.x,y:r.y,w:r.w,h:r.h});
    editorTrace("render",{reason:"EDITOR_STATE_CHANGED"});
    if(gx!=null||gy!=null) showGuides(gx,gy,k); else hideGuides();
    if(info) info.textContent=`${Math.round(r.w)}×${Math.round(r.h)} @ (${Math.round(r.x)},${Math.round(r.y)})`
      +(gx!=null||gy!=null?"  · hít mốc":"")+"   [Alt: tắt hít · Shift: giữ tỉ lệ]";
  };

  const move=ev=>{
    if(ev.pointerId!==e.pointerId) return;
    last=ev; if(raf===null) raf=requestAnimationFrame(apply);
  };
  const finish=ev=>{
    if(ev.pointerId!==e.pointerId) return;
    if(ev.type==="lostpointercapture"&&(ev.buttons||0)!==0){
      editorTrace("lostpointercapture-ignored",{buttons:ev.buttons});
      if(el&&el.isConnected){ try{ el.setPointerCapture(e.pointerId); }catch(_){ } }
      return;
    }
    window.removeEventListener("pointermove",move);
    window.removeEventListener("pointerup",finish);
    window.removeEventListener("pointercancel",finish);
    if(el) el.removeEventListener("lostpointercapture",finish);
    if(ev.type==="pointerup") last=ev;
    if(raf!==null) cancelAnimationFrame(raf);
    apply();
    try{ if(el) el.releasePointerCapture(ev.pointerId); }catch(_){}
    if(el){ el.style.willChange=""; el.classList.remove("dragging"); }
    _isDragging=false; _dragMeta=null;
    document.body.style.cursor="";
    hideGuides();
    editorTrace(ev.type,{x:r.x,y:r.y,w:r.w,h:r.h});
    save(); draw(); renderPanel();
  };
  window.addEventListener("pointermove",move,{passive:true});
  window.addEventListener("pointerup",finish);
  window.addEventListener("pointercancel",finish);
  if(el) el.addEventListener("lostpointercapture",finish);
}
function applyZoom(){
  const z=document.getElementById("zoom").value/100;
  const st=document.getElementById("stage");
  V().style.maxHeight=(st.clientHeight*z-4)+"px";
  V().style.maxWidth=(st.clientWidth*z-4)+"px";
  requestPreviewRender("LAYOUT_CHANGED");
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
  if(!PR||e.button!==0||_isDragging) return;
  e.preventDefault(); e.stopPropagation();
  _isDragging=true;_editorClickPointer=e.pointerId;
  const line=document.getElementById("trkCut"), d=Math.max(.1,dur());
  line.setPointerCapture(e.pointerId);
  const apply=ev=>{
    const r=line.getBoundingClientRect();
    const t=Math.max(0,Math.min(d,(ev.clientX-r.left)/r.width*d));
    setTrimPoint(side,t,true);
    drawTracks(true);
    const b=trimBounds();
    document.getElementById("rgninfo").textContent=
      `Đoạn giữ ${fmt(b.start)} → ${fmt(b.end)} · dài ${fmt(b.duration)}`;
  };
  const move=ev=>{if(ev.pointerId===e.pointerId) apply(ev);};
  const up=ev=>{
    if(ev.pointerId!==e.pointerId) return;
    window.removeEventListener("pointermove",move);
    window.removeEventListener("pointerup",up);
    window.removeEventListener("pointercancel",up);
    line.removeEventListener("lostpointercapture",up);
    if(ev.type==="pointerup") apply(ev);
    if(line.hasPointerCapture(e.pointerId)) line.releasePointerCapture(e.pointerId);
    _isDragging=false;save();draw();renderPanel();
  };
  window.addEventListener("pointermove",move,{passive:true});
  window.addEventListener("pointerup",up);
  window.addEventListener("pointercancel",up);
  line.addEventListener("lostpointercapture",up);
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
    // Subtitle boundaries can occur within the same second, including paused seeks.
    if(document.getElementById("prev").checked){
      const t=document.getElementById("subtext");
      const txt=currentLineText()||"Phụ đề tiếng Việt sẽ hiện ở đây";
      if(t&&t.textContent!==txt) t.textContent=txt;
    }
    const sec=Math.floor(v.currentTime);
    if(sec!==_lastSec){                 // chữ chỉ đổi mỗi giây, không phải mỗi khung
      _lastSec=sec;
      document.getElementById("tcur").textContent=fmt(v.currentTime);
      document.getElementById("fnum").textContent=
        String(Math.round(v.currentTime*25)).padStart(5,"0");
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
function jumpTo(t, play){
  const v=V();
  const wrap=document.getElementById("vwrap");
  if(!v || (wrap && wrap.style.display==="none")){
    toast("Chọn video trên hàng đợi trước","warn");
    return;
  }
  const dest=Math.max(0,+t||0);
  const max=Number(v.duration);
  v.currentTime=Number.isFinite(max)&&max>0?Math.min(dest,Math.max(0,max-0.05)):dest;
  tick();
  if(play) v.play().catch(()=>{});
}
function syncPlaybackControls(){
  const v=V();
  const overlay=document.getElementById("playOverlay");
  document.getElementById("play").textContent=v.paused?"▶":"❚❚";
  overlay.classList.toggle("playing",!v.paused);
  const button=overlay.querySelector("button");
  button.disabled=!v.paused;
  button.setAttribute("aria-label",v.paused?"Phát video":"Tạm dừng video");
}
function togglePlay(){
  const v=V();
  if(v.paused) v.play().catch(e=>toast("Không phát được video: "+e.message,"err"));
  else v.pause();
}
function stepLine(dir){
  const s=segs(); if(!s.length) return;
  const t=V().currentTime;
  let target=null;
  if(dir>0){ for(const x of s) if(x.start>t+0.05){target=x.start;break;} }
  else{ for(let i=s.length-1;i>=0;i--) if(s[i].start<t-0.25){target=s[i].start;break;} }
  if(target!=null){ V().currentTime=target; tick(); }
}
