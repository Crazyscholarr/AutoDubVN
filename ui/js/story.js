"use strict";
function storyDims(){
  if(STORY.aspect==="9:16") return {w:1080,h:1920};
  if(STORY.aspect==="1:1") return {w:1080,h:1080};
  return {w:1920,h:1080};
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
  const countEl = document.getElementById("simgcount");
  if(countEl) countEl.textContent = STORY_MEDIA_MODE==="image" ? STORY.imgs.length+" ảnh" : (STORY.source_videos ? STORY.source_videos.length : 0)+" video";
  el.innerHTML=STORY.imgs.map((p,i)=>storyThumb(p,i)).join("")
    ||`<div class="hint" style="grid-column:1/-1">Chưa có ảnh nào.<br>
       Bấm <b>+ Thêm ảnh</b> (chọn được nhiều ảnh một lúc) hoặc
       <b>+ Thư mục ảnh</b>. Ảnh được chia đều theo độ dài giọng đọc.</div>`;
}
function renderStoryStage(){
  const st=document.getElementById("sstage"); if(!st) return;
  st.classList.toggle("doc",STORY.aspect==="9:16");
  st.classList.toggle("square",STORY.aspect==="1:1");
  const firstImg=STORY.imgs.find(p=>_IMG_EXT.test(p));
  const sel=(STORY.sel>=0&&STORY.imgs[STORY.sel]&&_IMG_EXT.test(STORY.imgs[STORY.sel]))
            ?STORY.imgs[STORY.sel]:firstImg;
  const sourceVideos=STORY.source_videos||[];
  const sourceIndex=Math.max(0,Math.min(sourceVideos.length-1,Number(STORY.source_sel)||0));
  const sourceVideo=sourceVideos[sourceIndex]||"";
  let inner="";
  if(MANUAL.output_path){
    inner=`<video controls preload="metadata" src="/api/manual/output?v=${_manualRev}"></video>`;
  }else if(STORY_MEDIA_MODE==="video"&&sourceVideo){
    const z=(Number(STORY.source_zoom)||100)/100;
    const fit=z<1?"contain":"cover";
    const clip=`inset(${STORY.source_crop_top||0}% ${STORY.source_crop_right||0}% ${STORY.source_crop_bottom||0}% ${STORY.source_crop_left||0}%)`;
    inner=`<video id="storySourcePreview" controls muted preload="metadata"
      src="/api/local_video?path=${encodeURIComponent(sourceVideo)}"
      style="object-fit:${fit};object-position:${STORY.source_x}% ${STORY.source_y}%;transform:scale(${z});clip-path:${clip}"></video>
      <div class="story-transform-help">Kéo hình để đổi tâm · lăn chuột để zoom</div>`;
  }else if(sel){
    inner=`<img src="/api/local_image?path=${encodeURIComponent(sel)}">`;
  }else{
    inner=`<div class="sempty"><b>Khung xem trước ${STORY.aspect}</b>
      ${STORY_MEDIA_MODE==="video"?"Thêm video ở cột trái để crop, kéo và zoom":"Thêm ảnh ở cột trái để xem bố cục"}</div>`;
  }
  if(!MANUAL.output_path&&STORY.logo_enabled&&STORY.logo_path){
    const margin="1.8%", pos=STORY.logo_position;
    const corner=pos==="top-left"?`left:${margin};top:${margin}`:
      pos==="bottom-left"?`left:${margin};bottom:${margin}`:
      pos==="bottom-right"?`right:${margin};bottom:${margin}`:
      `right:${margin};top:${margin}`;
    inner+=`<img class="story-logo-preview"
      src="/api/local_image?path=${encodeURIComponent(STORY.logo_path)}"
      style="${corner};width:${STORY.logo_width}%;opacity:${STORY.logo_opacity/100}">`;
  }
  if(!MANUAL.output_path && STORY.character_enabled){
    const charW = Math.max(8, Math.min(32, Math.round(18 * (STORY.character_scale || 1))));
    const charOp = (STORY.character_opacity || 92) / 100;
    inner+=`<div class="story-character-preview" aria-label="Nhân vật chỉ dẫn" style="width:${charW}%;opacity:${charOp}">
      <img src="/api/local_image?path=assets%2Fnhan_vat_mit.png" alt="Nhân vật quả mít"></div>`;
  }
  if(!MANUAL.output_path && STORY.sub_enabled){
    const firstLine=(MANUAL.text||"Phụ đề mẫu sẽ hiển thị như thế này")
      .trim().split(/[\r\n]+/)[0].slice(0,90)||"Phụ đề mẫu";
    const s=STORY.sub, d=storyDims();
    const pos=s.align==="top-center"?"top:5.5%":
              s.align==="mid-center"?"top:50%;transform:translateY(-50%)":
              `bottom:${Math.round(s.margin_v/d.h*100)}%`;
    const previewHeight=STORY.aspect==="16:9"
      ?(document.getElementById("sstage").clientWidth*9/16||300)
      :(document.getElementById("sstage").clientHeight||520);
    const px=Math.max(11,Math.round(s.size/d.h*previewHeight));
    inner+=`<div class="ssub" style="${pos};color:${s.color};
      font-size:${px}px;${s.bold?"":"font-weight:400;"}">${esc(firstLine)}</div>`;
  }
  st.innerHTML=inner;
  bindStoryVideoStage();
  const previewVideo=st.querySelector("video");
  if(previewVideo){
    const sync=()=>{
      const el=document.getElementById("storyTime");
      if(el) el.textContent=`${fmt(previewVideo.currentTime)} / ${fmt(previewVideo.duration||0)}`;
    };
    previewVideo.addEventListener("loadedmetadata",sync);
    previewVideo.addEventListener("timeupdate",sync);
  }else{
    const time=document.getElementById("storyTime"); if(time) time.textContent="00:00 / 00:00";
  }
  const aspectBadge=document.getElementById("storyAspectBadge");
  if(aspectBadge) aspectBadge.textContent=STORY.aspect;
  const sceneBadge=document.getElementById("storySceneBadge");
  if(sceneBadge){
    const at=STORY.imgs.length?Math.max(1,STORY.sel+1):0;
    sceneBadge.textContent=`Cảnh ${at}/${STORY.imgs.length}`;
  }
  const previewTitle=document.getElementById("storyPreviewTitle");
  if(previewTitle) previewTitle.textContent=MANUAL.writer_title||MANUAL.name||"Video kể chuyện AI";
  const info=document.getElementById("sinfo");
  if(info){
    const n=(MANUAL.text||"").length;
    const estMin=n?(n/18/60):0;   // giọng Việt đọc ~18-20 ký tự/giây
    const mediaText=STORY_MEDIA_MODE==="video"
      ?`${(STORY.source_videos||[]).length} video nguồn`:`${STORY.imgs.length} mục ảnh`;
    info.innerHTML=`Khổ <b>${STORY.aspect}</b> · ${mediaText}
      ${STORY.pack?` · <span title="${esc(STORY.pack)}">📦 đã lưu gói ảnh</span>`:""}
      · ${n.toLocaleString("vi-VN")} ký tự${n?` · giọng đọc ước ~<b>${estMin.toFixed(1)} phút</b>`:""}
      ${MANUAL.audio_duration?` · audio hiện có <b>${fmt(MANUAL.audio_duration)}</b>`:""}
      ${MANUAL.output_path?` · <span class="lplay" onclick="openManualFolder('output')">📂 mở thư mục video</span>`:""}
      ${MANUAL.calendar_path?` · <span class="lplay" onclick="openOut('${esc(MANUAL.calendar_path).replace(/\\/g,"\\\\")}')">📊 mở Excel lịch nội dung</span>`:""}
      ${MANUAL.thumbnail_path?` · <span class="lplay" onclick="openOut('${esc(MANUAL.thumbnail_path).replace(/\\/g,"\\\\")}')">🖼 thumbnail</span>`:""}
      ${MANUAL.description_path?` · <span class="lplay" onclick="openOut('${esc(MANUAL.description_path).replace(/\\/g,"\\\\")}')">📄 mô tả YouTube</span>`:""}`;
  }
}
function renderStoryPanel(){
  const P=document.getElementById("spanel"); if(!P) return;
  const eng=MANUAL.engine||"edge", vs=VOICES[eng]||[];
  const voiceOpts=vs.length
    ? vs.map(v=>`<option value="${esc(v.id)}" ${MANUAL.voice===v.id?"selected":""}>${v.status==="ok"?"✓ ":v.status==="failed"?"✕ ":""}${esc(v.name)}</option>`).join("")
    : `<option value="${esc(MANUAL.voice||"")}">${MANUAL.voice?esc(MANUAL.voice):"(đang nạp giọng…)"}</option>`;
  const busy=!!(ST.manual&&ST.manual.working);
  const resumeImages=!busy&&storyCanResumeImages();
  const s=STORY.sub;
  const stepNames=["✨ AI Viết", "📝 Nội Dung", "🎙 Giọng Đọc", "🎵 Nhạc Nền", "𝐓 Phụ Đề", "🖼 Khung Hình", "🎬 Nguồn Video", "⚙ Logo & Nhân Vật"];
  const sourceFiles=(STORY.source_videos||[]).length;
  P.innerHTML=`<div class="story-wizard-tabs">
    ${stepNames.map((name,i)=>`<button class="${STORY.step===i?"on":""}"
      onclick="setStoryStep(${i})">${name}</button>`).join("")}
    </div>
  <div class="sstep"><div class="shd"><span class="sn">1</span>✨ Tự động viết truyện từ tiêu đề</div>
    ${fld("Tiêu đề / Ý tưởng của video",`<input value="${esc(MANUAL.writer_title||"")}"
      placeholder="Ví dụ: Con dâu bỏ đi 10 năm, ngày về bất ngờ…"
      oninput="storyWriterTitleInput(this.value)">`)}
    ${MANUAL.content_idea_id?`<div class="story-plan-imported"><b>◆ Kế hoạch từ Kho ý tưởng</b>
      <span>${esc((MANUAL.youtube_tags||[]).join(", ")||"Chưa có tag")}</span>
      ${MANUAL.rewrite_brief?`<small>Đã nhận hồ sơ hook/plot twist tiếng Việt; Perplexity sẽ viết mới hoàn toàn.</small>`:
        MANUAL.content_outline?`<small>${esc(MANUAL.content_outline)}</small>`:""}</div>`:""}
    ${busy?`<button class="btn danger story-stop-action" style="width:100%;text-align:center;height:38px"
      ${_cancelPending?"disabled":""} onclick="cancelStoryRun()">
      ■ ${_cancelPending?"ĐANG DỪNG…":"DỪNG TÁC VỤ"}</button>`:
      `<button id="storyPrimaryAction" class="btn pri" style="width:100%;text-align:center;height:38px"
      onclick="storyPrimaryAction()">${resumeImages?
        `↻ TIẾP TỤC ${MANUAL.image_ready}/${MANUAL.image_total} ẢNH → RA VIDEO`:
        `✨ TẠO TRUYỆN → TẠO ẢNH → RA VIDEO`}</button>`}
    <label style="display:flex;gap:7px;align-items:flex-start;margin-top:10px">
      <input type="checkbox" ${STORY.auto_images?"checked":""}
        onchange="STORY.auto_images=this.checked;localStorage.setItem('advn_auto_images',this.checked?'1':'0')">
      <span>Tự tạo ảnh bằng Gemini Pro<br><small style="color:var(--muted)">
        Không cần API key: app tự gửi từng cảnh, chờ và tải ảnh về.</small></span></label>
    <label style="display:flex;gap:7px;align-items:flex-start;margin-top:8px">
      <input type="checkbox" ${STORY.auto_youtube_thumb?"checked":""}
        onchange="STORY.auto_youtube_thumb=this.checked;localStorage.setItem('advn_auto_yt_thumb',this.checked?'1':'0');settingsSetYt('auto_thumbnail',this.checked)">
      <span>Tạo thumbnail YouTube bằng ChatGPT<br><small style="color:var(--muted)">
        Ảnh 16:9 không chữ, app tự chèn chữ lớn. Cần đăng nhập ở Cài đặt → YouTube.</small></span></label>
    <label style="display:flex;gap:7px;align-items:flex-start;margin-top:8px">
      <input type="checkbox" ${STORY.auto_youtube_desc?"checked":""}
        onchange="STORY.auto_youtube_desc=this.checked;localStorage.setItem('advn_auto_yt_desc',this.checked?'1':'0');settingsSetYt('auto_description',this.checked)">
      <span>Viết mô tả YouTube 6 khối<br><small style="color:var(--muted)">
        Lưu <b>mo_ta_youtube.txt</b> trong thư mục video khi dựng xong.</small></span></label>
    ${fld("Số ảnh (ít ảnh sẽ xong nhanh hơn)",`<select onchange="STORY.scene_count=+this.value;localStorage.setItem('advn_story_scene_count',this.value)">
      <option value="6" ${STORY.scene_count===6?"selected":""}>6 ảnh · nhanh nhất</option>
      <option value="8" ${STORY.scene_count===8?"selected":""}>8 ảnh · cân bằng (khuyên dùng)</option>
      <option value="10" ${STORY.scene_count===10?"selected":""}>10 ảnh · chi tiết</option>
      <option value="14" ${STORY.scene_count===14?"selected":""}>14 ảnh · chậm nhất</option>
    </select>`)}
    <div class="story-cta-box">
      <label class="sonoff"><input type="checkbox" ${STORY.cta_enabled?"checked":""}
        onchange="STORY.cta_enabled=this.checked;storySaveCta();renderStoryPanel()">
        Chèn lời nhắc kênh (CTA)</label>
      ${STORY.cta_enabled?`
        ${fld("Câu chèn cố định của kênh",`<textarea readonly style="min-height:110px;opacity:.9"
          title="CTA này được giữ cố định theo yêu cầu của bạn">${esc(STORY.cta_text)}</textarea>`)}
        <div class="grid2">
          ${fld("Vị trí theo % truyện",`<input value="${esc(STORY.cta_positions)}"
            placeholder="12,55" oninput="STORY.cta_positions=this.value;storySaveCta()">`)}
          ${fld("Tốc độ riêng câu chèn",`<select onchange="STORY.cta_speed=+this.value;storySaveCta()">
            <option value="1" ${STORY.cta_speed===1?"selected":""}>1x</option>
            <option value="1.25" ${STORY.cta_speed===1.25?"selected":""}>1.25x</option>
            <option value="1.5" ${STORY.cta_speed===1.5?"selected":""}>1.5x</option>
            <option value="2" ${STORY.cta_speed===2?"selected":""}>2x (khuyên dùng)</option>
          </select>`)}
        </div>
        <div class="hint">Mặc định chèn ở 12% và 55% nội dung. Phải có ít nhất 2 vị trí;
          nếu nhập thiếu, app tự bổ sung. Chỉ các câu này được tăng tốc.</div>`:""}
    </div>
    <button class="btn sm" style="margin-top:8px" onclick="storyUseTitlePrompt()">✎ Nạp prompt tạo 8 tiêu đề</button>
    <div class="hint">Công cụ <b>Tạo kịch bản</b> sẽ viết từng chương, xuất đúng
      <b>KICH_BAN_DOC.txt</b>, rút prompt theo 6 chương, chuẩn bị ảnh rồi mới tạo
      giọng, nhạc, phụ đề và video. Không cần thêm ảnh trước khi bấm.</div>
    ${MANUAL.image_status?`<div class="hint"><b>Ảnh:</b> ${esc(MANUAL.image_status)}</div>`:""}
    ${MANUAL.script_path?`<div class="hint" title="${esc(MANUAL.script_path)}">
      ✓ Đã nạp ${MANUAL.script_words.toLocaleString("vi-VN")} từ ·
      ${esc(MANUAL.script_path.split(/[\\/]/).pop())}</div>`:""}
  </div>
  <div class="sstep"><div class="shd"><span class="sn">2</span>📝 Nội dung & Kịch bản truyện</div>
    ${fld("",`<textarea id="manualText" style="min-height:140px"
      placeholder="Dán nội dung truyện cần đọc, hoặc bấm 'Tải file' bên dưới…"
      oninput="MANUAL.text=this.value;storySubLive()">${esc(MANUAL.text||"")}</textarea>`)}
    <div class="rowbtns" style="align-items:center">
      <button class="btn" ${busy?"disabled":""} onclick="pickManualText()">📂 Tải file văn bản</button>
      ${fld("",`<input value="${esc(MANUAL.name||"")}" placeholder="Tên video xuất ra…"
        oninput="MANUAL.name=this.value">`)}
    </div>
  </div>
  <div class="sstep"><div class="shd"><span class="sn">3</span>🎙 Giọng đọc & Phân vai</div>
    <div class="grid2">
      ${fld("Bộ giọng",`<select onchange="setManualEngine(this.value)">
        <option value="edge" ${eng==="edge"?"selected":""}>edge-tts (Online nhanh)</option>
        <option value="vieneu" ${eng==="vieneu"?"selected":""}>VieNeu (Offline)</option>
        <option value="capcut" ${eng==="capcut"?"selected":""}>CapCut TTS</option>
      </select>`)}
      ${fld("Giọng",`<select onchange="MANUAL.voice=this.value">${voiceOpts}</select>`)}
    </div>
    ${rng("Tốc độ đọc",parseInt(MANUAL.rate||"0"),"%",-30,50,5,
      "MANUAL.rate=(this.value>0?'+':'')+this.value+'%';this.previousElementSibling.querySelector('b').textContent=this.value+'%'")}
    ${nutNgheThu("story")}
    <div class="hint">Giọng đang chọn dùng cho <b>lời kể</b>. Khi bật đa giọng, công cụ tự
      phát hiện tuổi/giới tính/vai trò, gán giọng riêng cho từng nhân vật.</div>
    ${voiceRecommendationHtml(eng,vs,busy)}
    <div class="rowbtns">
      <button class="btn" ${busy?"disabled":""} onclick="createManualAudio()">🎵 Tạo riêng audio</button>
      <button class="btn" onclick="pickManualAudio()">📂 Dùng audio có sẵn</button>
    </div>
    ${MANUAL.audio_path?`<audio controls preload="metadata" style="width:100%;margin-top:8px"
      src="/api/manual/audio?v=${_manualRev}"></audio>`:""}
  </div>
  <div class="sstep"><div class="shd"><span class="sn">4</span>🎵 Nhạc nền lồng tiếng
    <label class="sonoff"><input type="checkbox" ${STORY.nhac_enabled?"checked":""}
      onchange="STORY.nhac_enabled=this.checked;renderStoryPanel()"> Bật</label></div>
    ${STORY.nhac_enabled?`
      ${fld("Bài nhạc nền (CC0)",`<select onchange="MANUAL.nhac_bai=this.value">
        <option value="">— Ngẫu nhiên trong kho (${NHAC_LIST.length} bài) —</option>
        ${NHAC_LIST.map(x=>`<option value="${esc(x.ten)}" ${MANUAL.nhac_bai===x.ten?"selected":""}>${esc(x.ten)}</option>`).join("")}
      </select>`)}
      ${rng("Âm lượng nhạc",MANUAL.nhac_db," dB",-50,-20,1,
        "MANUAL.nhac_db=+this.value;this.previousElementSibling.querySelector('b').textContent=this.value+' dB'")}
      <label style="display:flex;gap:7px;align-items:center;margin-top:6px">
        <input type="checkbox" ${MANUAL.nhac_duck?"checked":""}
          onchange="MANUAL.nhac_duck=this.checked">
        <span>Tự động giảm nhạc khi có giọng nói (Ducking)</span></label>
      <div class="rowbtns" style="margin-top:8px">
        <button class="btn sm" ${busy?"disabled":""} onclick="taiNhacNen()">⇩ Tải thêm nhạc</button>
        <button class="btn sm" onclick="loadNhacNen(true)">↻ Làm mới</button>
      </div>`:`<div class="hint">Video sẽ chỉ phát giọng đọc, không có nhạc nền.</div>`}
  </div>
  <div class="sstep"><div class="shd"><span class="sn">5</span>𝐓 Cấu hình phụ đề cứng
    <label class="sonoff"><input type="checkbox" ${STORY.sub_enabled?"checked":""}
      onchange="STORY.sub_enabled=this.checked;renderStoryPanel();renderStoryStage()"> Bật</label></div>
    ${STORY.sub_enabled?`
      <div class="grid2">
        ${fld("Cỡ chữ",`<input type="number" min="20" max="120" value="${s.size}"
          onchange="STORY.sub.size=Math.max(20,+this.value||48);renderStoryStage()">`)}
        ${fld("Vị trí hiển thị",`<select onchange="STORY.sub.align=this.value;renderStoryStage()">
          <option value="bottom-center" ${s.align==="bottom-center"?"selected":""}>Dưới đáy</option>
          <option value="mid-center" ${s.align==="mid-center"?"selected":""}>Giữa khung hình</option>
          <option value="top-center" ${s.align==="top-center"?"selected":""}>Trên đỉnh</option>
        </select>`)}
      </div>
      <div class="grid2">
        ${fld("Màu sắc chữ",`<input type="color" value="${s.color}" style="height:34px;padding:2px"
          onchange="STORY.sub.color=this.value;renderStoryStage()">`)}
        ${fld("Độ dày viền",`<input type="number" min="0" max="6" value="${s.outline}"
          onchange="STORY.sub.outline=Math.max(0,+this.value||0)">`)}
      </div>
      <div class="hint">Phụ đề tự động khớp mốc thời gian với giọng đọc, ngắt dòng thông minh tối đa 2 dòng.</div>`
      :`<div class="hint">Video sẽ không in phụ đề lên hình.</div>`}
  </div>
  <div class="sstep"><div class="shd"><span class="sn">6</span>🖼 Tỷ lệ khung hình & Hiệu ứng ảnh</div>
    <label class="mini-label">Tỷ lệ video xuất ra</label>
    <div class="aspect-grid">
      <button class="aspect-card ${STORY.aspect==="16:9"?"on":""}" onclick="storySetAspect('16:9')"><b>16:9 Ngang</b><span>YouTube / TV</span></button>
      <button class="aspect-card ${STORY.aspect==="9:16"?"on":""}" onclick="storySetAspect('9:16')"><b>9:16 Dọc</b><span>TikTok / Reels / Shorts</span></button>
      <button class="aspect-card ${STORY.aspect==="1:1"?"on":""}" onclick="storySetAspect('1:1')"><b>1:1 Vuông</b><span>Facebook / Feed</span></button>
    </div>
    <div class="grid2 single-setting" style="margin-top:12px">
      ${fld("Hiệu ứng chuyển động hình ảnh",`<select onchange="STORY.kieu=this.value">
        <option value="chuyen_dong" ${STORY.kieu==="chuyen_dong"?"selected":""}>Ảnh trôi + phóng chậm (Ken Burns sống động)</option>
        <option value="tinh" ${STORY.kieu==="tinh"?"selected":""}>Ảnh đứng yên (Xuất nhanh nhất)</option>
      </select>`)}
    </div>
    <div class="hint" style="margin-top:12px">Bạn có thể chọn ảnh minh họa ở thanh danh sách cảnh dưới cùng hoặc để AI tự tạo.</div>
  </div>
  <div class="sstep"><div class="shd"><span class="sn">7</span>🎬 Video nền cho Audio</div>
    <div class="story-source-box story-video-source-box" style="margin-top:0">
      <div class="shd story-subhead">Kho video nền đang dùng
        <span class="story-library-badge ${sourceFiles?'has-files':''}">${sourceFiles?
          `${sourceFiles} nguồn` : "Kho đang trống"}</span></div>
      <div class="story-video-intro">Tải và cắt nằm trong <b>Công cụ Video</b>. Tại đây bạn chọn kho đã có,
        sau đó thiết lập cách video được ghép vào audio.</div>
      <div class="story-video-tool-grid">
        <button class="story-video-tool-card primary" onclick="setMode('tools');setVideoToolsTab('download')">
          <span class="story-video-tool-icon">⇩</span><span><b>Tải video nền</b><small>Bilibili · YouTube · Douyin · TikTok</small></span>
        </button>
        <button class="story-video-tool-card" onclick="setMode('tools');setVideoToolsTab('cut')">
          <span class="story-video-tool-icon">✂</span><span><b>Cắt video</b><small>Chia hàng loạt thành đoạn nhỏ</small></span>
        </button>
        <button class="story-video-pick" onclick="storySetMediaMode('video');storyAddVideos()">
          <span>▣</span><span><b>Chọn video có sẵn</b><small>Nhập nhiều file hoặc cả thư mục vào kho đang dùng</small></span><i>›</i>
        </button>
      </div>
      <div class="grid2">
        ${fld("Hiệu ứng ghép video",`<select onchange="STORY.source_effect=this.value">
          <option value="tinh" ${STORY.source_effect==="tinh"?"selected":""}>Giữ khung ổn định</option>
          <option value="zoom_in" ${STORY.source_effect==="zoom_in"?"selected":""}>Zoom vào nhẹ</option>
          <option value="zoom_out" ${STORY.source_effect==="zoom_out"?"selected":""}>Zoom ra nhẹ</option>
        </select>`)}
        ${fld("Cách lấy video",`<label style="display:flex;gap:7px;align-items:center;height:34px">
          <input type="checkbox" ${STORY.source_random?"checked":""}
          onchange="STORY.source_random=this.checked;storySaveSourceTransform()"> Random, tránh lặp liền nhau</label>`)}
      </div>
      <div class="grid2">
        ${fld("Đoạn random ngắn nhất (phút)",`<input type="number" min="0.1" max="60" step="0.5"
          value="${STORY.source_clip_min_minutes}"
          onchange="STORY.source_clip_min_minutes=Math.max(.1,+this.value||5);storySaveSourceTransform()">`)}
        ${fld("Đoạn random dài nhất (phút)",`<input type="number" min="0.1" max="60" step="0.5"
          value="${STORY.source_clip_max_minutes}"
          onchange="STORY.source_clip_max_minutes=Math.max(STORY.source_clip_min_minutes,+this.value||10);storySaveSourceTransform()">`)}
      </div>
      <div class="rowbtns" style="margin:7px 0 10px">
        <button class="btn sm" onclick="storyRandomizeSource()">⤨ Trộn một lượt mới</button>
        <button class="btn sm" onclick="storyResetSourceTransform()">↺ Đặt lại crop / zoom</button>
      </div>
      <div class="story-source-transform">
        <div class="shd story-subhead">Crop / zoom video nguồn
          <span class="hint">Kéo trực tiếp video trong khung xem trước</span></div>
        ${rng("Phóng / thu video",STORY.source_zoom,"%",40,220,5,
          "STORY.source_zoom=+this.value;this.previousElementSibling.querySelector('b').textContent=this.value+'%';storySaveSourceTransform();renderStoryStage()")}
        <div class="grid2">
          ${fld("Tâm ngang (%)",`<input type="number" min="0" max="100" value="${Math.round(STORY.source_x)}"
            onchange="STORY.source_x=Math.max(0,Math.min(100,+this.value||0));storySaveSourceTransform();renderStoryStage()">`)}
          ${fld("Tâm dọc (%)",`<input type="number" min="0" max="100" value="${Math.round(STORY.source_y)}"
            onchange="STORY.source_y=Math.max(0,Math.min(100,+this.value||0));storySaveSourceTransform();renderStoryStage()">`)}
        </div>
        <div class="grid2 story-crop-grid">
          ${fld("Cắt trái / phải (%)",`<div class="crop-pair"><input type="number" min="0" max="45" value="${STORY.source_crop_left}"
            onchange="STORY.source_crop_left=Math.max(0,Math.min(45,+this.value||0));renderStoryStage()"><input type="number" min="0" max="45" value="${STORY.source_crop_right}"
            onchange="STORY.source_crop_right=Math.max(0,Math.min(45,+this.value||0));renderStoryStage()"></div>`)}
          ${fld("Cắt trên / dưới (%)",`<div class="crop-pair"><input type="number" min="0" max="45" value="${STORY.source_crop_top}"
            onchange="STORY.source_crop_top=Math.max(0,Math.min(45,+this.value||0));renderStoryStage()"><input type="number" min="0" max="45" value="${STORY.source_crop_bottom}"
            onchange="STORY.source_crop_bottom=Math.max(0,Math.min(45,+this.value||0));renderStoryStage()"></div>`)}
        </div>
      </div>
      ${fld("Xử lý phụ đề gốc trên video",`<select onchange="STORY.source_cover=this.value">
        <option value="none" ${STORY.source_cover==="none"?"selected":""}>Giữ nguyên video</option>
        <option value="blur_bottom" ${STORY.source_cover==="blur_bottom"?"selected":""}>Làm mờ dải phụ đề dưới</option>
        <option value="che_bottom" ${STORY.source_cover==="che_bottom"?"selected":""}>Che đen dải phụ đề dưới</option>
      </select>`)}
      <div class="hint">💡 Khi bật Random, công cụ sẽ trộn các video/clip đã nhập và pick tiếp cho tới khi đủ thời lượng audio.
        Muốn tạo clip sẵn ${STORY.source_clip_min_minutes}–${STORY.source_clip_max_minutes} phút, hãy dùng tab <b>Cắt video hàng loạt</b> ở Công cụ Video.</div>
    </div>
  </div>
  <div class="sstep"><div class="shd"><span class="sn">8</span>⚙ Logo thương hiệu & Nhân vật chỉ dẫn</div>
    <div class="story-logo-box" style="margin-top:0">
      <div class="shd story-subhead">Logo thương hiệu
        <label class="sonoff"><input type="checkbox" ${STORY.logo_enabled?"checked":""}
          onchange="STORY.logo_enabled=this.checked;storySaveLogo();renderStoryPanel();renderStoryStage()">
          Bật</label></div>
      <div class="rowbtns">
        <button class="btn" onclick="storyPickLogo()">▣ Chọn ảnh Logo</button>
        ${STORY.logo_path?`<button class="btn danger" onclick="storyClearLogo()">✕ Bỏ logo</button>`:""}
      </div>
      ${STORY.logo_path?`<div class="hint story-logo-path" title="${esc(STORY.logo_path)}">
        ✓ ${esc(STORY.logo_path.split(/[\\/]/).pop()||STORY.logo_path)}</div>`:
        `<div class="hint">Khuyên dùng ảnh định dạng PNG nền trong suốt để đẹp nhất.</div>`}
      ${STORY.logo_enabled?`<div class="grid2">
        ${fld("Vị trí hiển thị",`<select onchange="STORY.logo_position=this.value;storySaveLogo();renderStoryStage()">
          <option value="top-right" ${STORY.logo_position==="top-right"?"selected":""}>Góc trên - Phải</option>
          <option value="top-left" ${STORY.logo_position==="top-left"?"selected":""}>Góc trên - Trái</option>
          <option value="bottom-right" ${STORY.logo_position==="bottom-right"?"selected":""}>Góc dưới - Phải</option>
          <option value="bottom-left" ${STORY.logo_position==="bottom-left"?"selected":""}>Góc dưới - Trái</option>
        </select>`)}
        ${fld("Độ rõ nét (%)",`<input type="number" min="5" max="100" value="${STORY.logo_opacity}"
          onchange="STORY.logo_opacity=Math.max(5,Math.min(100,+this.value||82));storySaveLogo();renderStoryStage()">`)}
      </div>
      ${rng("Kích thước logo",STORY.logo_width,"%",4,40,1,
        "STORY.logo_width=+this.value;this.previousElementSibling.querySelector('b').textContent=this.value+'%';storySaveLogo();renderStoryStage()")}`:""}
    </div>
    <div class="story-character-box">
      <div class="shd story-subhead">Nhân vật quả mít chỉ dẫn (Góc phải dưới)
        <label class="sonoff"><input type="checkbox" ${STORY.character_enabled?"checked":""}
          onchange="STORY.character_enabled=this.checked;storySaveCharacter();renderStoryPanel();renderStoryStage()"> Bật</label>
      </div>
      ${STORY.character_enabled?`<div class="grid2">
        ${fld("Kích thước (%)",`<input type="number" min="55" max="180" value="${Math.round(STORY.character_scale*100)}"
          onchange="STORY.character_scale=Math.max(.55,Math.min(1.8,(+this.value||100)/100));storySaveCharacter();renderStoryStage()">`)}
        ${fld("Độ rõ nét (%)",`<input type="number" min="25" max="100" value="${STORY.character_opacity}"
          onchange="STORY.character_opacity=Math.max(25,Math.min(100,+this.value||92));storySaveCharacter();renderStoryStage()">`)}
      </div>
      <div class="hint" style="margin-top:8px">Nhân vật quả mít xinh xắn sẽ xuất hiện ở góc dưới bên phải, nhấp nhô chỉ dẫn vào nội dung.</div>`
      :`<div class="hint">Tắt nhân vật chỉ dẫn, video sẽ giữ nguyên không chèn nhân vật.</div>`}
    </div>
  </div>
  ${busy?`<button class="btn danger story-run-all story-stop-action"
    ${_cancelPending?"disabled":""} onclick="cancelStoryRun()">
    ■ ${_cancelPending?"ĐANG DỪNG AN TOÀN…":"DỪNG TÁC VỤ ĐANG CHẠY"}</button>`:
    (MANUAL.audio_path&&(STORY.source_videos||[]).length
      ?`<button class="btn pri story-run-all" onclick="storyRenderReadyAudio()">
        ▶ RANDOM VIDEO + XUẤT MP4 — GIỮ AUDIO HIỆN CÓ</button>`
      :`<button class="btn pri story-run-all" onclick="storyRunAll()">
        ▶ CHẠY TẤT CẢ — XUẤT VIDEO HOÀN CHỈNH</button>`)}
  <div class="hint" style="margin-top:8px"><b>${esc(MANUAL.status||"Sẵn sàng")}</b>
    ${MANUAL.error?`<br><span style="color:var(--red)">${esc(MANUAL.error)}</span>`:""}</div>
  ${MANUAL.output_path?`<button class="btn" style="width:100%;text-align:center;margin-top:6px"
    onclick="openManualFolder('output')">📂 Mở thư mục video kết quả</button>`:""}
  <div class="hint story-last-hint" style="margin-top:10px">Muốn ghép audio vào một video có sẵn thay vì dựng
    từ ảnh: chuyển sang chế độ <b>Lồng tiếng</b>, chọn video rồi quay lại đây
    <span class="lplay" onclick="muxManualAudio()">▶ ghép vào video đang chọn</span>.</div>`;
  const steps=Array.from(P.querySelectorAll(".sstep"));
  steps.forEach((el,i)=>{
    el.dataset.step=String(i);
    el.classList.toggle("active",i===STORY.step);
    if(i<steps.length-1){
      const next=document.createElement("button");
      next.className="btn story-next";
      next.textContent=`Tiếp tục: ${stepNames[i+1]} →`;
      next.onclick=()=>setStoryStep(i+1);
      el.appendChild(next);
    }
  });
}
function setStoryStep(i){
  STORY.step=Math.max(0,Math.min(7,Number(i)||0));
  localStorage.setItem("advn_story_step",String(STORY.step));
  renderStoryPanel();
}
function storySetAspect(aspect){
  STORY.aspect=["16:9","9:16","1:1"].includes(aspect)?aspect:"16:9";
  localStorage.setItem("advn_aspect",STORY.aspect);
  renderStoryPanel(); renderStoryStage();
}
function storySaveCta(){
  localStorage.setItem("advn_story_cta_enabled",STORY.cta_enabled?"1":"0");
  localStorage.setItem("advn_story_cta_text",STORY.cta_text||"");
  localStorage.setItem("advn_story_cta_positions",STORY.cta_positions||"12,55");
  localStorage.setItem("advn_story_cta_speed",String(STORY.cta_speed||2));
}
function storySaveLogo(){
  localStorage.setItem("advn_story_logo_enabled",STORY.logo_enabled?"1":"0");
  localStorage.setItem("advn_story_logo_path",STORY.logo_path||"");
  localStorage.setItem("advn_story_logo_position",STORY.logo_position||"top-right");
  localStorage.setItem("advn_story_logo_width",String(STORY.logo_width||12));
  localStorage.setItem("advn_story_logo_opacity",String(STORY.logo_opacity||82));
}
function storySaveCharacter(){
  localStorage.setItem("advn_story_character_enabled",STORY.character_enabled?"1":"0");
  localStorage.setItem("advn_story_character_scale",String(STORY.character_scale||1));
  localStorage.setItem("advn_story_character_opacity",String(STORY.character_opacity||92));
}
async function storyPickLogo(){
  let path="";
  if(DESK()){
    try{
      const r=await pywebview.api.pick_image();
      if(r&&r.error) return toast(r.error,"err");
      path=Array.isArray(r)?(r[0]||""):(r||"");
    }catch(e){ return toast(e.message||String(e),"err"); }
  }else path=prompt("Dán đường dẫn file logo:")||"";
  if(!String(path).trim()) return;
  STORY.logo_path=String(path).trim(); STORY.logo_enabled=true;
  MANUAL.output_path=""; storySaveLogo(); renderStoryPanel(); renderStoryStage();
  toast("Đã chọn logo cho video kể chuyện","ok");
}
function storyClearLogo(){
  STORY.logo_path=""; STORY.logo_enabled=false; MANUAL.output_path="";
  storySaveLogo(); renderStoryPanel(); renderStoryStage();
}
function storySelectRelative(delta){
  if(!STORY.imgs.length) return;
  const start=STORY.sel>=0?STORY.sel:(delta>0?-1:0);
  STORY.sel=(start+delta+STORY.imgs.length)%STORY.imgs.length;
  MANUAL.output_path=""; renderStoryImgs(); renderStoryStage();
}
function storyTogglePreview(){
  const v=document.querySelector("#sstage video");
  if(v){ if(v.paused)v.play();else v.pause(); return; }
  storySelectRelative(1);
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
function renderStory(){
  const isVideo=STORY_MEDIA_MODE==="video";
  document.getElementById("storyModeImg")?.classList.toggle("on",!isVideo);
  document.getElementById("storyModeVid")?.classList.toggle("on",isVideo);
  if(document.getElementById("storyImageActions")) document.getElementById("storyImageActions").style.display=isVideo?"none":"";
  if(document.getElementById("storyVideoActions")) document.getElementById("storyVideoActions").style.display=isVideo?"":"none";
  if(document.getElementById("simgs")) document.getElementById("simgs").style.display=isVideo?"none":"";
  if(document.getElementById("svideos")) document.getElementById("svideos").style.display=isVideo?"":"none";
  renderStoryMedia(); renderStoryStage(); renderStoryPanel();
}
function storySel(i){ STORY.sel=i; MANUAL.output_path=""; renderStoryImgs(); renderStoryStage(); }
function storyDelImg(i){ STORY.imgs.splice(i,1); if(STORY.sel>=STORY.imgs.length)STORY.sel=-1; renderStory(); storyPersistPack(); }
async function storyClearImgs(){
  if(!STORY.imgs.length) return toast("Danh sách ảnh đã trống","warn");
  STORY.imgs=[]; STORY.sel=-1; MANUAL.output_path=""; renderStory();
  const saved=await storyPersistPack(true);
  if(saved!==false) toast("Đã xoá hết ảnh khỏi danh sách","ok");
}
function storyMove(i,d){
  const j=i+d; if(j<0||j>=STORY.imgs.length) return;
  [STORY.imgs[i],STORY.imgs[j]]=[STORY.imgs[j],STORY.imgs[i]];
  if(STORY.sel===i)STORY.sel=j; else if(STORY.sel===j)STORY.sel=i;
  renderStoryImgs(); renderStoryStage(); storyPersistPack();
}

function storySetMediaMode(mode){
  STORY_MEDIA_MODE = mode === "video" ? "video" : "image";
  localStorage.setItem("advn_story_media_mode", STORY_MEDIA_MODE);
  document.getElementById("storyModeImg").classList.toggle("on", STORY_MEDIA_MODE==="image");
  document.getElementById("storyModeVid").classList.toggle("on", STORY_MEDIA_MODE==="video");
  document.getElementById("storyImageActions").style.display = STORY_MEDIA_MODE==="image" ? "" : "none";
  document.getElementById("storyVideoActions").style.display = STORY_MEDIA_MODE==="video" ? "" : "none";
  document.getElementById("simgs").style.display = STORY_MEDIA_MODE==="image" ? "" : "none";
  document.getElementById("svideos").style.display = STORY_MEDIA_MODE==="video" ? "" : "none";
  renderStoryMedia(); renderStoryStage();
}

function storySaveSourceTransform(){
  localStorage.setItem("advn_source_clip_min",String(STORY.source_clip_min_minutes));
  localStorage.setItem("advn_source_clip_max",String(STORY.source_clip_max_minutes));
  localStorage.setItem("advn_source_random",STORY.source_random?"1":"0");
  localStorage.setItem("advn_source_seed",String(STORY.source_random_seed||0));
  localStorage.setItem("advn_source_zoom",String(STORY.source_zoom));
  localStorage.setItem("advn_source_x",String(STORY.source_x));
  localStorage.setItem("advn_source_y",String(STORY.source_y));
}
function storyResetSourceTransform(){
  STORY.source_zoom=100; STORY.source_x=50; STORY.source_y=50;
  STORY.source_crop_left=0; STORY.source_crop_right=0;
  STORY.source_crop_top=0; STORY.source_crop_bottom=0;
  storySaveSourceTransform(); renderStoryPanel(); renderStoryStage();
}
function storyRandomizeSource(){
  STORY.source_random_seed=Date.now(); storySaveSourceTransform();
  toast("Đã tạo lượt trộn video mới","ok");
}
function bindStoryVideoStage(){
  const v=document.getElementById("storySourcePreview");
  if(!v) return;
  let dragging=false,lastX=0,lastY=0;
  v.addEventListener("pointerdown",e=>{dragging=true;lastX=e.clientX;lastY=e.clientY;v.setPointerCapture?.(e.pointerId);});
  v.addEventListener("pointermove",e=>{
    if(!dragging) return;
    const rect=v.getBoundingClientRect();
    STORY.source_x=Math.max(0,Math.min(100,STORY.source_x+(e.clientX-lastX)/Math.max(1,rect.width)*100));
    STORY.source_y=Math.max(0,Math.min(100,STORY.source_y+(e.clientY-lastY)/Math.max(1,rect.height)*100));
    lastX=e.clientX;lastY=e.clientY;
    v.style.objectPosition=`${STORY.source_x}% ${STORY.source_y}%`;
  });
  const end=()=>{if(dragging){dragging=false;storySaveSourceTransform();renderStoryPanel();}};
  v.addEventListener("pointerup",end);v.addEventListener("pointercancel",end);
  v.addEventListener("wheel",e=>{
    e.preventDefault(); STORY.source_zoom=Math.max(40,Math.min(220,STORY.source_zoom+(e.deltaY<0?5:-5)));
    storySaveSourceTransform(); renderStoryPanel(); renderStoryStage();
  },{passive:false});
}

function renderStoryMedia(){
  if(STORY_MEDIA_MODE==="video") renderStoryVideos();
  else renderStoryImgs();
}

async function storyAddVideos(){
  let files;
  if(window.pywebview && window.pywebview.api && window.pywebview.api.pick_video){
    files = await window.pywebview.api.pick_video();
  } else {
    // fallback: prompt for path
    const p = prompt("Đường dẫn video:");
    files = p ? [p] : [];
  }
  if(!files || !files.length) return;
  for(const f of files){
    if(!STORY.source_videos.includes(f)) STORY.source_videos.push(f);
  }
  renderStoryMedia();
  storyUpdateVideoInfo();
}

async function storyAddVideoFolder(){
  let folder;
  if(window.pywebview && window.pywebview.api && window.pywebview.api.pick_folder){
    folder = await window.pywebview.api.pick_folder();
  } else {
    folder = prompt("Đường dẫn thư mục:");
  }
  if(!folder) return;
  // Add folder path - backend will scan for video files
  if(!STORY.source_videos.includes(folder)) STORY.source_videos.push(folder);
  renderStoryMedia();
  storyUpdateVideoInfo();
}

function storyClearVideos(){
  STORY.source_videos = [];
  STORY._video_info = null;
  renderStoryMedia();
}

function storyDeleteVideo(i){
  STORY.source_videos.splice(i, 1);
  STORY._video_info = null;
  renderStoryMedia();
  storyUpdateVideoInfo();
}

function storyMoveVideo(i, d){
  const j = i + d;
  if(j < 0 || j >= STORY.source_videos.length) return;
  const t = STORY.source_videos[i];
  STORY.source_videos[i] = STORY.source_videos[j];
  STORY.source_videos[j] = t;
  renderStoryMedia();
}

async function storyUpdateVideoInfo(){
  if(!STORY.source_videos.length){ STORY._video_info = null; return; }
  try{
    const info = await api("/api/story/video_info", {paths: STORY.source_videos});
    if(Array.isArray(info.paths)&&info.paths.length) STORY.source_videos=info.paths;
    STORY._video_info = info;
    renderStoryMedia();
  } catch(e){ console.warn("video_info error:", e); }
}

function renderStoryVideos(){
  const el = document.getElementById("svideos");
  if(!el) return;
  const vids = STORY.source_videos || [];
  const info = STORY._video_info || {};
  const videoDetails = info.videos || [];

  // Count
  const countEl = document.getElementById("simgcount");
  if(countEl) countEl.textContent = STORY_MEDIA_MODE==="video" ? `${vids.length} video` : `${STORY.imgs.length} ảnh`;

  if(!vids.length){
    el.innerHTML = `<div class="story-empty-hint">Bấm <b>+ Thêm video</b> để chọn video nguồn.<br>
      Video sẽ tự được cắt khớp thời lượng audio.</div>`;
    return;
  }

  let totalDur = 0;
  const rows = vids.map((v, i) => {
    const name = v.split(/[\\/]/).pop() || v;
    const detail = videoDetails.find(d => d.path === v);
    const dur = detail ? detail.duration : 0;
    totalDur += dur;
    const durText = dur ? fmt(dur) : "?";
    const res = detail && detail.width ? `${detail.width}×${detail.height}` : "";
    return `<div class="story-video-item${i === STORY.source_sel ? ' selected' : ''}" onclick="storySelectVideo(${i})">
      <div class="story-video-info">
        <b>${i+1}. ${esc(name)}</b>
        <small>${durText}${res ? " · " + res : ""}</small>
      </div>
      <div class="story-video-actions">
        <button class="btn sm" onclick="storyMoveVideo(${i},-1)" title="Lên">▲</button>
        <button class="btn sm" onclick="storyMoveVideo(${i},1)" title="Xuống">▼</button>
        <button class="btn sm danger" onclick="storyDeleteVideo(${i})" title="Xóa">✕</button>
      </div>
    </div>`;
  }).join("");

  // Trim info
  const audioDur = info.audio_duration || MANUAL.audio_duration || 0;
  let trimHtml = "";
  if(audioDur > 0 && totalDur > 0){
    const diff = totalDur - audioDur;
    if(diff > 1){
      trimHtml = `<div class="story-trim-info trim-excess">⚡ Tổng video: ${fmt(totalDur)} — Audio: ${fmt(audioDur)} → Tự cắt ${fmt(diff)} thừa</div>`;
    } else if(diff < -1){
      trimHtml = `<div class="story-trim-info trim-short">⚠ Tổng video: ${fmt(totalDur)} ngắn hơn audio ${fmt(audioDur)} — video sẽ được lặp lại</div>`;
    } else {
      trimHtml = `<div class="story-trim-info trim-ok">✓ Tổng video (${fmt(totalDur)}) khớp audio (${fmt(audioDur)})</div>`;
    }
  }

  el.innerHTML = rows + trimHtml;
}
function storySelectVideo(i){
  STORY.source_sel=Math.max(0,Math.min((STORY.source_videos||[]).length-1,Number(i)||0));
  MANUAL.output_path=""; renderStoryVideos(); renderStoryStage();
}
function storyTitleKey(value){
  return String(value||"").normalize("NFKC").toLocaleLowerCase("vi-VN")
    .replace(/[^\p{L}\p{N}]+/gu," ").trim();
}
function storyPackMatchesTitle(title){
  const wanted=storyTitleKey(title);
  const owner=storyTitleKey(STORY.packTitle);
  return !!wanted&&!!owner&&wanted===owner;
}
function storyCanResumeImages(){
  return STORY.auto_images&&!!STORY.pack&&storyPackMatchesTitle(MANUAL.writer_title)&&
    MANUAL.image_total>0&&MANUAL.image_ready<MANUAL.image_total;
}
function storyWriterTitleInput(value){
  MANUAL.writer_title=value;
  const nextTitle=String(value||"").trim();
  const oldPackTitle=String(STORY.packTitle||"").trim();
  if(nextTitle&&oldPackTitle&&
     storyTitleKey(nextTitle)!==storyTitleKey(oldPackTitle)){
    // Đổi tiêu đề nghĩa là mở một phiên truyện mới. Tách toàn bộ dữ liệu
    // phục hồi của truyện cũ khỏi UI; file trên đĩa vẫn được giữ nguyên.
    closeStoryPrompt(); _storyPromptData={text:"",url:""};
    STORY.pack=""; STORY.packTitle=""; STORY.imgs=[]; STORY.sel=-1;
    MANUAL.image_ready=0; MANUAL.image_total=0; MANUAL.image_status="";
    MANUAL.script_path=""; MANUAL.script_title=oldPackTitle;
    MANUAL.script_words=0; MANUAL.output_path="";
    localStorage.removeItem("advn_story_image_pack");
    _lastPromptPack=""; _lastImageReadyCount=-1;
    renderStoryImgs(); renderStoryStage();
  }
  const previewTitle=document.getElementById("storyPreviewTitle");
  if(previewTitle) previewTitle.textContent=value||MANUAL.name||"Video kể chuyện AI";
  const button=document.getElementById("storyPrimaryAction");
  if(!button) return;
  const resume=storyCanResumeImages();
  button.textContent=resume
    ?`↻ TIẾP TỤC ${MANUAL.image_ready}/${MANUAL.image_total} ẢNH → RA VIDEO`
    :`✨ TẠO TRUYỆN → TẠO ẢNH → RA VIDEO`;
}
function storyPrimaryAction(){
  return storyCanResumeImages()?storyResumeImages():storyGenerateAndRun();
}
async function storyPersistPack(replaceEmpty=false){
  if(!STORY.pack) return true;
  try{
    const r=await api("/api/story/image_pack",{
      manifest_path:STORY.pack, images:STORY.imgs,
      replace_images:replaceEmpty||STORY.imgs.length>0, include_prompt:false});
    if(r&&r.manifest_path){
      STORY.pack=r.manifest_path;
      STORY.packTitle=String(r.title||STORY.packTitle||"");
      localStorage.setItem("advn_story_image_pack",STORY.pack);
      if(Array.isArray(r.images)) STORY.imgs=r.images;
      renderStoryImgs(); renderStoryStage();
    }
    return true;
  }catch(e){ toast("Không lưu được thứ tự gói ảnh: "+e.message,"warn"); return false; }
}
async function storyRestoreImagePack(){
  try{
    let r=null;
    if(STORY.pack){
      try{ r=await api("/api/story/image_pack",{manifest_path:STORY.pack,include_prompt:false}); }
      catch(_e){ STORY.pack=""; localStorage.removeItem("advn_story_image_pack"); }
    }
    if(!r) r=await api("/api/story/image_pack/latest");
    if(r&&r.manifest_path){
      STORY.pack=r.manifest_path;
      STORY.packTitle=String(r.title||"");
      localStorage.setItem("advn_story_image_pack",STORY.pack);
      if(!STORY.imgs.length&&Array.isArray(r.images)) STORY.imgs=r.images;
      MANUAL.image_ready=Number(r.ready_count||0);
      MANUAL.image_total=Number(r.scene_count||0);
    }
  }catch(_e){}
}
async function _copyStoryPrompt(text){
  if(!text) return false;
  if(DESK()&&pywebview.api.copy_text){
    try{ if(await pywebview.api.copy_text(text)) return true; }catch(_e){}
  }
  try{ await navigator.clipboard.writeText(text); return true; }
  catch(_e){
    try{
      const ta=document.createElement("textarea"); ta.value=text;
      ta.style.position="fixed"; ta.style.opacity="0"; document.body.appendChild(ta);
      ta.select(); const ok=document.execCommand("copy"); ta.remove(); return ok;
    }catch(_e2){ return false; }
  }
}
let _storyPromptData={text:"",url:""};
let _lastPromptPack="",_lastImageReadyCount=-1;
function _storyPromptHtmlText(text){
  return String(text||"").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
}
function closeStoryPrompt(){ document.getElementById("storyPromptModal")?.remove(); }
async function copyStoryPromptFromModal(){
  const ok=await _copyStoryPrompt(_storyPromptData.text);
  toast(ok?"Đã sao chép prompt":"Không sao chép được; hãy chọn nội dung trong ô và Ctrl+C",ok?"ok":"warn");
}
async function openStoryPromptProvider(){
  const {url,text}=_storyPromptData;
  if(!url) return;
  if(DESK()&&pywebview.api.open_url_and_paste){
    const r=await pywebview.api.open_url_and_paste(url,text);
    if(r&&r.paste_scheduled){
      toast("Đang mở Gemini — app sẽ tự dán prompt vào ô chat","ok");
      return true;
    }
  }
  const copied=await _copyStoryPrompt(text);
  if(DESK()&&pywebview.api.open_url) await pywebview.api.open_url(url);
  else window.open(url,"_blank","noopener");
  toast(copied?"Đã mở Gemini; nếu chưa thấy prompt hãy nhấn Ctrl+V":"Đã mở Gemini; prompt vẫn hiện trong app","warn");
  return false;
}
async function showStoryPrompt(r,openProvider=false){
  const text=String((r&&r.prompt_text)||"");
  if(!text) return toast("Gói ảnh chưa có nội dung prompt","warn");
  _storyPromptData={text,url:String((r&&r.provider_url)||"")};
  closeStoryPrompt();
  const shell=document.createElement("div");
  shell.id="storyPromptModal"; shell.className="modal-shell open";
  shell.innerHTML=`<div class="settings-dialog story-prompt-dialog">
    <div class="settings-head"><span class="settings-symbol">✦</span><div>
      <h2>Prompt hình ảnh đã sẵn sàng</h2>
      <p>Prompt được rút từ bản thiết kế và lưu cùng gói ảnh.</p></div>
      <button class="modal-close" onclick="closeStoryPrompt()">×</button></div>
    <div class="story-prompt-body">
      <textarea readonly spellcheck="false">${_storyPromptHtmlText(text)}</textarea>
      <div class="hint">Đây là màn dự phòng khi lượt tự động bị lỗi. App sẽ mở
        Gemini bằng tài khoản đang đăng nhập và dán prompt để bạn xử lý tiếp.
        Nếu trình duyệt chặn, chỉ cần nhấn <b>Ctrl+V</b>.</div>
    </div><div class="settings-foot">
      <button class="btn" onclick="copyStoryPromptFromModal()">Sao chép prompt</button>
      <button class="btn pri" onclick="openStoryPromptProvider()">Mở Gemini &amp; tự dán</button>
    </div></div>`;
  document.body.appendChild(shell);
  const copied=await _copyStoryPrompt(text);
  if(openProvider){ await openStoryPromptProvider(); }
  else toast(copied?"Prompt đã hiện và đã sao chép":"Prompt đã hiện; bạn có thể chọn và Ctrl+C",copied?"ok":"warn");
}
async function storyCreateImagePack(){
  const title=String(MANUAL.writer_title||MANUAL.name||"Truyện").trim()||"Truyện";
  // Nút Prompt ảnh luôn thuộc tiêu đề đang nhập. Gói khôi phục từ
  // localStorage của truyện khác phải bị tách khỏi phiên hiện tại trước.
  if(STORY.pack&&!storyPackMatchesTitle(title)){
    STORY.pack=""; STORY.packTitle=""; STORY.imgs=[]; STORY.sel=-1;
    MANUAL.image_ready=0; MANUAL.image_total=0;
    localStorage.removeItem("advn_story_image_pack");
    _lastPromptPack=""; _lastImageReadyCount=-1;
    renderStoryImgs(); renderStoryStage();
  }
  const currentPack=storyPackMatchesTitle(title)?STORY.pack:"";
  const scriptMatches=!!MANUAL.script_path&&
    storyTitleKey(MANUAL.script_title)===storyTitleKey(title);
  if(!currentPack&&!scriptMatches)
    return toast("Hãy tạo truyện trước; prompt ảnh cần cốt truyện và hồ sơ nhân vật","warn");
  try{
    const body=currentPack
      ?{manifest_path:currentPack,expected_title:title,include_prompt:true}
      :{title, name:title, txt_path:MANUAL.script_path||"", aspect:STORY.aspect,
        scene_count:STORY.scene_count, images:STORY.pack?[]:STORY.imgs, include_prompt:true};
    const r=await api("/api/story/image_pack",body);
    STORY.pack=r.manifest_path||"";
    STORY.packTitle=String(r.title||title);
    if(STORY.pack) localStorage.setItem("advn_story_image_pack",STORY.pack);
    if(Array.isArray(r.images)&&r.images.length) STORY.imgs=r.images;
    renderStory();
    await showStoryPrompt(r,true);
  }catch(e){ toast(e.message||String(e),"err"); }
}
async function storyAddImgs(){
  if(DESK()){
    try{
      const r=await pywebview.api.pick_images();
      if(r&&r.error) return toast(r.error,"err");
      const list=Array.isArray(r)?r:(r?[r]:[]);
      if(list.length){ STORY.imgs.push(...list); renderStory(); await storyPersistPack(); toast(`Đã thêm ${list.length} ảnh`,"ok"); }
    }catch(e){ toast(e.message||String(e),"err"); }
    return;
  }
  const p=prompt("Dán đường dẫn ảnh (mỗi lần một ảnh):")||"";
  if(p.trim()){ STORY.imgs.push(p.trim()); renderStory(); await storyPersistPack(); }
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
  if(path.trim()){ STORY.imgs.push(path.trim()); renderStory(); await storyPersistPack(); toast("Đã thêm thư mục ảnh","ok"); }
}
function storyUseTitlePrompt(){
  const summary=String(MANUAL.writer_title||"").trim();
  MANUAL.writer_title=STORY_TITLE_PROMPT.replace("[DÁN TÓM TẮT]",summary||"[DÁN TÓM TẮT]");
  STORY.step=0; localStorage.setItem("advn_story_step","0"); renderStory();
  toast("Đã nạp prompt tạo 8 tiêu đề; hãy thay phần tóm tắt rồi chạy AI viết","ok");
}
function storyRequestPayload(includeText){
  const ta=document.getElementById("manualText");
  if(ta) MANUAL.text=ta.value;
  const d=storyDims();
  const payload={
    name:MANUAL.name,
    txt_path:MANUAL.script_path||"",
    engine:MANUAL.engine, voice:MANUAL.voice, pitch:MANUAL.pitch, rate:MANUAL.rate,
    anh:STORY.imgs, image_pack:STORY.pack, aspect:STORY.aspect,
    video_sources:STORY.source_videos||[],
    source_effect:STORY.source_effect||"tinh",
    source_clip_min_seconds:Math.max(2,Number(STORY.source_clip_min_minutes||5)*60),
    source_clip_max_seconds:Math.max(2,Number(STORY.source_clip_max_minutes||10)*60),
    source_random:!!STORY.source_random,
    source_random_seed:Number(STORY.source_random_seed)||0,
    source_transform:{zoom:Number(STORY.source_zoom)||100,x:Number(STORY.source_x)||50,
      y:Number(STORY.source_y)||50,crop_left:Number(STORY.source_crop_left)||0,
      crop_right:Number(STORY.source_crop_right)||0,crop_top:Number(STORY.source_crop_top)||0,
      crop_bottom:Number(STORY.source_crop_bottom)||0},
    source_cover:STORY.source_cover||"none",
    character:{enabled:!!STORY.character_enabled,scale:STORY.character_scale,opacity:STORY.character_opacity/100},
    scene_count:STORY.scene_count, auto_images:STORY.auto_images,
    auto_youtube_thumbnail:STORY.auto_youtube_thumb,
    auto_youtube_description:STORY.auto_youtube_desc,
    w:d.w, h:d.h, fps:STORY.fps, kieu:STORY.kieu,
    nhac:{enabled:STORY.nhac_enabled, bai:MANUAL.nhac_bai,
          muc_db:MANUAL.nhac_db, duck:MANUAL.nhac_duck},
    cta:{enabled:STORY.cta_enabled, text:STORY.cta_text,
         positions:String(STORY.cta_positions||"12,55").split(/[,;\s]+/)
           .map(Number).filter(Number.isFinite), speed:STORY.cta_speed},
    logo:{enabled:STORY.logo_enabled, path:STORY.logo_path,
          position:STORY.logo_position, width_pct:STORY.logo_width,
          opacity:STORY.logo_opacity/100},
    sub:{enabled:STORY.sub_enabled, style:{
      size:STORY.sub.size, color:STORY.sub.color, outline:STORY.sub.outline,
      bold:STORY.sub.bold, align:STORY.sub.align, margin_v:STORY.sub.margin_v}},
    voice_auto:STORY.auto_voice,
    multi_voice:STORY.multi_voice,
    max_character_voices:8,
    regions: STORY.regions || (PR && PR.regions) || [],
    blur_bottom_ratio: STORY.source_cover === "blur_bottom" ? 0.22 : 0,
    content_idea_id:MANUAL.content_idea_id||"",
    youtube_description:MANUAL.youtube_description||"",
    youtube_tags:MANUAL.youtube_tags||[],
    content_outline:MANUAL.content_outline||"",
    rewrite_brief:MANUAL.rewrite_brief||"",
  };
  if(includeText) payload.text=MANUAL.text;
  return payload;
}
async function storyGenerateAndRun(){
  const title=String(MANUAL.writer_title||"").trim();
  if(!title) return toast("Hãy nhập tiêu đề truyện","warn");
  try{
    const payload=storyRequestPayload(false);
    payload.name=title;
    if(storyTitleKey(MANUAL.script_title)!==storyTitleKey(title))
      payload.txt_path="";
    const stalePack=STORY.auto_images&&STORY.pack&&
      storyTitleKey(STORY.packTitle)!==storyTitleKey(title);
    if(stalePack){
      // Ảnh được tự khôi phục từ localStorage thuộc truyện trước. Không gửi
      // chúng sang backend cho tiêu đề mới.
      payload.anh=[];
      payload.image_pack="";
    }else if(!STORY.imgs.length&&!storyPackMatchesTitle(title)) payload.image_pack="";
    payload.story_title=title;
    await api("/api/story/generate_and_run",payload);
    if(stalePack){
      STORY.imgs=[]; STORY.pack=""; STORY.packTitle=title; STORY.sel=-1;
      localStorage.removeItem("advn_story_image_pack");
      _lastImageReadyCount=-1; _lastPromptPack="";
    }
    MANUAL.name=title;
    MANUAL.output_path=""; MANUAL.status="Đang tạo kịch bản từ tiêu đề…"; MANUAL.error="";
    MANUAL.image_status="";
    MANUAL.script_path=""; MANUAL.script_title=title; MANUAL.script_words=0;
    ST.manual={...(ST.manual||{}),working:true};
    renderStory();
    toast("Đã bắt đầu: viết truyện → prompt → ảnh → giọng → video","ok");
  }catch(e){ toast(e.message,"err"); }
}
async function storyResumeImages(){
  const title=String(MANUAL.writer_title||"").trim();
  if(!STORY.pack||!storyPackMatchesTitle(title)) return storyGenerateAndRun();
  try{
    const payload=storyRequestPayload(false);
    payload.anh=[];
    payload.image_pack=STORY.pack;
    payload.story_title=title;
    payload.txt_path=MANUAL.script_path||"";
    await api("/api/story/resume_images",payload);
    MANUAL.output_path=""; MANUAL.error="";
    MANUAL.status=`Đang tiếp tục từ ${MANUAL.image_ready}/${MANUAL.image_total} ảnh…`;
    ST.manual={...(ST.manual||{}),working:true};
    renderStory(); toast("Đang tạo tiếp các cảnh còn thiếu; ảnh cũ được giữ nguyên","ok");
  }catch(e){ toast(e.message||String(e),"err"); }
}
async function storyRunAll(){
  storyRequestPayload(true);
  if(!String(MANUAL.text||"").trim()) return toast("Hãy nhập nội dung truyện (bước 1)","warn");
  if(!STORY.imgs.length&&!((STORY.source_videos||[]).length))
    return toast("Hãy thêm ảnh hoặc tải video nguồn ở cột trái","warn");
  try{
    await api("/api/manual/run_all",storyRequestPayload(true));
    MANUAL.output_path=""; MANUAL.status="Bắt đầu làm video kể chuyện…"; MANUAL.error="";
    ST.manual={...(ST.manual||{}),working:true};
    renderStory(); toast("Đã bắt đầu: giọng đọc → nhạc nền → phụ đề → video","ok");
  }catch(e){ toast(e.message,"err"); }
}

async function storyRenderReadyAudio(){
  if(!MANUAL.audio_path) return toast("Chưa có audio để dựng video","warn");
  if(!((STORY.source_videos||[]).length))
    return toast("Hãy chọn thư mục hoặc video nguồn trước","warn");
  try{
    const payload=storyRequestPayload(false);
    payload.audio_path=MANUAL.audio_path;
    await api("/api/manual/slideshow",payload);
    MANUAL.output_path=""; MANUAL.status="Đang random video cho đủ thời lượng audio…";
    MANUAL.error=""; ST.manual={...(ST.manual||{}),working:true};
    renderStory();
    toast("Đã vào thẳng bước random video; không tạo lại giọng và nhạc","ok");
  }catch(e){ toast(e.message,"err"); }
}

/* ======================= khởi động ======================= */
async function init(){
  await loadConfig();
  await storyRestoreImagePack();
  setMode(MODE);
  refresh();
  setInterval(refresh,1200);
}
init();
