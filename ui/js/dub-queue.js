"use strict";
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
  const raw = document.getElementById("url").value.trim();
  if(!raw) return;
  // Split by newlines to support multiple URLs
  const lines = raw.split(/[\r\n]+/).map(s=>s.trim()).filter(Boolean);
  if(lines.length === 0) return;

  if(lines.length === 1){
    // Single URL - use original endpoint
    toast("Đã thêm vào hàng đợi tải…");
    try{
      const r = await api("/api/queue/add", {url: lines[0]});
      document.getElementById("url").value = "";
      if(r.async){
        await api("/api/queue/select", {id: r.id});
        JID=null; PR=null; _lastRev=-1;
        showEmpty("Đang tải video", "Khi tải xong, video sẽ tự hiện ở đây.");
        renderPanel();
        await refresh();
        toast("Đang tải trong nền…", "ok");
      } else {
        await selectJob(r.id); refresh(); toast("Tải xong", "ok");
      }
    } catch(e){ toast(e.message, "err"); }
  } else {
    // Multiple URLs - use batch endpoint
    toast(`Đang thêm ${lines.length} link vào hàng đợi…`);
    try{
      const r = await api("/api/queue/add_batch", {urls: lines});
      document.getElementById("url").value = "";
      const ok = (r.results||[]).filter(x=>x.ok).length;
      const fail = (r.results||[]).filter(x=>x.error).length;
      await refresh();
      if(fail) toast(`Đã thêm ${ok} link, ${fail} lỗi`, "warn");
      else toast(`Đã thêm ${ok} link vào hàng đợi tải`, "ok");
    } catch(e){ toast(e.message, "err"); }
  }
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
  if(PR&&JID&&JID!==id) await saveNow();
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
  ACT=null;
  _lastRev=-1; _ciHint=0; _trkSig="";
  const v=V();
  v.src="/api/video?id="+id;
  document.getElementById("vwrap").style.display="";
  document.getElementById("empty").style.display="none";
  v.onloadedmetadata=()=>{applyZoom();draw();document.getElementById("tdur").textContent=fmt(v.duration)};
  renderPanel(); draw(); loadVoices(false);
}
async function saveProject(){
  if(!PR||!JID) return toast("Chưa chọn video","warn");
  try{await saveNow();toast("Đã lưu cấu hình dự án","ok");}
  catch(e){toast("Không lưu được dự án: "+e.message,"err");}
}

/* ======================= vùng phủ (3 lớp) ======================= */
function addRegion(type){
  if(!PR) return toast("Chưa chọn video","warn");
  const w=Math.round(PR.w*0.5), h=Math.round(PR.h*0.13);
  PR.regions.push({x:Math.round((PR.w-w)/2),
                   y:type==="delogo"?Math.round(PR.h*0.05):Math.round(PR.h*0.8),
                   w,h,type,strength:20,start:null,end:null});
  ACT={kind:"rgn",i:PR.regions.length-1}; setTab("blur"); save(); draw();
}
function clearRegions(){ if(!PR)return; PR.regions=[]; ACT=null; save(); draw(); renderPanel(); }
function addLogoBox(){
  if(!PR) return toast("Chưa chọn video","warn");
  PR.logo={x:Math.round(PR.w*0.03),y:Math.round(PR.h*0.04),
           w:Math.round(PR.w*0.15),h:Math.round(PR.h*0.09),opacity:1,path:"",motion:false};
  ACT={kind:"logo"}; setTab("logo"); save(); draw();
}
async function autoDetect(){
  if(!JID) return toast("Chưa chọn video","warn");
  toast("Đang dò vùng phụ đề gốc… có thể mất 30–60 giây");
  try{
    const r=await api("/api/detect_sub",{id:JID});
    if(r&&r.async){
      const before=((PR&&PR.regions)||[]).length;
      const started=Date.now();
      while(Date.now()-started<90000){
        await new Promise(ok=>setTimeout(ok,800));
        await refresh();
        if(ST.busy) continue;
        const np=await api("/api/project?id="+JID).catch(()=>null);
        if(np) PR=np;
        const regions=(PR&&PR.regions)||[];
        if(regions.length>before){
          const region=regions[regions.length-1];
          ACT={kind:"rgn",i:regions.length-1};
          draw(); renderPanel();
          toast(`Đã dò: ${region.w}×${region.h} tại (${region.x},${region.y})`,"ok");
          return;
        }
        toast("Không dò được vùng phụ đề. Hãy kéo khung mờ thủ công.","warn");
        return;
      }
      toast("Dò vùng phụ đề vẫn chạy nền. Khi xong, khung mới sẽ hiện trên video.","warn");
      return;
    }
    if(!r||!r.region){
      toast("Không dò được vùng phụ đề.","warn");
      return;
    }
    PR=await api("/api/project?id="+JID);
    ACT={kind:"rgn",i:PR.regions.length-1};
    draw(); renderPanel();
    toast(`Đã dò: ${r.region.w}×${r.region.h} tại (${r.region.x},${r.region.y})`,"ok");
  }catch(e){ toast(e.message,"err"); }
}
