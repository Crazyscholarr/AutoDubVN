"use strict";
/* ======================= bảng bên phải ======================= */
const DUB_SUB_FONTS = [
  "Be Vietnam Pro", "Arial", "Segoe UI", "Roboto", "Tahoma", "Times New Roman",
  "Arial Black", "Arial Narrow", "Bahnschrift", "Calibri", "Calibri Light",
  "Cambria", "Consolas", "Georgia", "Palatino Linotype", "Trebuchet MS",
  "Verdana", "Segoe UI Light", "Segoe UI Semibold", "Segoe UI Black",
  "Segoe Print", "Segoe Script"
];
const DUB_SUB_COLORS = [
  ["#FFFFFF", "Trắng"], ["#F1F5F9", "Trắng khói"], ["#CBD5E1", "Xám sáng"],
  ["#64748B", "Xám"], ["#1E293B", "Xám đậm"], ["#000000", "Đen"],
  ["#FFF4CC", "Kem"], ["#FFFF00", "Vàng tươi"], ["#FFD700", "Vàng gold"],
  ["#F59E0B", "Hổ phách"], ["#FB923C", "Cam sáng"], ["#EA580C", "Cam đậm"],
  ["#FDA4AF", "Hồng nhạt"], ["#F472B6", "Hồng"], ["#EF4444", "Đỏ"],
  ["#BE123C", "Đỏ hồng"], ["#C4B5FD", "Tím nhạt"], ["#A855F7", "Tím"],
  ["#7DD3FC", "Xanh da trời"], ["#38BDF8", "Xanh sáng"], ["#2563EB", "Xanh dương"],
  ["#5EEAD4", "Xanh ngọc"], ["#86EFAC", "Xanh lá nhạt"], ["#22C55E", "Xanh lá"]
];
function setSubColor(value){
  let color=String(value||"").trim();
  if(!color.startsWith("#")) color="#"+color;
  if(/^#[0-9a-f]{3}$/i.test(color)) color="#"+[...color.slice(1)].map(c=>c+c).join("");
  if(!/^#[0-9a-f]{6}$/i.test(color)){
    toast("Mã màu chưa hợp lệ. Ví dụ: #FFD700", "err");
    renderPanel();
    return;
  }
  setSt("color",color.toUpperCase());
}
function setTab(t){
  TAB=t;
  if(t==="tts") loadVoices(false);
  if(t==="manual"){ loadManualVoices(false); loadNhacNen(false); }
  document.querySelectorAll("#tabs .tab-item, #tabs .btn").forEach(b=>
    b.classList.toggle("on",b.dataset.t===t));
  renderPanel();
}
const REVIEW_REASON={
  suspicious_chunk:"Đoạn nhận dạng rỗng dù máy nghe có tiếng",
  unresolved_speech_gap:"Lỗ tiếng chưa có phụ đề (≥ 1.2s)",
  invalid_source_clock:"Mốc thời gian nguồn sai",
  invalid_clock:"Mốc thời gian sai",
  missing_speech_marks:"Thiếu mốc lời nói",
  overlapping_clock:"Hai dòng chồng mốc",
  weak_fragment_alignment:"Khớp mảnh yếu"
};
let _captionReview={};
let _captionReviewLoad=0;
let _captionReviewGeneration={};
function invalidateCaptionReview(id){
  _captionReviewGeneration[id]=(_captionReviewGeneration[id]||0)+1;
  delete _captionReview[id];
  if(_captionReviewLoad===id) _captionReviewLoad=0;
}
function selectedQueueJob(){
  return (ST.queue||[]).find(j=>j.id===JID) || (ST.queue||[]).find(j=>j.id===ST.selected) || null;
}
function jobNeedsCaptionReview(job){
  return !!(job && (job.result_status==="REVIEW_REQUIRED" ||
    (job.status==="cần kiểm tra" && !!job.review_dir)));
}
function ttsOverflowHtml(){
  const job=selectedQueueJob()||{};
  const data=job.tts_overflow||{};
  const clips=Array.isArray(data.clips)?data.clips:[];
  if(!clips.length) return "";
  const rows=clips.slice(0,12).map(c=>{
    const start=+c.start||0;
    const play=Math.max(0,start-1.0);
    return `<button type="button" class="btn sm" onclick="jumpTo(${play},true)">
      ${fmt(start)} · cắt ${(+c.cut_s||0).toFixed(2)}s · ${esc((c.text||"").slice(0,48))}</button>`;
  }).join("");
  return `<div class="hint"><b>${clips.length} clip bị cắt đuôi</b> — nghe trước khi chấp nhận mất lời.
    File <b>tts_overflow.json</b> trong thư mục output.</div>
    <div class="rowbtns" style="margin-top:6px">${rows}</div>`;
}
function jsPathArg(p){
  return esc(p||"").replace(/\\/g,"\\\\");
}
function reviewGapsFor(job){
  const cached=(job && _captionReview[job.id]) || {};
  const gaps=cached.gaps || (job && job.review_gaps) || [];
  return Array.isArray(gaps)?gaps:[];
}
function reviewAckedFor(job){
  const cached=(job && _captionReview[job.id]) || {};
  return Array.isArray(cached.acked)?cached.acked:[];
}
function gapIsAcked(g, acked, remaining){
  if(typeof g.blocking==='boolean') return !g.blocking;
  // Backend decides which review reasons a non-speech confirmation resolves.
  // Time overlap alone must not paint a still-blocked clock error green.
  if(Array.isArray(remaining)){
    if(g.issue_id) return !remaining.some(r=>r.issue_id===g.issue_id);
    return !remaining.some(r=>+r.start<+g.end && +r.end>+g.start);
  }
  const start=+g.start||0, end=+g.end||0, dur=Math.max(0.001,end-start);
  return (acked||[]).some(a=>{
    const lo=Math.max(start, +a.start||0), hi=Math.min(end, +a.end||0);
    return hi>lo && (hi-lo)/dur>=0.9;
  });
}
async function loadCaptionReview(id){
  if(!id || (_captionReview[id] && _captionReview[id].ok) || _captionReviewLoad===id) return;
  _captionReviewLoad=id;
  const generation=_captionReviewGeneration[id]||0;
  const reviewDir=((ST.queue||[]).find(j=>j.id===id)||{}).review_dir;
  try{
    const r=await api("/api/caption_review?id="+id);
    const current=(ST.queue||[]).find(j=>j.id===id);
    if(generation===(_captionReviewGeneration[id]||0) && current && current.review_dir===reviewDir && jobNeedsCaptionReview(current)){
      _captionReview[id]=Object.assign({ok:true}, r||{});
    }
  }catch(e){
    const current=(ST.queue||[]).find(j=>j.id===id);
    if(generation===(_captionReviewGeneration[id]||0) && current && current.review_dir===reviewDir && jobNeedsCaptionReview(current)){
      _captionReview[id]={ok:false, error:e.message, gaps:[]};
    }
  }finally{
    if(_captionReviewLoad===id && generation===(_captionReviewGeneration[id]||0)) _captionReviewLoad=0;
  }
  if(TAB==="asr" && JID===id) renderPanel();
}
async function ackNonSpeech(ranges, resume){
  const job=selectedQueueJob();
  if(!job || !jobNeedsCaptionReview(job)) return toast("Không có đoạn cần kiểm tra","warn");
  if(!ranges || !ranges.length) return toast("Chưa có mốc để xác nhận","warn");
  try{
    const r=await api("/api/caption_review/ack",{
      id:job.id,
      ranges:ranges.map(g=>({start:+g.start,end:+g.end,reason:g.reason||"unresolved_speech_gap",note:"tạp âm / hiệu ứng, không phải thoại"})),
      continue_pipeline:!!resume
    });
    invalidateCaptionReview(job.id);
    if(r && r.started){
      toast("Đã xác nhận tạp âm. Tiếp tục dịch, không nhận dạng lại.","ok");
    }else if(r && (r.remaining||[]).length){
      toast("Đã ghi nhận. Còn "+r.remaining.length+" đoạn chưa xác nhận.","warn");
    }else{
      toast("Đã ghi nhận: không phải thoại.","ok");
    }
    await refresh();
    if(TAB==="asr") renderPanel();
  }catch(e){
    toast(e.message||String(e),"err");
  }
}
function ackNonSpeechAt(i){
  const g=reviewGapsFor(selectedQueueJob())[i];
  if(!g) return;
  return ackNonSpeech([g], false);
}
function ackNonSpeechAll(resume){
  const job=selectedQueueJob();
  const gaps=reviewGapsFor(job);
  if(!gaps.length) return toast("Đang đọc danh sách đoạn…","warn");
  return ackNonSpeech(gaps, !!resume);
}
async function rerecognizeGap(i){
  const job=selectedQueueJob();
  const g=reviewGapsFor(job)[i];
  if(!job||!g) return toast("Không có đoạn cần nhận dạng lại","warn");
  try{
    await api("/api/caption_review/rerecognize",{
      id:job.id, start:+g.start, end:+g.end
    });
    invalidateCaptionReview(job.id);
    toast("Đang nhận dạng lại đoạn này (cắt nhỏ ~12s, không bịa chữ, không chạy cả phim).","ok");
    if(typeof refresh==="function") await refresh();
  }catch(e){
    toast(e.message||String(e),"err");
  }
}
function captionReviewHtml(job){
  if(!jobNeedsCaptionReview(job)) return "";
  const cached=_captionReview[job.id]||{};
  const dir=cached.review_dir||job.review_dir||"";
  const gaps=reviewGapsFor(job);
  const acked=reviewAckedFor(job);
  if(dir && !_captionReview[job.id]) loadCaptionReview(job.id);
  const remaining=gaps.filter(g=>!gapIsAcked(g,acked,cached.remaining));
  const busy=!!(ST.running||ST.busy);
  const rows=gaps.map((g,i)=>{
    const start=+g.start||0, end=+g.end||0;
    const why=REVIEW_REASON[g.reason]||esc(g.reason||"cần kiểm tra");
    const done=gapIsAcked(g,acked,cached.remaining);
    const play=Math.max(0, (+g.play_start || (start-1.5)));
    return `<div class="review-gap${done?" acked":""}">
      <button type="button" class="review-seek" onclick="jumpTo(${play},true)">
        <b>${fmtms(start)} → ${fmtms(end)}</b>
        <span>${why} · ${Math.max(0.1,end-start).toFixed(1)}s</span>
        <em>Tua &amp; nghe (kèm ngữ cảnh)</em>
      </button>
      <div class="review-gap-actions">
        <button type="button" class="btn sm pri" ${done||busy?"disabled":""}
          onclick="rerecognizeGap(${i})">Nhận dạng lại đoạn này</button>
        <button type="button" class="btn sm" ${done||busy?"disabled":""}
          onclick="ackNonSpeechAt(${i})">${done?"Đã ghi: không phải thoại":"Không phải thoại"}</button>
      </div>
    </div>`;
  }).join("");
  const open=dir?`<button class="btn" style="width:100%;text-align:center"
      onclick="openOut('${jsPathArg(dir)}')">📂 Mở thư mục kiểm tra</button>`:"";
  const cont=gaps.length?`<button class="btn pri" style="width:100%;text-align:center;margin-top:8px"
      ${busy?"disabled":""}
      onclick="ackNonSpeechAll(true)">Tạp âm / hiệu ứng — tiếp tục dịch</button>`:"";
  return `<div class="review-card">
    <div class="sect">CẦN KIỂM TRA PHỤ ĐỀ TRUNG</div>
    <div class="hint">Nếu nghe thấy lời Trung lẫn nắm tay/hiệu ứng, bấm <b>Nhận dạng lại đoạn này</b>
      (cắt nhỏ ~12s, không chạy cả phim, không bịa chữ).
      Chỉ bấm <b>Không phải thoại</b> khi chắc không có lời.
      Cổng 1.2s không hạ.</div>
    ${open}
    <div class="review-gaps">${rows||'<div class="hint">Đang đọc danh sách đoạn…</div>'}</div>
    ${remaining.length?`<div class="hint">Còn <b>${remaining.length}</b> / ${gaps.length} đoạn chưa xác nhận.</div>`:`<div class="hint">Đã xác nhận hết đoạn tạp âm.</div>`}
    ${cont}
  </div>`;
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
    const font=st.font||"Be Vietnam Pro";
    const fonts=DUB_SUB_FONTS.includes(font)?DUB_SUB_FONTS:[font,...DUB_SUB_FONTS];
    const color=/^#[0-9a-f]{6}$/i.test(st.color||"")?st.color.toUpperCase():"#FFFFFF";
    P.innerHTML=`<div class="sect">PHỤ ĐỀ TIẾNG VIỆT (LỚP 3)</div>
    <div class="grid2">
      ${fld("Font chữ",`<select aria-label="Font chữ phụ đề" onchange="setSt('font',this.value)">
        ${fonts.map(f=>`<option value="${esc(f)}" ${font===f?"selected":""}>${esc(f)}</option>`).join("")}</select>`)}
      ${fld("Cỡ chữ",`<input type="number" min="8" max="200" value="${st.size||30}"
        onchange="setSt('size',+this.value)">`)}
    </div>
    <div class="hint">Chọn font đã cài trên máy để xem trước và xuất video đúng kiểu chữ. Font chưa có sẽ được thay bằng font dự phòng.</div>
    ${fld("Màu chữ",`<div class="sub-color-palette">${DUB_SUB_COLORS.map(([c,name])=>
      `<button type="button" class="sub-color-swatch ${color===c?"on":""}" style="background:${c}"
        title="${name} · ${c}" aria-label="${name} ${c}" aria-pressed="${color===c}"
        onclick="setSubColor('${c}')"></button>`).join("")}</div>
      <div class="sub-custom-color">
        <input type="color" aria-label="Chọn màu chữ tuỳ ý" value="${color}" onchange="setSubColor(this.value)">
        <input type="text" aria-label="Mã HEX màu chữ" value="${color}" maxlength="7" spellcheck="false"
          placeholder="#FFD700" onchange="setSubColor(this.value)"
          onkeydown="if(event.key==='Enter'){this.blur()}">
        <span>Tuỳ chọn</span>
      </div>`)}
    <div style="margin-top:12px">
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
        placeholder="chưa chọn ảnh" oninput="PR.logo.path=this.value;save();draw()">`)}
      <button class="btn" style="width:100%;text-align:center;margin:7px 0 4px"
        onclick="pickLogo()">📂 Chọn ảnh logo…</button>
      <div style="height:11px"></div>
      ${rng("Độ mờ",Math.round((l.opacity??1)*100),"%",5,100,1,
        "PR.logo.opacity=+this.value/100;save();draw()")}
      <div class="grid2">
        ${fld("X",`<input type="number" value="${l.x}" onchange="setLogoGeometry('x',+this.value)">`)}
        ${fld("Y",`<input type="number" value="${l.y}" onchange="setLogoGeometry('y',+this.value)">`)}
        ${fld("Rộng",`<input type="number" value="${l.w}" onchange="setLogoGeometry('w',+this.value)">`)}
        ${fld("Cao",`<input type="number" value="${l.h}" onchange="setLogoGeometry('h',+this.value)">`)}
      </div>
      <button class="btn danger" style="width:100%" onclick="PR.logo=null;ACT=null;save();draw();renderPanel()">Bỏ logo</button>`
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
    const job=selectedQueueJob()||{};
    P.innerHTML=`<div class="sect">NHẬN DẠNG PHỤ ĐỀ GỐC</div>
    ${captionReviewHtml(job)}
    <div class="hint">Cấu hình engine nằm trong <b>config.yaml</b> (mục <b>asr</b>).
      Mặc định <b>paraformer</b> cho tiếng Trung, tự dự phòng sang faster-whisper.
      Có sẵn <b>kiểm tra độ phủ</b> và <b>tự vá lỗ hổng</b> chống mất đoạn.
      Cắt mốc kiểu CapCut (<b>asr.caption_style: screen</b>): mỗi dòng ~8-16 chữ / ~1.8-2.8 giây, ngắt khi im lặng, ưu tiên dấu câu — không đợi hết câu。</div>
    <button class="btn pri" style="width:100%;text-align:center"
      onclick="run(['asr'])">${jobNeedsCaptionReview(job)?"▶ Chạy nhận dạng lại":"▶ Chạy nhận dạng"}</button>
    <div style="height:9px"></div>
    <div class="hint">Số dòng hiện có: <b>${segs().length}</b></div>`;
  }

  else if(TAB==="tr"){
    const tr=CFG.translation||{};
    const provider=tr.provider||"browser";
    const providerFields = provider==="zai" ? `
      ${fld("Z.AI API key",`<input type="password" autocomplete="off"
        value="${esc(tr.zai_api_key||"")}" placeholder="key.xxxx"
        onchange="setTrCfg('zai_api_key',this.value)">`)}
      ${fld("Model miễn phí",`<select onchange="setTrCfg('zai_model',this.value)">
        <option value="glm-4.7-flash" ${(tr.zai_model||"glm-4.7-flash")==="glm-4.7-flash"?"selected":""}>GLM-4.7-Flash · Free (đã kiểm thử)</option>
        <option value="glm-4.5-flash" ${tr.zai_model==="glm-4.5-flash"?"selected":""}>GLM-4.5-Flash · Free, chậm hơn</option>
        <option value="glm-4.6v-flash" ${tr.zai_model==="glm-4.6v-flash"?"selected":""}>GLM-4.6V-Flash · vision, hay quá tải</option>
      </select>`)}
      ${fld("Base URL",`<input value="${esc(tr.zai_base_url||"https://api.z.ai/api/paas/v4")}" onchange="setTrCfg('zai_base_url',this.value)">`)}
      ${fld("Timeout mỗi request (giây)",`<input type="number" min="30" step="15" value="${Number(tr.zai_timeout||120)}" onchange="setTrCfg('zai_timeout',Math.max(30,+this.value||120))">`)}
      <div class="hint">Z.AI official <b>/api/paas/v4/chat/completions</b>. GLM-4.7-Flash miễn phí, JSON dịch ổn hơn Qwen free trên Xkiro.</div>
      <button class="btn" onclick="testTrCfg()">Thử kết nối Z.AI</button>`
    : provider==="xkiro" ? `
      ${fld("Xkiro API key",`<input type="password" autocomplete="off"
        value="${esc(tr.xkiro_api_key||"")}" placeholder="sk-xt-…"
        onchange="setTrCfg('xkiro_api_key',this.value)">`)}
      ${fld("Model miễn phí",`<select onchange="setTrCfg('xkiro_model',this.value)">
        <option value="qwen/qwen3.5-flash:free" ${(tr.xkiro_model||"qwen/qwen3.5-flash:free")==="qwen/qwen3.5-flash:free"?"selected":""}>Qwen3.5 Flash · Free (đã kiểm thử)</option>
        <option value="qwen/qwen3.5-plus:free" ${tr.xkiro_model==="qwen/qwen3.5-plus:free"?"selected":""}>Qwen3.5 Plus · Free (đã kiểm thử)</option>
      </select>`)}
      ${fld("Base URL",`<input value="${esc(tr.xkiro_base_url||"https://api.xkiro.com/v1")}" onchange="setTrCfg('xkiro_base_url',this.value)">`)}
      ${fld("Timeout mỗi request (giây)",`<input type="number" min="30" step="15" value="${Number(tr.xkiro_timeout||120)}" onchange="setTrCfg('xkiro_timeout',Math.max(30,+this.value||120))">`)}
      <button class="btn" onclick="testTrCfg()">Thử kết nối Xkiro</button>`
    : provider==="tokenharbor" ? `
      <div class="grid2">
        ${fld("TokenHarbor API key",`<input type="password" autocomplete="off"
          value="${esc(tr.tokenharbor_api_key||"")}" placeholder="thk_live_..."
          onchange="setTrCfg('tokenharbor_api_key',this.value)">`)}
        ${fld("Model",`<input value="${esc(tr.tokenharbor_model||"deepseek-v4-flash:free")}"
          onchange="setTrCfg('tokenharbor_model',this.value)">`)}
      </div>
      ${fld("Base URL",`<input value="${esc(tr.tokenharbor_base_url||"https://tokenharbor.ai/v1")}"
        onchange="setTrCfg('tokenharbor_base_url',this.value)">`)}
      ${fld("Timeout mỗi request",`<input type="number" min="30" step="15"
        value="${Number(tr.tokenharbor_timeout||120)}"
        onchange="setTrCfg('tokenharbor_timeout',Math.max(30,+this.value||120))">`)}
      <div class="hint">Token Harbor: OpenAI <b>/v1/chat/completions</b>, lỗi thì tự chuyển Anthropic <b>/v1/messages</b>. Model free <b>deepseek-v4-flash:free</b>.</div>`
    : provider==="zenmux" ? `
      <div class="grid2">
        ${fld("ZenMux API key",`<input type="password" autocomplete="off"
          value="${esc(tr.zenmux_api_key||"")}" placeholder="sk-mg-v1-..."
          onchange="setTrCfg('zenmux_api_key',this.value)">`)}
        ${fld("Model",`<input value="${esc(tr.zenmux_model||"z-ai/glm-4.7-flash-free")}"
          onchange="setTrCfg('zenmux_model',this.value)">`)}
      </div>
      ${fld("Base URL",`<input value="${esc(tr.zenmux_base_url||"https://zenmux.ai/api/v1")}"
        onchange="setTrCfg('zenmux_base_url',this.value)">`)}
      ${fld("Timeout mỗi request",`<input type="number" min="60" step="30"
        value="${Number(tr.zenmux_timeout||420)}"
        onchange="setTrCfg('zenmux_timeout',Math.max(60,+this.value||420))">`)}
      <div class="hint">ZenMux dùng chuẩn OpenAI-compatible. Model mặc định <b>z-ai/glm-4.7-flash-free</b>; khóa chỉ lưu cục bộ trong config.yaml.</div>`
    : provider==="nvidia" ? `<div class="hint">NVIDIA đang bật. Key và model chỉnh ở ô gắn sẵn phía trên.</div>`
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
    : `<div class="hint">Dùng Gemini qua trình duyệt Edge/Chrome đã đăng nhập, không cần API key.
      Hồ sơ trình duyệt dùng chung với kho ý tưởng nên <b>chat/tab mới</b> dễ rơi về dịch văn bản.
      App tự bấm <b>Chat mới</b>, gieo khóa <b>lồng tiếng phim</b>, và dán khóa đó vào mọi lô.
      Bản dịch cũ kiểu bài báo không tái sử dụng (cache v7) — xóa <b>*.vi.srt</b> nếu đang reuse.</div>`;

    P.innerHTML=`<div class="sect">DỊCH SANG TIẾNG VIỆT</div>
    <div class="grid2">
      ${fld("Chế độ dịch",`<select onchange="setTrCfg('provider',this.value,true)">
        <option value="browser" ${provider==="browser"?"selected":""}>browser - Gemini qua trình duyệt</option>
        <option value="zai" ${provider==="zai"?"selected":""}>zai - GLM-4.7-Flash miễn phí (Z.AI)</option>
        <option value="gemini" ${provider==="gemini"?"selected":""}>gemini - API key</option>
        <option value="tokenharbor" ${provider==="tokenharbor"?"selected":""}>tokenharbor - DeepSeek V4 Flash free</option>
        <option value="xkiro" ${provider==="xkiro"?"selected":""}>xkiro - Qwen miễn phí</option>
        <option value="nvidia" ${provider==="nvidia"?"selected":""}>nvidia - NIM catalog (Pro / Lightning / Kimi)</option>
        <option value="zenmux" ${provider==="zenmux"?"selected":""}>zenmux - GLM-5.3 free</option>
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
    ${nvidiaReadyPanel("tr")}
    ${providerFields}
    <div class="grid2">
      ${fld("Ép tên nam chính",`<input value="${esc(tr.male_lead_name||"")}"
        placeholder="để trống = không ép"
        onchange="setTrCfg('male_lead_name',this.value)">`)}
      ${fld("Ép tên nữ chính",`<input value="${esc(tr.female_lead_name||"")}"
        placeholder="để trống = không ép"
        onchange="setTrCfg('female_lead_name',this.value)">`)}
    </div>
    <label class="chk2"><input type="checkbox" ${tr.keep_source_timing!==false?"checked":""}
      onchange="setTrCfg('keep_source_timing',this.checked)"> Giữ nhịp phụ đề Trung (1 câu Trung = 1 câu Việt)</label>
    <div class="hint">Bật (mặc định): mỗi câu Việt <b>cùng start/end</b> với SRT Trung — không gộp nhiều câu Trung thành một câu Việt dài.
      Tắt thì app gộp mảnh ASR rồi chia lại sub Việt theo ý (câu Việt dài hơn miệng Trung).
      Sau khi dịch, app <b>chia lại chữ Việt trong đúng mốc</b> theo ngữ pháp (không cắt <b>tôi / đã...</b>), 环 bắn đích = <b>điểm</b>.</div>
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
    ${ttsOverflowHtml()}
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
    <label class="chk2"><input type="checkbox" ${op.lock_av!==false?"checked":""}
      onchange="PR.options.lock_av=this.checked;PR.options.sync_mode=this.checked?'strict':'cascade';save();renderPanel()"> Khóa cứng lời thoại với hình</label>
    <div class="hint">Bật (mặc định): mỗi câu bắt đầu đúng timestamp gốc, không dồn lệch câu-này-kéo-câu-kia.
      Chương trình còn <b>khóa đồng hồ hình với tiếng</b>: lệch thấy sau ~1 phút rồi càng về sau càng nặng
      là hai đồng hồ chạy khác tốc độ (PTS hình ≠ audio), không phải một câu trượt. App đo tỉ lệ một lần,
      kéo mọi mốc thoại + track giọng xuyên suốt. Thiếu vài giây ở đuôi thì đệm im lặng, không kéo chậm cả phim.
      Track cũ: bấm <b>Dựng lại giọng đọc</b> (không cần nhận dạng/dịch)
      rồi <b>Xuất video</b> — hoặc chỉ Xuất nếu 3 đoạn mẫu đã nghe khớp.</div>
    ${dubSyncCheckHtml(op)}
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
    <label class="chk2"><input type="checkbox" ${(op.auto_dub_thumbnail!==undefined?op.auto_dub_thumbnail:(CFG.dang_youtube||{}).auto_dub_thumbnail===true)?"checked":""}
      onchange="setOpt('auto_dub_thumbnail',this.checked);settingsSetYt('auto_dub_thumbnail',this.checked)"> Tự cắt cảnh → ChatGPT thumbnail + tiêu đề</label>
    <div class="hint">Mặc định tắt — ảnh ChatGPT cho phim lồng tiếng thường xấu, không dùng nữa trừ khi bật tay.</div>
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
      video được <b>copy nguyên luồng hình</b>, xuất gần như tức thì.
      Phim dài: 3 đoạn mẫu kiểm tra khớp &lt; 1 phút. Chỉ thay tiếng thường mất vài phút đến khoảng 10–20% thời lượng phim.
      Ghi cứng phụ đề/làm mờ có thể gần bằng cả tập — chỉ xuất khi 3 đoạn mẫu đã ổn.</div>
    ${dubSyncCheckHtml(op)}
    <button class="btn" style="width:100%;text-align:center;margin-bottom:9px"
      onclick="run(['sync_check'])">▶ Kiểm tra khớp hình (3 đoạn mẫu)</button>
    <button class="btn" style="width:100%;text-align:center;margin-bottom:9px"
      onclick="cleanupTemp()">Dọn file tạm (_tmp)</button>
    <div class="hint">Giữ audio ASR (<b>audio16k</b>) và checkpoint nhận dạng để không tách/nhận dạng lại cả phim. Chỉ xóa bản lồng tiếng tạm và đoạn render.</div>
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
const VOICE_RECS={analysis:null,items:[],cast:[],coverage:0,loading:false,engine:""};
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
  const cast=VOICE_RECS.engine===eng?VOICE_RECS.cast:[];
  const castHtml=cast.length?`<div style="margin-top:8px;border-top:1px solid var(--line);padding-top:7px">
    <div class="hint"><b>Dàn nhân vật tự chọn:</b> ${cast.length} giọng riêng · nhận diện chắc
      ${Number(VOICE_RECS.coverage||0).toFixed(1)}% lượt thoại.</div>
    <div style="display:grid;gap:5px;margin-top:6px">${cast.map(c=>`
      <div style="border:1px solid var(--line);border-radius:7px;padding:6px 7px">
        <div style="display:flex;align-items:center;gap:5px"><b style="flex:1">${esc(c.character||"")}</b>
          <span class="hint">${c.dialogue_lines||0} lượt</span>
          <button class="btn sm" ${busy?"disabled":""} onclick="previewCastVoice(${VOICE_RECS.cast.indexOf(c)})">▶ Nghe</button></div>
        <span style="color:var(--accent)">${esc(c.voice_name||c.voice_id||"")}</span>
        ${c.description?`<div class="hint" style="margin-top:2px">${esc(c.description)}</div>`:""}
      </div>`).join("")}</div></div>`:"";
  const library=VOICE_LIBRARY_OPEN?`<div style="max-height:210px;overflow:auto;display:grid;gap:4px;margin-top:7px">
    ${vs.map((v,i)=>`<button class="btn sm ${MANUAL.voice===v.id?"pri":""}"
      style="text-align:left;${v.status==="failed"?"opacity:.62":""}" ${busy?"disabled":""}
      title="${esc(v.status_error||"")}" onclick="previewVoiceAt(${i})">▶ ${v.status==="ok"?"✓ ":v.status==="failed"?"✕ ":""}${esc(v.name)}</button>`).join("")}
    ${vs.length?"":`<div class="hint">Đang nạp catalog giọng…</div>`}</div>`:"";
  return `<label style="display:flex;gap:7px;align-items:center;margin-top:7px">
      <input type="checkbox" ${STORY.auto_voice?"checked":""}
        onchange="STORY.auto_voice=this.checked;localStorage.setItem('advn_auto_voice',this.checked?'1':'0')">
      <span>Tự phân tích truyện và chọn giọng kể phù hợp</span></label>
    <label style="display:flex;gap:7px;align-items:center;margin-top:7px">
      <input type="checkbox" ${STORY.multi_voice?"checked":""}
        onchange="STORY.multi_voice=this.checked;localStorage.setItem('advn_multi_voice',this.checked?'1':'0');renderStoryPanel()">
      <span><b>Đa giọng:</b> mỗi nhân vật một giọng, lời dẫn giữ giọng kể</span></label>
    <div class="rowbtns" style="margin-top:7px">
      <button class="btn" ${busy||VOICE_RECS.loading?"disabled":""} onclick="analyseStoryVoice()">
        ${VOICE_RECS.loading?"Đang phân tích…":"✨ Phân tích & đề xuất giọng"}</button>
      <button class="btn" onclick="VOICE_LIBRARY_OPEN=!VOICE_LIBRARY_OPEN;renderStoryPanel()">
        🎧 ${VOICE_LIBRARY_OPEN?"Đóng":"Mở"} thư viện ${vs.length} giọng</button>
    </div>${summary}${cards}${castHtml}${library}`;
}
async function analyseStoryVoice(){
  const ta=document.getElementById("manualText");
  if(ta) MANUAL.text=ta.value;
  if(!String(MANUAL.text||"").trim()) return toast("Hãy nạp nội dung truyện trước","warn");
  VOICE_RECS.loading=true; renderStoryPanel();
  try{
    const r=await api("/api/story/voice_recommendations",{
      text:MANUAL.text,engine:MANUAL.engine||"capcut",voice:MANUAL.voice||"",
      txt_path:MANUAL.script_path||"",max_character_voices:8});
    VOICE_RECS.analysis=r.analysis||null;
    VOICE_RECS.items=r.recommendations||[];
    VOICE_RECS.cast=r.cast||[];
    VOICE_RECS.coverage=+r.assignment_coverage||0;
    VOICE_RECS.engine=r.engine||MANUAL.engine;
    toast(`Đã chọn giọng kể và ${VOICE_RECS.cast.length} giọng nhân vật`,"ok");
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
async function previewCastVoice(i){
  const c=VOICE_RECS.cast[i]; if(!c||!c.voice_id) return;
  const old=MANUAL.voice;
  MANUAL.voice=c.voice_id;
  try{ await ngheThuGiong("story"); }
  finally{ MANUAL.voice=old; }
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
      voice:MANUAL.voice,pitch:MANUAL.pitch,rate:MANUAL.rate,
      txt_path:MANUAL.script_path||"",
      voice_auto:STORY.auto_voice,multi_voice:STORY.multi_voice,
      max_character_voices:8
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
function dubSyncCheckHtml(op){
  const sc=(op&&op.last_sync_check)||{};
  const v=sc.verdict||"";
  const previews=Array.isArray(sc.previews)?sc.previews:[];
  const title=v==="ok"?"Khớp hình":v==="fail"?"Chưa khớp":v==="warn"?"Gần khớp":"";
  let html=`<div class="hint">Sau dựng giọng, nghe <b>3 đoạn mẫu</b> đầu/giữa/cuối (~10 giây). Đó là sản phẩm kiểm tra — file cả phim (*.vietsub_dub.mp4) chỉ ra khi bấm <b>Xuất video</b>.
    Lệch thì <b>Dựng lại giọng đọc</b>, không nhận dạng/dịch lại. Khớp thì Xuất (không cần tắt chương trình).</div>`;
  if(title) html+=`<div class="hint"><b>${title}</b>${sc.message?": "+esc(sc.message):""}</div>`;
  if(previews.length){
    html+=`<div class="rowbtns">${previews.map(p=>`<button class="btn" onclick="openOut('${esc(p.path||"").replace(/\\/g,"\\\\")}')">▶ ${esc(p.label||"Mẫu")}</button>`).join("")}</div>`;
  }
  if(v==="fail"){
    html+=`<label class="chk2"><input type="checkbox" ${op.force_export?"checked":""}
      onchange="setOpt('force_export',this.checked)"> Xuất bất chấp (track thiếu thoại)</label>
      <div class="hint">Mặc định không xuất khi kiểm tra fail. Chỉ bật sau khi đã nghe 3 đoạn mẫu và chấp nhận video thiếu giọng.</div>`;
  }
  return html;
}
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
function setLogoGeometry(k,v){
  if(!PR?.logo) return;
  PR.logo[k]=v;PR.logo.motion=false;save();draw();
}
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
function esc(s){return String(s||"").replace(/&/g,"&amp;").replace(/"/g,"&quot;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/'/g,"&#39;").replace(/`/g,"&#96;");}
function fmtms(s){
  s=s||0; const m=Math.floor(s/60), x=(s%60).toFixed(3);
  return `${String(Math.floor(m/60)).padStart(2,"0")}:${String(m%60).padStart(2,"0")}:${x.padStart(6,"0")}`;
}
function V_seek(t){ V().currentTime=t; tick(); }
