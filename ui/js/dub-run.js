"use strict";
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
async function cancelRun(){
  if(_cancelPending) return;
  _cancelPending=true;
  if(ST.manual&&ST.manual.working){
    MANUAL.status="Đang dừng tác vụ…";
    MANUAL.error="";
    ST.manual={...ST.manual,status:MANUAL.status};
    rerenderMode();
  }
  try{
    const r=await api("/api/cancel",{});
    toast(r&&r.active!==false?"Đang dừng an toàn…":"Hiện không có tác vụ đang chạy","warn");
  }catch(e){
    _cancelPending=false;
    toast(e.message||String(e),"err");
  }
}
function cancelStoryRun(){ return cancelRun(); }

async function openCaptionReview(id, ev, folder){
  if(ev) ev.stopPropagation();
  if(id!=null && id!==JID) await selectJob(id);
  const job=(ST.queue||[]).find(x=>x.id===id);
  if(MODE==="dub" && typeof jobNeedsCaptionReview==="function" &&
      jobNeedsCaptionReview(job)) setTab("asr");
  const path=folder || (job && job.review_dir) || (job && job.output) || "";
  if(path) openOut(path);
}

/* ======================= đồng bộ trạng thái ======================= */
let _refreshInFlight=null;
let _reviewTabNudged=false;
function refresh(){
  if(!_refreshInFlight){
    _refreshInFlight=refreshState().finally(()=>{_refreshInFlight=null;});
  }
  return _refreshInFlight;
}
async function refreshState(){
  const wasBusy=!!(ST.running||ST.busy);
  try{ ST=await api("/api/state"); }catch(e){ return; }
  const manualState=ST.manual||{};
  const manualFinished=_manualWorking&&!manualState.working;
  _manualWorking=!!manualState.working;
  if(!manualState.working&&!ST.running) _cancelPending=false;
  if(manualState.rev!=null && manualState.rev!==_manualRev){
    _manualRev=manualState.rev;
    MANUAL.audio_path=manualState.audio_path||MANUAL.audio_path||"";
    MANUAL.audio_duration=+manualState.audio_duration||0;
    MANUAL.output_path=manualState.output_path||"";
    MANUAL.calendar_path=manualState.calendar_path||MANUAL.calendar_path||"";
    MANUAL.status=manualState.status||"Sẵn sàng";
    MANUAL.error=manualState.error||"";
    const oldScriptTitle=String(MANUAL.script_title||"");
    const nextScriptTitle=String(manualState.script_title||"");
    const uiStoryTitle=String(MANUAL.writer_title||"").trim();
    const stateMatchesUi=!uiStoryTitle||!nextScriptTitle||
      storyTitleKey(uiStoryTitle)===storyTitleKey(nextScriptTitle);
    MANUAL.image_status=stateMatchesUi?
      (manualState.image_generation_status||""):"";
    if(stateMatchesUi&&(manualState.image_pack_path||
                       Number(manualState.image_scene_count||0)>0)){
      MANUAL.image_ready=Number(manualState.image_ready_count||0);
      MANUAL.image_total=Number(manualState.image_scene_count||0);
    }else{
      // Backend xoa gói ảnh khi bắt đầu tiêu đề mới. Không giữ lại bộ đếm
      // của truyện cũ, nếu không nút chính có thể hiểu nhầm là đang resume.
      MANUAL.image_ready=0;
      MANUAL.image_total=0;
    }
    if(Array.isArray(manualState.source_results))
      STORY.source_results=manualState.source_results;
    if(Array.isArray(manualState.source_videos))
      STORY.source_videos=manualState.source_videos;
    if(Array.isArray(manualState.source_clips))
      STORY.source_clips=manualState.source_clips;
    if(Array.isArray(manualState.source_links))
      STORY.source_links=manualState.source_links.join("\n");
    STORY.source_keyword=String(manualState.source_keyword||STORY.source_keyword||"");
    STORY.source_status=String(manualState.source_status||"");
    STORY.cut_status=String(manualState.cut_status||"");
    STORY.cut_done=Number(manualState.cut_done||0);
    STORY.cut_total=Number(manualState.cut_total||0);
    // STATE luôn có các khóa này (kể cả khi giá trị rỗng), vì vậy không được
    // dùng fallback sang script cũ khi backend vừa bắt đầu một tiêu đề mới.
    MANUAL.script_path=String(manualState.script_path||"");
    MANUAL.script_words=+manualState.script_words||0;
    const titleStillFollowsScript=!MANUAL.writer_title||
      storyTitleKey(MANUAL.writer_title)===storyTitleKey(oldScriptTitle);
    MANUAL.script_title=nextScriptTitle;
    if(manualState.content_idea_id!==undefined)
      MANUAL.content_idea_id=String(manualState.content_idea_id||"");
    if(manualState.youtube_description!==undefined)
      MANUAL.youtube_description=String(manualState.youtube_description||"");
    if(Array.isArray(manualState.youtube_tags))
      MANUAL.youtube_tags=manualState.youtube_tags;
    if(manualState.content_outline!==undefined)
      MANUAL.content_outline=String(manualState.content_outline||"");
    if(manualState.rewrite_brief!==undefined)
      MANUAL.rewrite_brief=String(manualState.rewrite_brief||"");
    if(manualState.thumbnail_path!==undefined)
      MANUAL.thumbnail_path=String(manualState.thumbnail_path||"");
    if(manualState.description_path!==undefined)
      MANUAL.description_path=String(manualState.description_path||"");
    // Không được kéo tiêu đề cũ từ tác vụ nền đè lên tiêu đề mới người dùng
    // vừa nhập. Chỉ đồng bộ khi ô tiêu đề vẫn đang bám theo script hiện tại.
    if(nextScriptTitle&&titleStillFollowsScript)
      MANUAL.writer_title=nextScriptTitle;
    if(Array.isArray(manualState.voice_recommendations)&&manualState.voice_recommendations.length){
      VOICE_RECS.items=manualState.voice_recommendations;
      VOICE_RECS.analysis=manualState.voice_analysis||null;
      VOICE_RECS.engine=manualState.voice_recommendations[0].engine||MANUAL.engine;
    }
    if(Array.isArray(manualState.voice_cast)){
      VOICE_RECS.cast=manualState.voice_cast;
      VOICE_RECS.coverage=+manualState.voice_assignment_coverage||0;
      if(manualState.voice_cast.length) VOICE_RECS.engine=MANUAL.engine;
    }
    if(manualState.recommended_voice){
      MANUAL.voice=manualState.recommended_voice;
      if(VOICE_RECS.engine) MANUAL.engine=VOICE_RECS.engine;
    }
    if(manualState.nhac_nen) MANUAL.nhac_ten=manualState.nhac_nen;
    if(MANUAL.script_path && MANUAL.script_path!==_lastScriptPath){
      _lastScriptPath=MANUAL.script_path;
      const generated=await api("/api/story/generated_script").catch(()=>null);
      const scriptBelongsToUi=!MANUAL.writer_title||!nextScriptTitle||
        storyTitleKey(MANUAL.writer_title)===storyTitleKey(nextScriptTitle);
      if(generated&&generated.text&&scriptBelongsToUi){
        MANUAL.text=generated.text;
        MANUAL.script_words=+generated.words||MANUAL.script_words;
        MANUAL.writer_title=generated.title||MANUAL.writer_title;
        if(!MANUAL.name) MANUAL.name=generated.title||"";
        toast(`Đã tự nạp kịch bản ${MANUAL.script_words.toLocaleString("vi-VN")} từ`,"ok");
      }
    }
    const imagePack=String(manualState.image_pack_path||"");
    const imageReady=Number(manualState.image_ready_count||0);
    const promptWaiting=!!manualState.image_prompt_ready;
    if(imagePack&&(imagePack!==STORY.pack||imageReady!==_lastImageReadyCount||
                    (promptWaiting&&imagePack!==_lastPromptPack))){
      const shouldOpen=promptWaiting&&imagePack!==_lastPromptPack;
      const pack=await api("/api/story/image_pack",{
        manifest_path:imagePack,include_prompt:shouldOpen}).catch(()=>null);
      if(pack){
        const stateTitle=String(manualState.script_title||"");
        const packTitle=String(pack.title||"");
        const uiTitle=String(MANUAL.writer_title||"").trim();
        const titleMatchesUi=!uiTitle||!stateTitle||
          storyTitleKey(uiTitle)===storyTitleKey(stateTitle);
        const belongsToState=titleMatchesUi&&(!stateTitle||
          (!!packTitle&&storyTitleKey(packTitle)===storyTitleKey(stateTitle)));
        if(belongsToState){
          STORY.pack=pack.manifest_path||imagePack;
          STORY.packTitle=packTitle||MANUAL.writer_title||"";
          localStorage.setItem("advn_story_image_pack",STORY.pack);
          if(Array.isArray(pack.images)) STORY.imgs=pack.images;
          _lastImageReadyCount=imageReady;
          if(shouldOpen){
            _lastPromptPack=imagePack;
            await showStoryPrompt(pack,true);
          }
        }else if(uiTitle&&stateTitle&&
                  storyTitleKey(uiTitle)!==storyTitleKey(stateTitle)){
          // Người dùng đã nhập tiêu đề mới trong khi project cũ còn dang dở.
          // Không cho prompt/gói ảnh của project cũ lọt vào UI mới.
          if(STORY.pack===imagePack||
             storyTitleKey(STORY.packTitle)===storyTitleKey(stateTitle)){
            STORY.pack=""; STORY.packTitle=""; STORY.imgs=[]; STORY.sel=-1;
            localStorage.removeItem("advn_story_image_pack");
            renderStoryImgs(); renderStoryStage();
          }
          _lastImageReadyCount=imageReady;
          _lastPromptPack=imagePack;
        }
      }
    }
    rerenderMode();
  }
  if(manualFinished){
    const stopped=/dừng/i.test(String(manualState.status||""));
    toast(manualState.error||manualState.status||"Đã xử lý xong",
          manualState.error?"err":stopped?"warn":"ok");
  }
  const videoToolsState=ST.video_tools||{};
  if(videoToolsState.rev!=null&&videoToolsState.rev!==_videoToolsRev){
    _videoToolsRev=videoToolsState.rev;
    VIDEO_TOOLS.server=videoToolsState;
    if(Array.isArray(videoToolsState.download_files))
      VIDEO_TOOLS.download_files=videoToolsState.download_files;
    if(Array.isArray(videoToolsState.search_results))
      VIDEO_TOOLS.search_results=videoToolsState.search_results;
    if(videoToolsState.search_keyword)
      VIDEO_TOOLS.search_keyword=videoToolsState.search_keyword;
    if(videoToolsState.search_provider)
      VIDEO_TOOLS.search_provider=videoToolsState.search_provider;
    if(Array.isArray(videoToolsState.cut_sources))
      VIDEO_TOOLS.cut_inputs=videoToolsState.cut_sources;
    if(Array.isArray(videoToolsState.cut_files))
      VIDEO_TOOLS.cut_files=videoToolsState.cut_files;
    if(videoToolsState.download_output_dir){
      VIDEO_TOOLS.download_output=videoToolsState.download_output_dir;
      localStorage.setItem("advn_vt_download_output",VIDEO_TOOLS.download_output);
    }
    if(videoToolsState.cut_output_dir){
      VIDEO_TOOLS.cut_output=videoToolsState.cut_output_dir;
      localStorage.setItem("advn_vt_cut_output",VIDEO_TOOLS.cut_output);
    }
    if(MODE==="tools") renderVideoTools();
  }
  const ideaState=ST.content_pipeline||{};
  const ideaFinished=_ideasWorking&&!ideaState.working;
  if(ideaState.working&&ideaState.active) _ideasLastActive=ideaState.active;
  _ideasWorking=!!ideaState.working;
  if(ideaState.rev!=null&&ideaState.rev!==_ideasRev){
    _ideasRev=ideaState.rev;
    IDEAS.server=ideaState;
    if(Array.isArray(ideaState.search_results)){
      const selected=new Map(IDEAS.sourceResults.map(x=>[x.url,x.selected!==false]));
      IDEAS.sourceResults=ideaState.search_results.map(x=>({...x,
        selected:selected.has(x.url)?selected.get(x.url):x.selected!==false}));
    }
    if(ideaState.search_keyword) IDEAS.onlineKeyword=ideaState.search_keyword;
    if(MODE==="ideas"){renderIdeaStatus();renderIdeaSourceResults();}
  }
  // Khi API đang chờ phản hồi, backend có thể chưa phát sinh rev mới. Vẫn vẽ
  // lại để đồng hồ "đã chờ" chạy, giúp phân biệt đang làm với bị treo.
  else if(MODE==="ideas"&&ideaState.working) renderIdeaStatus();
  if(ideaFinished){
    await ideasLoad(true);
    if(_ideasLastActive==="source_download"&&Number(ideaState.download_success)>0)
      setIdeasTab("library");
    toast(ideaState.error||ideaState.status||"Đã xử lý kho ý tưởng",
          ideaState.error?"err":"ok");
    _ideasLastActive="";
  }
  const readyDownloads=[];
  const failedDownloads=[];
  let openedReview=false;
  let reviewChanged=false;
  for(const j of (ST.queue||[])){
    const old=_queuePrev[j.id];
    if(old&&old.status==="đang tải"&&j.status!=="đang tải"){
      if(j.status==="lỗi") failedDownloads.push(j);
      else if(j.path) readyDownloads.push(j);
    }
    const nowReview=typeof jobNeedsCaptionReview==="function" &&
      jobNeedsCaptionReview(j);
    const wasReview=!!(old && typeof jobNeedsCaptionReview==="function" &&
      jobNeedsCaptionReview(old));
    if(nowReview && !wasReview){
      toast("Cần kiểm tra phụ đề Trung. Mở thẻ Nhận dạng hoặc bấm 📂 kiểm tra phụ đề.","warn");
      if(!openedReview && MODE==="dub" && j.id===ST.selected){
        try{ setTab("asr"); openedReview=true; }catch(_tab){}
      }
    }
    if(nowReview && old && (old.review_dir!==j.review_dir ||
        JSON.stringify(old.review_gaps||[])!==JSON.stringify(j.review_gaps||[]))){
      if(typeof _captionReview!=="undefined") delete _captionReview[j.id];
      if(j.id===JID) reviewChanged=true;
    }
    if(!nowReview && typeof _captionReview!=="undefined" && _captionReview[j.id]) delete _captionReview[j.id];
  }
  if(MODE==="dub" && TAB==="asr" &&
      (reviewChanged || wasBusy!==!!(ST.running||ST.busy))) renderPanel();
  document.getElementById("cuda").innerHTML = ST.nvenc?"<i></i> CUDA sẵn sàng":"<i></i> chạy CPU";
  const topGpu=document.getElementById("topGpu");
  if(topGpu) topGpu.innerHTML=`<span>▧</span> GPU ${ST.nvenc?"NVENC":"CPU"} <i></i>`;
  document.getElementById("qcount").textContent=ST.queue.length+" mục";
  // Download badge
  const dlActive = (ST.queue||[]).filter(x=>x.status==="đang tải").length;
  const dlWaiting = (ST.queue||[]).filter(x=>x.status==="chờ tải").length;
  const dlBadge = document.getElementById("dlcount");
  if(dlBadge){
    if(dlActive + dlWaiting > 0){
      dlBadge.textContent = `⇩ ${dlActive}${dlWaiting ? " +" + dlWaiting : ""}`;
      dlBadge.style.display = "";
    } else {
      dlBadge.style.display = "none";
    }
  }
  const q=document.getElementById("queue");
  q.innerHTML=ST.queue.map(j=>{
    const review=typeof jobNeedsCaptionReview==="function" &&
      jobNeedsCaptionReview(j);
    const syncReview=j.result_status==="SYNC_CHECK_FAILED";
    const attention=review||syncReview||j.status==="cần kiểm tra";
    const cls=j.status==="xong"?"done":j.status==="lỗi"?"err":
              attention?"warn":
              (j.status==="dang chay"||j.status==="đang tải")?"run":"";
    const jpct=Number(j.progress||0);
    const pct=j.status==="xong"?100:
      (j.status==="đang tải"?Math.max(5,jpct):
       (j.id===ST.selected&&ST.running?(ST.progress.pct||0):jpct));
    const note=esc(j.note||j.status);
    const folder=review&&j.review_dir?j.review_dir:j.output;
    const folderLabel=review&&j.review_dir?"📂 kiểm tra phụ đề":"📂 mở thư mục";
    const reviewLabel=review?"cần kiểm tra phụ đề":
      (syncReview?"cần kiểm tra khớp hình":note);
    return `<div class="qitem ${j.id===ST.selected?"sel":""}${attention?" review":""}" onclick="selectJob(${j.id})">
      <div class="qx" onclick="delJob(${j.id},event)">×</div>
      <div class="qtop"><span class="qdot ${cls}"></span>
        <span class="qname" title="${esc(j.name)}">${esc(j.name)}</span></div>
      <div class="qbar"><i style="width:${pct}%"></i></div>
      <div class="qfoot">${folder?`<span class="lplay" style="margin-right:auto"
         onclick="event.stopPropagation();openCaptionReview(${j.id},event,'${esc(folder).replace(/\\/g,"\\\\")}')"
         >${folderLabel}</span>`:""}<span class="qstatus" title="${note}">${reviewLabel}</span></div></div>`;
  }).join("")||`<div class="hint" style="margin:8px 4px">Hàng đợi trống.</div>`;
  try{
    const selectedJob=(ST.queue||[]).find(j=>j.id===ST.selected);
    const asrTab=document.querySelector('#tabs [data-t="asr"]');
    const needsReview=typeof jobNeedsCaptionReview==="function" && jobNeedsCaptionReview(selectedJob);
    if(asrTab) asrTab.classList.toggle("need-review", !!needsReview);
    if(!_reviewTabNudged && MODE==="dub" && needsReview){
      _reviewTabNudged=true;
      if(TAB!=="asr") setTab("asr");
      else renderPanel();
    }
  }catch(_reviewUi){}

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
  if(JID && ST.rev!=null && ST.rev!==_lastRev && !_isDragging && !saveTimer && !_projectSaveInFlight){
    const id=JID, revision=_editorRevision, serverRevision=ST.rev;
    const np=await api("/api/project?id="+id).catch(()=>null);
    if(np&&np.w&&JID===id&&revision===_editorRevision&&!_isDragging&&!saveTimer&&!_projectSaveInFlight){
      _lastRev=serverRevision;
      PR=np;
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
    status:j.status,path:j.path,note:j.note,
    result_status:j.result_status,review_dir:j.review_dir,review_gaps:j.review_gaps||[]
  }]));
}

