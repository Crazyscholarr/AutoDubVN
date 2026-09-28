"use strict";
function setVideoToolsTab(tab){
  VIDEO_TOOLS.tab=tab==="cut"?"cut":"download";
  localStorage.setItem("advn_video_tools_tab",VIDEO_TOOLS.tab);
  renderVideoTools();
}
function videoToolName(path){
  const value=String(path||"");
  return value.split(/[\\/]/).pop()||value;
}
function videoToolFileRows(paths,removable){
  const list=Array.isArray(paths)?paths:[];
  if(!list.length) return `<div class="vt-empty">Chưa có file nào trong danh sách.</div>`;
  return `<div class="vt-files">${list.map((path,index)=>`<div class="vt-file" title="${esc(path)}">
    <span class="vt-file-no">${index+1}</span><span class="vt-file-info">
      <b>${esc(videoToolName(path))}</b><small>${esc(path)}</small></span>
    ${removable?`<button class="btn sm danger" onclick="videoToolRemoveCutInput(${index})" title="Bỏ khỏi danh sách">×</button>`:""}
  </div>`).join("")}</div>`;
}
function videoToolStatus(kind){
  const state=VIDEO_TOOLS.server||{};
  const isDownload=kind==="download";
  const active=state.active===kind&&state.working;
  const status=String(isDownload?state.download_status||"":state.cut_status||"");
  const pct=Math.max(0,Math.min(100,Number(isDownload?state.download_pct:state.cut_pct)||0));
  const done=Number(isDownload?state.download_done:state.cut_done)||0;
  const total=Number(isDownload?state.download_total:state.cut_total)||0;
  if(!active&&!status&&!state.error) return "";
  return `<div class="vt-status">
    <div class="vt-status-head"><b>${active?"Đang xử lý":"Trạng thái"}${total?` · ${done}/${total}`:""}</b>
      <strong>${Math.round(pct)}%</strong></div>
    <div class="vt-progress"><i style="width:${pct}%"></i></div>
    <div>${esc(status||(state.error?"Có lỗi xảy ra":"Đang chuẩn bị…"))}</div>
    ${state.error?`<div class="vt-error">${esc(state.error)}</div>`:""}
  </div>`;
}
function renderVideoTools(){
  const body=document.getElementById("videoToolsBody"); if(!body) return;
  document.getElementById("vtTabDownload")?.classList.toggle("on",VIDEO_TOOLS.tab==="download");
  document.getElementById("vtTabCut")?.classList.toggle("on",VIDEO_TOOLS.tab==="cut");
  const state=VIDEO_TOOLS.server||{};
  const working=!!state.working;
  const appBusy=!!ST.running&&!working;
  if(VIDEO_TOOLS.tab==="download"){
    const files=VIDEO_TOOLS.download_files||[];
    const selected=new Set(VIDEO_TOOLS.search_selected||[]);
    const searchRows=(VIDEO_TOOLS.search_results||[]).map((row,index)=>{
      const url=String(row.url||"");
      const meta=`${row.duration?fmt(row.duration):"?"} · ${row.channel||row.provider||"Video nền"}`;
      return `<label class="story-source-row"><input type="checkbox" ${selected.has(url)?"checked":""}
        onchange="videoToolToggleSearchResult(decodeURIComponent('${encodeURIComponent(url)}'),this.checked)">
        <span><b>${index+1}. ${esc(row.title||url)}</b><small>${esc(meta)}</small></span></label>`;
    }).join("");
    body.innerHTML=`<div class="vt-grid">
      <section class="vt-card"><h3>⇩ Tải video nền cho phần làm Audio</h3>
        <p class="vt-desc">Kho tải riêng cho video nền. Link ở đây không được coi là tư liệu tham khảo viết truyện.</p>
        <div class="vt-options"><div class="vt-field"><label>Nguồn tìm kiếm</label>
          <select id="vtSearchProvider" ${working?"disabled":""}>
            <option value="all" ${VIDEO_TOOLS.search_provider==="all"?"selected":""}>Bilibili + YouTube + Douyin</option>
            <option value="bilibili" ${VIDEO_TOOLS.search_provider==="bilibili"?"selected":""}>Chỉ Bilibili</option>
            <option value="youtube" ${VIDEO_TOOLS.search_provider==="youtube"?"selected":""}>Chỉ YouTube</option>
            <option value="douyin" ${VIDEO_TOOLS.search_provider==="douyin"?"selected":""}>Chỉ Douyin</option>
            <option value="tiktok" ${VIDEO_TOOLS.search_provider==="tiktok"?"selected":""}>Chỉ TikTok</option>
            <option value="kuaishou" ${VIDEO_TOOLS.search_provider==="kuaishou"?"selected":""}>Chỉ Kuaishou</option>
          </select></div><div class="vt-field"><label>Số video muốn tìm</label>
          <input id="vtSearchCount" type="number" min="1" max="50" value="${VIDEO_TOOLS.search_count}" ${working?"disabled":""}></div></div>
        <div class="vt-field"><label>Từ khóa video nền (Việt / Trung / Anh)</label><div class="vt-folder-row">
          <input id="vtSearchKeyword" value="${esc(VIDEO_TOOLS.search_keyword)}" ${working?"disabled":""}
            placeholder="婆媳矛盾 / cảnh làng quê / rural rainy walk">
          <button class="btn" ${working||appBusy?"disabled":""} onclick="videoToolSearch()">⌕ Tìm video</button>
        </div></div>
        ${searchRows?`<div class="story-source-results">${searchRows}</div>`:""}
        ${state.search_status?`<div class="vt-search-status">${esc(state.search_status)}</div>`:""}
        <div class="vt-separator"><span>HOẶC DÁN LINK TRỰC TIẾP</span></div>
        <div class="vt-field"><label>Link Bilibili / YouTube / Douyin / TikTok — mỗi dòng một link</label>
          <textarea id="vtDownloadLinks" ${working?"disabled":""}
            placeholder="https://www.bilibili.com/video/BV...\nhttps://www.youtube.com/watch?v=...\nhttps://www.douyin.com/video/...\nhttps://v.douyin.com/xxxx">${esc(VIDEO_TOOLS.links)}</textarea></div>
        <div class="vt-field"><label>Thư mục lưu video</label><div class="vt-folder-row">
          <input id="vtDownloadOutput" value="${esc(VIDEO_TOOLS.download_output)}" ${working?"disabled":""}
            placeholder="Để trống: tự tạo trong downloads/audio_background">
          <button class="btn" ${working?"disabled":""} onclick="videoToolPickOutput('download')">📁 Chọn thư mục</button>
        </div></div>
        <div class="vt-options"><div class="vt-field"><label>Chất lượng video nền</label>
          <select id="vtDownloadQuality" ${working?"disabled":""}>
            <option value="360" ${VIDEO_TOOLS.quality==="360"?"selected":""}>Tối đa 360p · nhẹ</option>
            <option value="480" ${VIDEO_TOOLS.quality==="480"?"selected":""}>Tối đa 480p · tiết kiệm dung lượng</option>
            <option value="720" ${VIDEO_TOOLS.quality==="720"?"selected":""}>Tối đa 720p</option>
            <option value="1080" ${VIDEO_TOOLS.quality==="1080"?"selected":""}>Tối đa 1080p</option>
            <option value="best" ${VIDEO_TOOLS.quality==="best"?"selected":""}>Nét tốt nhất có thể</option>
          </select></div><div class="vt-field"><label>Xử lý đồng thời</label>
          <input value="Tối đa 3 video" disabled></div></div>
        <div class="vt-actions">
          ${working&&state.active==="download"?`<button class="btn danger" onclick="cancelRun()">■ Dừng tải</button>`:""}
          <button class="btn pri" ${working||appBusy?"disabled":""} onclick="videoToolRunDownload()">⇩ TẢI VIDEO NỀN</button>
        </div>${appBusy?`<div class="vt-note">Một tác vụ khác của AutoDubVN đang chạy. Hãy chờ tác vụ đó hoàn tất.</div>`:""}
        ${videoToolStatus("download")}
      </section>
      <aside class="vt-card"><div class="vt-summary"><span>Video đã tải</span><b>${files.length} file</b></div>
        ${videoToolFileRows(files,false)}
        <div class="vt-actions">
          <button class="btn" ${!files.length?"disabled":""} onclick="videoToolOpenFolder('download')">📂 Mở thư mục</button>
          <button class="btn" ${!files.length?"disabled":""} onclick="videoToolUseDownloadsForCut()">✂ Đưa sang cắt</button>
          <button class="btn" ${!files.length?"disabled":""} onclick="videoToolImportToStory('download')">🎬 Dùng cho Video kể chuyện</button>
        </div><div class="vt-note">Tải xong vẫn nằm trong kho riêng. Chỉ khi bấm “Dùng cho Video kể chuyện”, các file mới được nhập vào phần Audio.</div>
      </aside></div>`;
  }else{
    const inputs=VIDEO_TOOLS.cut_inputs||[], files=VIDEO_TOOLS.cut_files||[];
    body.innerHTML=`<div class="vt-grid">
      <section class="vt-card"><h3>✂ Cắt nhiều video cùng lúc</h3>
        <p class="vt-desc">Chọn nhiều file trong cùng hộp thoại, hoặc thêm cả thư mục. Mỗi video được cắt nhanh thành các đoạn nhỏ.</p>
        <div class="vt-actions" style="margin-top:0">
          <button class="btn pri" ${working?"disabled":""} onclick="videoToolPickCutVideos()">▣ Chọn nhiều video cùng lúc</button>
          <button class="btn" ${working?"disabled":""} onclick="videoToolPickCutFolder()">📁 Thêm thư mục video</button>
          <button class="btn danger" ${working||!inputs.length?"disabled":""} onclick="videoToolClearCutInputs()">Xoá danh sách</button>
        </div>
        <div class="vt-summary" style="margin-top:13px"><span>Danh sách nguồn đã chọn</span><b>${inputs.length} mục</b></div>
        ${videoToolFileRows(inputs,true)}
        <div class="vt-field" style="margin-top:13px"><label>Thư mục lưu các đoạn đã cắt</label><div class="vt-folder-row">
          <input id="vtCutOutput" value="${esc(VIDEO_TOOLS.cut_output)}" ${working?"disabled":""}
            placeholder="Để trống: tự tạo trong downloads/video_segments">
          <button class="btn" ${working?"disabled":""} onclick="videoToolPickOutput('cut')">📁 Chọn thư mục</button>
        </div></div>
        <div class="vt-options">
          <div class="vt-field"><label>Đoạn ngắn nhất (phút)</label><input id="vtCutMin" type="number" min="0.1" max="180" step="0.5" value="${VIDEO_TOOLS.min_minutes}" ${working?"disabled":""}></div>
          <div class="vt-field"><label>Đoạn dài nhất (phút)</label><input id="vtCutMax" type="number" min="0.1" max="180" step="0.5" value="${VIDEO_TOOLS.max_minutes}" ${working?"disabled":""}></div>
        </div><div class="vt-actions">
          ${working&&state.active==="cut"?`<button class="btn danger" onclick="cancelRun()">■ Dừng cắt</button>`:""}
          <button class="btn pri" ${working||appBusy||!inputs.length?"disabled":""} onclick="videoToolRunCut()">✂ CẮT TOÀN BỘ VIDEO ĐÃ CHỌN</button>
        </div>${appBusy?`<div class="vt-note">Một tác vụ khác của AutoDubVN đang chạy. Hãy chờ tác vụ đó hoàn tất.</div>`:""}
        ${videoToolStatus("cut")}
      </section>
      <aside class="vt-card"><div class="vt-summary"><span>Kết quả đã cắt</span><b>${files.length} đoạn</b></div>
        ${videoToolFileRows(files,false)}
        <div class="vt-actions"><button class="btn" ${!files.length?"disabled":""} onclick="videoToolOpenFolder('cut')">📂 Mở thư mục</button>
          <button class="btn pri" ${!files.length?"disabled":""} onclick="videoToolImportToStory('cut')">🎲 Dùng làm kho random cho Audio</button></div>
        <div class="vt-note">Sau khi nhập, phần Video kể chuyện có thể random các đoạn này cho tới khi đủ thời lượng audio.</div>
      </aside></div>`;
  }
}
function videoToolToggleSearchResult(url,on){
  const selected=new Set(VIDEO_TOOLS.search_selected||[]);
  if(on) selected.add(url); else selected.delete(url);
  VIDEO_TOOLS.search_selected=[...selected];
}
async function videoToolSearch(){
  const keyword=(document.getElementById("vtSearchKeyword")?.value||VIDEO_TOOLS.search_keyword||"").trim();
  if(!keyword) return toast("Hãy nhập từ khóa tìm video nền","warn");
  const provider=document.getElementById("vtSearchProvider")?.value||"all";
  const limit=Math.max(1,Math.min(50,Number(document.getElementById("vtSearchCount")?.value)||10));
  VIDEO_TOOLS.search_keyword=keyword; VIDEO_TOOLS.search_provider=provider; VIDEO_TOOLS.search_count=limit;
  VIDEO_TOOLS.server={...(VIDEO_TOOLS.server||{}),search_status:"Đang tìm video nền…",error:""};
  renderVideoTools();
  try{
    const r=await api("/api/tools/search_videos",{keyword,provider,limit});
    VIDEO_TOOLS.search_results=r.results||[]; VIDEO_TOOLS.search_selected=[];
    VIDEO_TOOLS.server={...(VIDEO_TOOLS.server||{}),search_status:`Tìm thấy ${VIDEO_TOOLS.search_results.length} video`,error:""};
    renderVideoTools(); toast(`Tìm thấy ${VIDEO_TOOLS.search_results.length} video nền`,"ok");
  }catch(e){
    VIDEO_TOOLS.server={...(VIDEO_TOOLS.server||{}),search_status:"Tìm video thất bại",error:e.message||String(e)};
    renderVideoTools(); toast(e.message||String(e),"err");
  }
}
async function videoToolPickOutput(kind){
  let folder="";
  if(DESK()){
    try{ folder=await pywebview.api.pick_folder(); }
    catch(e){ return toast(e.message||String(e),"err"); }
  }else folder=prompt("Dán đường dẫn thư mục lưu:")||"";
  if(!folder) return;
  if(kind==="cut"){
    VIDEO_TOOLS.cut_output=String(folder); localStorage.setItem("advn_vt_cut_output",VIDEO_TOOLS.cut_output);
  }else{
    VIDEO_TOOLS.download_output=String(folder); localStorage.setItem("advn_vt_download_output",VIDEO_TOOLS.download_output);
  }
  renderVideoTools();
}
async function videoToolPickCutVideos(){
  let files=[];
  if(DESK()){
    try{ files=await pywebview.api.pick_video(); }
    catch(e){ return toast(e.message||String(e),"err"); }
    if(files&&files.error) return toast(files.error,"err");
  }else{
    const raw=prompt("Dán đường dẫn video, mỗi dòng một file:")||"";
    files=raw.split(/[\r\n]+/).map(x=>x.trim()).filter(Boolean);
  }
  if(!Array.isArray(files)) files=files?[files]:[];
  VIDEO_TOOLS.cut_inputs=[...new Set([...(VIDEO_TOOLS.cut_inputs||[]),...files.map(String)])];
  if(files.length) toast(`Đã thêm ${files.length} video vào danh sách cắt`,"ok");
  renderVideoTools();
}
async function videoToolPickCutFolder(){
  let folder="";
  if(DESK()){
    try{ folder=await pywebview.api.pick_folder(); }
    catch(e){ return toast(e.message||String(e),"err"); }
  }else folder=prompt("Dán đường dẫn thư mục video:")||"";
  if(!folder) return;
  VIDEO_TOOLS.cut_inputs=[...new Set([...(VIDEO_TOOLS.cut_inputs||[]),String(folder)])];
  renderVideoTools();
}
function videoToolRemoveCutInput(index){ VIDEO_TOOLS.cut_inputs.splice(index,1); renderVideoTools(); }
function videoToolClearCutInputs(){ VIDEO_TOOLS.cut_inputs=[]; renderVideoTools(); }
async function videoToolRunDownload(){
  const pasted=document.getElementById("vtDownloadLinks")?.value||VIDEO_TOOLS.links||"";
  const links=[...new Set([...(VIDEO_TOOLS.search_selected||[]),
    ...String(pasted).split(/[\r\n]+/).map(x=>x.trim()).filter(Boolean)])].join("\n");
  const output=document.getElementById("vtDownloadOutput")?.value.trim()||"";
  const quality=document.getElementById("vtDownloadQuality")?.value||VIDEO_TOOLS.quality;
  if(!String(links).trim()) return toast("Hãy chọn kết quả tìm kiếm hoặc dán ít nhất một link video","warn");
  VIDEO_TOOLS.links=String(pasted); VIDEO_TOOLS.download_output=output; VIDEO_TOOLS.quality=quality;
  localStorage.setItem("advn_vt_download_output",output); localStorage.setItem("advn_vt_quality",quality);
  try{
    const r=await api("/api/tools/download_videos",{links,output_dir:output,quality});
    VIDEO_TOOLS.download_output=r.output_dir||output;
    VIDEO_TOOLS.server={...(VIDEO_TOOLS.server||{}),working:true,active:"download",error:"",
      download_status:`Đang tải ${r.total||1} video…`,download_pct:0};
    renderVideoTools(); toast("Đã bắt đầu tải video nền cho Audio","ok");
  }catch(e){ toast(e.message||String(e),"err"); }
}
async function videoToolRunCut(){
  const inputs=VIDEO_TOOLS.cut_inputs||[];
  if(!inputs.length) return toast("Hãy chọn một hoặc nhiều video cần cắt","warn");
  const output=document.getElementById("vtCutOutput")?.value.trim()||"";
  const min=Math.max(.1,Number(document.getElementById("vtCutMin")?.value)||5);
  const max=Math.max(min,Number(document.getElementById("vtCutMax")?.value)||10);
  VIDEO_TOOLS.cut_output=output; VIDEO_TOOLS.min_minutes=min; VIDEO_TOOLS.max_minutes=max;
  localStorage.setItem("advn_vt_cut_output",output);
  localStorage.setItem("advn_vt_min_minutes",String(min)); localStorage.setItem("advn_vt_max_minutes",String(max));
  try{
    const r=await api("/api/tools/cut_videos",{paths:inputs,output_dir:output,min_seconds:min*60,max_seconds:max*60});
    VIDEO_TOOLS.cut_output=r.output_dir||output;
    VIDEO_TOOLS.server={...(VIDEO_TOOLS.server||{}),working:true,active:"cut",error:"",
      cut_status:`Đang cắt ${r.total||inputs.length} video…`,cut_pct:0};
    renderVideoTools(); toast(`Đã bắt đầu cắt ${r.total||inputs.length} video`,"ok");
  }catch(e){ toast(e.message||String(e),"err"); }
}
function videoToolUseDownloadsForCut(){
  VIDEO_TOOLS.cut_inputs=[...new Set([...(VIDEO_TOOLS.cut_inputs||[]),...(VIDEO_TOOLS.download_files||[])])];
  setVideoToolsTab("cut"); toast(`Đã đưa ${VIDEO_TOOLS.download_files.length} video sang danh sách cắt`,"ok");
}
async function videoToolOpenFolder(kind){
  const path=kind==="cut"?VIDEO_TOOLS.cut_output:VIDEO_TOOLS.download_output;
  if(path) await openOut(path); else toast("Chưa có thư mục kết quả","warn");
}
function videoToolImportToStory(kind){
  const files=kind==="cut"?(VIDEO_TOOLS.cut_files||[]):(VIDEO_TOOLS.download_files||[]);
  if(!files.length) return toast("Chưa có file để nhập","warn");
  STORY.source_videos=[...new Set([...(STORY.source_videos||[]),...files])];
  STORY.source_sel=0; STORY_MEDIA_MODE="video"; STORY.step=6;
  localStorage.setItem("advn_story_media_mode","video"); localStorage.setItem("advn_story_step","6");
  setMode("story"); renderStoryMedia(); storyUpdateVideoInfo(); renderStory();
  toast(`Đã nhập ${files.length} video vào kho nền Audio`,"ok");
}