/* ======================= khởi động ======================= */
V().addEventListener("timeupdate",tick);
V().addEventListener("seeked",tick);
for(const event of ["loadedmetadata","loadeddata","seeked","play","pause","ended","emptied"]){
  V().addEventListener(event,()=>{
    syncPlaybackControls();
    requestPreviewRender("MEDIA_"+event.toUpperCase());
    tick();
  });
}
// Editor gestures (overlay/handle) win over click-to-play, including when the
// original hit target would have been the center Play icon.
document.getElementById("vwrap").addEventListener("pointerdown", onPreviewPointerDown, true);
document.addEventListener("pointerdown",()=>{
  if(!_isDragging){ _editorClickPointer=null; _suppressPlayClick=false; }
},true);
document.addEventListener("click",e=>{
  if(!_suppressPlayClick) return;
  _suppressPlayClick=false;
  _editorClickPointer=null;
  e.preventDefault();
  e.stopImmediatePropagation();
},true);
/* Click vào vùng video (không phải box) = play/pause */
document.getElementById("vwrap").addEventListener("click",function(e){
  if(_isDragging||_suppressPlayClick) return;
  if(e.target.closest(".box")) return;
  togglePlay();
});
window.addEventListener("resize",()=>{applyZoom();});
const _editorLayoutObserver=new ResizeObserver(()=>applyZoom());
_editorLayoutObserver.observe(document.getElementById("stage"));
const _editorVideoObserver=new ResizeObserver(()=>requestPreviewRender("VIDEO_SIZE_CHANGED"));
_editorVideoObserver.observe(V());
document.addEventListener("keydown",e=>{
  if(e.key==="Escape"&&document.getElementById("settingsModal")?.classList.contains("open")){
    closeSettings(); return;
  }
  if(e.target.tagName==="INPUT"||e.target.tagName==="SELECT"||
     e.target.tagName==="TEXTAREA"||e.target.isContentEditable) return;
  if(e.code==="Space"&&e.target.tagName!=="BUTTON"){e.preventDefault();togglePlay();}
  if(e.key==="ArrowLeft")jump(-5);
  if(e.key==="ArrowRight")jump(5);
  if(e.key==="Delete"&&ACT&&ACT.kind==="rgn"){PR.regions.splice(ACT.i,1);ACT=null;save();draw();renderPanel();}
});
/* Chạy trong trình duyệt (không phải app desktop) thì ẩn nút cửa sổ */
function initDesktop(){
  if(!DESK()){
    const w=document.getElementById("wbtns"); if(w) w.style.display="none";
    document.getElementById("titlebar").style.display="none";
  }
}
window.addEventListener("pywebviewready",initDesktop);
setTimeout(initDesktop,900);
