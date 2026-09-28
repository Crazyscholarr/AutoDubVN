"use strict";
/* ======================= trạng thái ======================= */
let ST={queue:[],selected:null,running:false,progress:{}};
let PR=null;            // dự án hiện tại
let JID=null;
let TAB="sub";
let ACT=null;           // vùng đang chọn {kind,index}
let CFG={translation:{},tts:{},content_pipeline:{},dang_youtube:{},nvidia_catalog:[]};
let IDEAS={items:[],detail:null,selectedId:"",search:"",provider:"browser",server:{},loaded:false,
  tab:localStorage.getItem("advn_ideas_tab")==="library"?"library":"collect",
  catalogLoaded:false,sources:[],chineseKeywords:[],sourceResults:[],
  onlineKeyword:"",sourceGroup:"zhihu",keywordIndex:-1,
  usageFilter:localStorage.getItem("advn_idea_usage_filter")||"unused"};
let MANUAL={text:"",name:"",engine:"edge",voice:"",pitch:"+0Hz",rate:"+0%",
             audio_path:"",audio_duration:0,output_path:"",status:"Sẵn sàng",error:"",
             image_status:"",image_ready:0,image_total:0,
             nhac_bai:"",nhac_db:-38,nhac_duck:true,nhac_ten:"",
             anh:"",slide_kieu:"chuyen_dong",writer_title:"",
             script_path:"",script_title:"",script_words:0,
             content_idea_id:"",content_outline:"",rewrite_brief:"",
             youtube_description:"",youtube_tags:[],calendar_path:"",
             thumbnail_path:"",description_path:""};
let STORY_MEDIA_MODE = localStorage.getItem("advn_story_media_mode") || "image";
let NHAC_LIST=[];
let saveTimer=null;
let _projectSaveInFlight=0, _editorRevision=0;
let configTimer=null;
let SETTINGS_TAB="api";
const NVIDIA_MODEL_CHOICES=[
  ["google/gemma-4-31b-it","Gemma 4 31B · dịch (ổn, ít treo)"],
  ["nvidia/nemotron-3-super-120b-a12b","Nemotron Super 120B · kho / dự phòng"],
  ["deepseek-ai/deepseek-v4-flash-0731","DeepSeek V4 Flash · hay nhưng hay kẹt cổng"],
  ["deepseek-ai/deepseek-v4-pro-0813","DeepSeek V4 Pro · chậm, dễ treo"],
  ["nvidia/nemotron-3.5-lightning-30b-a3b","Nemotron Lightning 30B · JSON rất nhanh"]
];
function nvidiaChoices(){
  const cat=Array.isArray(CFG.nvidia_catalog)?CFG.nvidia_catalog:[];
  if(cat.length) return cat.map(x=>[x.id, x.label||x.id]);
  return NVIDIA_MODEL_CHOICES;
}
function nvidiaModelSelect(cur, onchange){
  const val=String(cur||"google/gemma-4-31b-it");
  const choices=nvidiaChoices();
  const known=choices.some(x=>x[0]===val);
  const extra=known?"":`<option value="${esc(val)}" selected>Đang dùng · ${esc(val)}</option>`;
  return `<select onchange="${onchange}">
    ${choices.map(([id,lb])=>`<option value="${esc(id)}" ${val===id?"selected":""}>${lb}</option>`).join("")}
    ${extra}
  </select>`;
}
function nvidiaEnabled(){
  return String((CFG.translation||{}).provider||"")==="nvidia";
}
function nvidiaReadyPanel(mode){
  const tr=CFG.translation||{};
  const on=nvidiaEnabled();
  const setKey=mode==="settings"?"settingsSetTr":"setTrCfg";
  const modelOn=mode==="settings"
    ?"settingsSetTr('nvidia_model',this.value)"
    :"setTrCfg('nvidia_model',this.value)";
  const fastOn=mode==="settings"
    ?"settingsSetTr('nvidia_fast_model',this.value)"
    :"setTrCfg('nvidia_fast_model',this.value)";
  return `<div class="nvidia-ready">
    <label class="ready-line" style="cursor:pointer">
      <input type="checkbox" ${on?"checked":""} onchange="setNvidiaEnabled(this.checked)">
      <div><b>NVIDIA NIM đã gắn sẵn</b>
      <small>${on?"Đang bật: dịch dùng Gemma 4 31B; kho dùng Nemotron Super. Treo thì tự chuyển Super."
        :"Tắt = Gemini Web. Tick ô này khi muốn thử NVIDIA."}</small></div>
    </label>
    <div class="grid2">
      ${fld("NVIDIA API key",`<input type="password" autocomplete="off" value="${esc(tr.nvidia_api_key||"")}"
        placeholder="nvapi-..." onchange="${setKey}('nvidia_api_key',this.value)">`)}
      ${fld("Model dịch / viết", nvidiaModelSelect(
        tr.nvidia_model||"google/gemma-4-31b-it", modelOn))}
    </div>
    ${fld("Model nhanh (kho ý tưởng)", nvidiaModelSelect(
      tr.nvidia_fast_model||"nvidia/nemotron-3-super-120b-a12b", fastOn))}
    <div class="rowbtns">
      <button class="btn" type="button" onclick="testNvidiaCfg()">Thử kết nối NVIDIA</button>
    </div>
  </div>`;
}
async function setNvidiaEnabled(on){
  CFG.translation=CFG.translation||{};
  CFG.translation.provider=on?"nvidia":"browser";
  applyProviderSideEffects(CFG.translation.provider);
  await saveTrCfg(false);
  if(document.getElementById("settingsBody")&&SETTINGS_TAB==="api") renderSettingsModal();
  if(TAB==="tr") renderPanel();
  toast(on?"Đã bật NVIDIA NIM":"Đã tắt NVIDIA, về Gemini Web","ok");
}
async function testNvidiaCfg(){
  try{
    const r=await api("/api/test_translation",{
      translation:{...(CFG.translation||{}), provider:"nvidia"}});
    toast(r.message||"NVIDIA hoạt động","ok");
  }catch(e){
    toast("NVIDIA lỗi: "+e.message,"err");
  }
}
function setIdeaProvider(v){
  IDEAS.provider=v;
  CFG.content_pipeline=CFG.content_pipeline||{};
  CFG.content_pipeline.provider=v;
  saveTrCfg(false);
}
let _lastRev=-1;
let _manualRev=-1;
let _videoToolsRev=-1;
let _ideasRev=-1;
let _ideasWorking=false;
let _ideasLastActive="";
let _manualWorking=false;
let _cancelPending=false;
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
  let j;
  try{j=await r.json()}catch(e){throw new Error(`Phản hồi máy chủ không hợp lệ (HTTP ${r.status}).`);}
  if(!r.ok) throw new Error((j&&j.error)||`Máy chủ trả lỗi HTTP ${r.status}.`);
  if(j&&j.error) throw new Error(j.error);
  return j;
}
async function loadConfig(){
  try{
    const r=await api("/api/config");
    CFG.translation=r.translation||{};
    CFG.tts=r.tts||{};
    CFG.content_pipeline=r.content_pipeline||{};
    CFG.dang_youtube=r.dang_youtube||{};
    CFG.nvidia_catalog=Array.isArray(r.nvidia_catalog)?r.nvidia_catalog:[];
    if(CFG.content_pipeline.provider)
      IDEAS.provider=CFG.content_pipeline.provider;
    if(CFG.dang_youtube.auto_thumbnail!==undefined)
      STORY.auto_youtube_thumb=!!CFG.dang_youtube.auto_thumbnail;
    if(CFG.dang_youtube.auto_description!==undefined)
      STORY.auto_youtube_desc=!!CFG.dang_youtube.auto_description;
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
    const r=await api("/api/config",{
      translation:CFG.translation||{},
      content_pipeline:CFG.content_pipeline||{},
      dang_youtube:CFG.dang_youtube||{}});
    CFG.translation=r.translation||CFG.translation||{};
    if(r.content_pipeline) CFG.content_pipeline=r.content_pipeline;
    if(r.dang_youtube) CFG.dang_youtube=r.dang_youtube;
    if(showToast) toast("Đã lưu cấu hình dịch","ok");
    return true;
  }catch(e){
    toast("Lỗi lưu cấu hình dịch: "+e.message,"err");
    return false;
  }
}
function applyProviderSideEffects(v){
  CFG.content_pipeline=CFG.content_pipeline||{};
  if(v==="nvidia"){
    CFG.content_pipeline.provider="nvidia";
    IDEAS.provider="nvidia";
  }else if(String(CFG.content_pipeline.provider||"")==="nvidia"){
    CFG.content_pipeline.provider="browser";
    if(IDEAS.provider==="nvidia") IDEAS.provider="browser";
  }
  const sel=document.getElementById("ideaProvider");
  if(sel) sel.value=IDEAS.provider||"browser";
}
function setTrCfg(k,v,rerender){
  CFG.translation=CFG.translation||{};
  CFG.translation[k]=v;
  if(k==="provider") applyProviderSideEffects(v);
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
  _editorRevision++;
  requestPreviewRender();
  clearTimeout(saveTimer);
  // Capture the edited project now: switching queue items before the debounce
  // fires must not save the next project and discard the previous edit.
  const payload=JSON.parse(JSON.stringify({id:JID,regions:PR.regions,logo:PR.logo,
      sub_style:PR.sub_style,segments:PR.segments,options:PR.options}));
  saveTimer=setTimeout(()=>{
    saveTimer=null;
    persistProject(payload).catch(e=>toast("Không lưu được dự án: "+e.message,"err"));
  },400);
}
async function persistProject(payload){
  _projectSaveInFlight++;
  try{return await api("/api/project",payload);}
  finally{_projectSaveInFlight--;}
}
async function saveNow(){
  if(!PR||!JID) return;
  _editorRevision++;
  clearTimeout(saveTimer);
  saveTimer=null;
  await persistProject({id:JID,regions:PR.regions,logo:PR.logo,
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

/* ======================= modal cài đặt ======================= */
function openSettings(tab){
  SETTINGS_TAB=tab||SETTINGS_TAB||"api";
  const modal=document.getElementById("settingsModal");
  if(!modal) return;
  renderSettingsModal();
  modal.classList.add("open");
  modal.setAttribute("aria-hidden","false");
}
function closeSettings(){
  const modal=document.getElementById("settingsModal");
  if(!modal) return;
  modal.classList.remove("open");
  modal.setAttribute("aria-hidden","true");
}
function settingsBackdrop(e){ if(e.target&&e.target.id==="settingsModal") closeSettings(); }
function setSettingsTab(tab){
  SETTINGS_TAB=["api","voice","gpu","youtube"].includes(tab)?tab:"api";
  renderSettingsModal();
}
function settingsSetTr(k,v,redraw){
  CFG.translation=CFG.translation||{};
  CFG.translation[k]=v;
  if(k==="provider") applyProviderSideEffects(v);
  clearTimeout(configTimer);
  configTimer=setTimeout(()=>saveTrCfg(false),250);
  if(redraw) renderSettingsModal();
}
function settingsSetProjectOpt(k,v,redraw){
  if(!PR||!PR.options) return toast("Hãy chọn một video trước khi đổi thiết lập dự án","warn");
  PR.options[k]=v; save();
  if(redraw) renderSettingsModal();
}
function settingsSetYt(k,v,redraw){
  CFG.dang_youtube=CFG.dang_youtube||{};
  CFG.dang_youtube[k]=v;
  if(k==="auto_thumbnail"){
    STORY.auto_youtube_thumb=!!v;
    localStorage.setItem("advn_auto_yt_thumb",v?"1":"0");
  }
  if(k==="auto_description"){
    STORY.auto_youtube_desc=!!v;
    localStorage.setItem("advn_auto_yt_desc",v?"1":"0");
  }
  if(k==="auto_dub_thumbnail"&&PR&&PR.options){
    PR.options.auto_dub_thumbnail=!!v;
    save();
  }
  clearTimeout(configTimer);
  configTimer=setTimeout(()=>saveTrCfg(false),250);
  if(redraw) renderSettingsModal();
}
async function loginChatGPT(){
  try{
    if(DESK()&&pywebview.api.login_chatgpt){
      const r=await pywebview.api.login_chatgpt();
      if(r&&r.error) throw new Error(r.error);
    }else{
      await api("/api/youtube/login_chatgpt",{});
    }
    toast("Đã mở trình duyệt ChatGPT. Đăng nhập xong thì đóng hết cửa sổ đó.","ok");
  }catch(e){ toast("Không mở được đăng nhập ChatGPT: "+e.message,"err"); }
}
function settingsSetEngine(v){
  if(!PR) return toast("Hãy chọn một video trước khi đổi bộ giọng","warn");
  setEngine(v); renderSettingsModal();
}
function renderSettingsModal(){
  const body=document.getElementById("settingsBody"); if(!body) return;
  document.querySelectorAll("[data-settings-tab]").forEach(b=>
    b.classList.toggle("on",b.dataset.settingsTab===SETTINGS_TAB));
  const tr=CFG.translation||{}, op=(PR&&PR.options)||{};
  const disabled=PR?"":"disabled";
  if(SETTINGS_TAB==="api"){
    const provider=tr.provider||"browser";
    const providers={
      xkiro:["xkiro_api_key","xkiro_model","qwen/qwen3.5-flash:free","Xkiro API Key"],
      zai:["zai_api_key","zai_model","glm-4.7-flash","Z.AI API Key"],
      nvidia:["nvidia_api_key","nvidia_model","google/gemma-4-31b-it","NVIDIA API Key"],
      gemini:["gemini_api_key","gemini_model","gemini-3.6-flash","Google Gemini API Key"],
      inferx:["inferx_api_key","inferx_model","deepseek-v4-flash","InferX API Key"],
      tokenrouter:["tokenrouter_api_key","tokenrouter_model","moonshotai/kimi-k3-free","TokenRouter API Key"],
      tokenrouter_gemini:["tokenrouter_gemini_api_key","tokenrouter_gemini_model","google/gemini-3.6-flash","TokenRouter Gemini Key"],
      zenmux:["zenmux_api_key","zenmux_model","z-ai/glm-4.7-flash-free","ZenMux API Key"],
      tokenharbor:["tokenharbor_api_key","tokenharbor_model","deepseek-v4-flash:free","TokenHarbor API Key"]
    };
    const pf=providers[provider];
    body.innerHTML=`<div class="settings-group">
      ${fld("Dịch vụ dịch & AI",`<select onchange="settingsSetTr('provider',this.value,true)">
        <option value="browser" ${provider==="browser"?"selected":""}>Gemini web · profile đã đăng nhập</option>
        <option value="zai" ${provider==="zai"?"selected":""}>Z.AI · GLM-4.7-Flash miễn phí</option>
        <option value="gemini" ${provider==="gemini"?"selected":""}>Google Gemini API</option>
        <option value="tokenharbor" ${provider==="tokenharbor"?"selected":""}>TokenHarbor · DeepSeek V4 Flash free</option>
        <option value="xkiro" ${provider==="xkiro"?"selected":""}>Xkiro · Qwen miễn phí</option>
        <option value="nvidia" ${provider==="nvidia"?"selected":""}>NVIDIA NIM · catalog miễn phí</option>
        <option value="zenmux" ${provider==="zenmux"?"selected":""}>ZenMux · GLM-5.3 free</option>
        <option value="inferx" ${provider==="inferx"?"selected":""}>InferX · DeepSeek V4 Flash</option>
        <option value="tokenrouter_gemini" ${provider==="tokenrouter_gemini"?"selected":""}>TokenRouter · Gemini</option>
        <option value="tokenrouter" ${provider==="tokenrouter"?"selected":""}>TokenRouter · Kimi</option>
      </select>`)}
      ${nvidiaReadyPanel("settings")}
      ${provider==="nvidia"?"":pf?`<div class="grid2">
        ${fld(pf[3],`<input type="password" autocomplete="off" value="${esc(tr[pf[0]]||"")}"
          placeholder="Nhập API key…" onchange="settingsSetTr('${pf[0]}',this.value)">`)}
        ${fld("Model",`<input value="${esc(tr[pf[1]]||pf[2])}"
          onchange="settingsSetTr('${pf[1]}',this.value)">`)}</div>`:
        `<div class="ready-line"><span>✓</span><div><b>Sẵn sàng</b><small>Dùng profile Gemini đã đăng nhập, không cần API key. Bật NVIDIA ở ô phía trên khi muốn thử.</small></div></div>`}
      <div class="ai-note"><span>✣</span><p><b>Tự động tối ưu văn phong lồng tiếng:</b> hệ thống giữ đúng nghĩa gốc, Việt hoá tự nhiên và tối ưu nhịp đọc.</p></div>
      <div class="rowbtns"><button class="btn" onclick="testTrCfg()">Kiểm tra kết nối API</button>
        <button class="btn" onclick="setTab('tr');closeSettings()">Mở thiết lập dịch nâng cao</button></div>
    </div>`;
  }else if(SETTINGS_TAB==="voice"){
    const origDb=Number(op.keep_original_db??-30), engine=op.engine||CFG.tts.engine||"edge";
    body.innerHTML=`${PR?"":`<div class="settings-warning">Chưa chọn video — các mục theo dự án đang tạm khoá.</div>`}
      <div class="settings-group">
      ${fld("Engine Giọng Đọc Mặc Định",`<select ${disabled} onchange="settingsSetEngine(this.value)">
        <option value="edge" ${engine==="edge"?"selected":""}>Edge TTS (Online, miễn phí)</option>
        <option value="capcut" ${engine==="capcut"?"selected":""}>CapCut TTS (Online, giọng thịnh hành)</option>
        <option value="vieneu" ${engine==="vieneu"?"selected":""}>VieNeu TTS (Offline)</option>
      </select>`)}
      <div class="settings-range"><label>Mức Giảm Âm Thanh Nền Gốc <b>${origDb} dB</b></label>
        <input ${disabled} type="range" min="-60" max="0" value="${origDb}"
          oninput="if(PR){PR.options.keep_original_db=+this.value;PR.options.keep_original_muted=false;save();this.previousElementSibling.querySelector('b').textContent=this.value+' dB'}">
        <div><span>-60 dB (gần như tắt)</span><span>-30 dB (khuyến nghị)</span><span>0 dB (giữ nguyên)</span></div></div>
      <div class="settings-checks">
        <label><input ${disabled} type="checkbox" ${op.lock_av!==false?"checked":""}
          onchange="settingsSetProjectOpt('lock_av',this.checked,true);if(PR&&PR.options){PR.options.sync_mode=this.checked?'strict':'cascade';save();}"> Khóa cứng lời thoại với hình (đồng hồ PTS xuyên suốt + không lệch cộng dồn theo giờ)</label>
        <label><input ${disabled} type="checkbox" ${Number(op.max_speed||1.6)>1?"checked":""}
          onchange="settingsSetProjectOpt('max_speed',this.checked?1.6:1,true)"> Tự động chống đè âm khi câu dịch dài</label>
        <label><input ${disabled} type="checkbox" ${Number(op.min_gap??.08)>0?"checked":""}
          onchange="settingsSetProjectOpt('min_gap',this.checked?0.08:0,true)"> Chèn khoảng nghỉ giữa các câu hội thoại</label>
      </div></div>`;
  }else if(SETTINGS_TAB==="youtube"){
    const yt=CFG.dang_youtube||{};
    body.innerHTML=`<div class="settings-group">
      <div class="ai-note"><span>▶</span><p><b>Lồng tiếng / dịch phim.</b> Bật ô dưới thì sau khi xuất video, app cắt vài cảnh nổi, <b>quăng sang ChatGPT</b> để vẽ thumbnail 16:9, rồi tự ghép tiêu đề lớn lên ảnh.</p></div>
      <div class="settings-checks">
        <label><input type="checkbox" ${yt.auto_dub_thumbnail===true?"checked":""}
          onchange="settingsSetYt('auto_dub_thumbnail',this.checked)"> Tự cắt cảnh → ChatGPT làm thumbnail + tiêu đề trên ảnh</label>
      </div>
      <div class="hint">Mặc định tắt. Ảnh ChatGPT cho phim lồng tiếng thường xấu; chỉ bật khi thật sự cần.
        Cần đăng nhập ChatGPT (hồ sơ riêng bên dưới). Lưu
        <b>thumbnail.jpg</b>, <b>thumbnails/</b>, <b>scenes/</b> và <b>cau_thumbnail.txt</b>.</div>
    </div>
    <div class="settings-group">
      <div class="ai-note"><span>▶</span><p><b>Kể chuyện · ChatGPT web, hồ sơ riêng.</b> Không dùng chung với Gemini.
        Máy không có Edge thì app mở Chrome. Thấy trang ChatGPT rồi <b>đóng hết cửa sổ trình duyệt đó</b> trước khi tạo video.</p></div>
      <div class="settings-checks">
        <label><input type="checkbox" ${yt.auto_thumbnail!==false?"checked":""}
          onchange="settingsSetYt('auto_thumbnail',this.checked)"> Tự tạo thumbnail YouTube 16:9 sau khi dựng video kể chuyện</label>
        <label><input type="checkbox" ${yt.auto_description!==false?"checked":""}
          onchange="settingsSetYt('auto_description',this.checked)"> Tự viết mô tả 6 khối cho kênh Gốc Mít Kể Chuyện</label>
      </div>
      <div class="export-path"><span>Hồ sơ trình duyệt ChatGPT</span>
        <b>${esc(yt.browser_profile||"browser_profile_chatgpt")}</b></div>
      <div class="rowbtns">
        <button class="btn pri" onclick="loginChatGPT()">Mở cửa sổ đăng nhập ChatGPT</button>
      </div>
      <div class="hint">Ảnh ChatGPT không có chữ. App tự vẽ chữ lớn bên trái rồi lưu
        <b>thumbnail.jpg</b> và <b>mo_ta_youtube.txt</b> trong thư mục video kể chuyện.</div>
    </div>`;
  }else{
    const crf=Number(op.crf||20);
    body.innerHTML=`${PR?"":`<div class="settings-warning">Chưa chọn video — các mục theo dự án đang tạm khoá.</div>`}
      <div class="settings-group">
      <label class="gpu-toggle"><input ${disabled} type="checkbox" ${op.use_gpu!==false?"checked":""}
        onchange="settingsSetProjectOpt('use_gpu',this.checked,true)"><span><b>Bật tăng tốc phần cứng GPU NVENC (NVIDIA CUDA)</b>
        <small>Tăng tốc render video nhiều lần so với CPU khi bộ lọc hình ảnh được bật.</small></span></label>
      <div class="settings-range"><label>Chất lượng Video CRF (Giá trị thấp = Nét hơn) <b>CRF ${crf}</b></label>
        <input ${disabled} type="range" min="14" max="30" value="${crf}"
          oninput="if(PR){PR.options.crf=+this.value;save();this.previousElementSibling.querySelector('b').textContent='CRF '+this.value}"></div>
      <div class="export-path"><span>Thư mục xuất video của dự án</span><b>${esc((PR&&PR.output_dir)||"Được tạo cạnh video nguồn")}</b></div>
      <button class="btn" ${disabled} onclick="setTab('export');closeSettings()">Mở toàn bộ tuỳ chọn xuất video</button>
      </div>`;
  }
}
async function saveSettingsModal(){
  try{
    const ok=await saveTrCfg(false);
    if(PR) await saveNow();
    if(ok){ toast("Đã lưu cấu hình","ok"); closeSettings(); }
  }catch(e){ toast("Không lưu được cấu hình: "+e.message,"err"); }
}


/* ======================= CHẾ ĐỘ 2: VIDEO KỂ CHUYỆN ======================= */
let MODE=localStorage.getItem("advn_mode")||"dub";
if(!["dub","story","tools","ideas"].includes(MODE)) MODE="dub";
const VIDEO_TOOLS={
  tab:localStorage.getItem("advn_video_tools_tab")==="cut"?"cut":"download",
  links:"",
  search_keyword:"", search_provider:"all", search_count:10,
  search_results:[], search_selected:[],
  quality:localStorage.getItem("advn_vt_quality")||"best",
  download_output:localStorage.getItem("advn_vt_download_output")||"",
  download_files:[],
  cut_inputs:[],
  cut_output:localStorage.getItem("advn_vt_cut_output")||"",
  min_minutes:Math.max(.1,Number(localStorage.getItem("advn_vt_min_minutes"))||5),
  max_minutes:Math.max(.1,Number(localStorage.getItem("advn_vt_max_minutes"))||10),
  cut_files:[],
  server:{}
};
const DEFAULT_STORY_CTA="Bạn đang nghe chuyện tại gốc mít kể chuyện . Nếu thấy câu chuyện này ý nghĩa, cô chú, anh chị nhớ đăng ký kênh, bật chuông và để lại một lời bình luận để tiếp tục đồng hành cùng Gốc Mít nghen. Mọi nội dung đều hư cấu xin mọi người không làm theo bất cứ dưới hình thức nào hoặc tung tin đồn , chúng tôi không chịu trách nhiệm .";
const STORY_TITLE_PROMPT=`Bạn đặt tiêu đề video cho kênh audio Việt Nam "Gốc Mít Kể Chuyện", chủ đề chuyện gia đình, tuổi già và chuyện tâm linh ông bà kể, khán giả từ 45 tuổi trở lên.

NỘI DUNG TRUYỆN: [DÁN TÓM TẮT]

Hãy tạo 8 tiêu đề theo đúng công thức 3 phần:
[MÓC CẢM XÚC] : [MỆNH ĐỀ VIẾT HOA TOÀN BỘ] | [ĐUÔI TỪ KHÓA]

Móc cảm xúc chọn trong: Nghe Mà Thấm / Nghe THẤM Tận Xương / Nghe Là Khóc / Nghe Mà Nghẹn Lòng / Nghe Mà Rơi Nước Mắt / Nghe Sướng Lỗ Tai / Nghe Mà Sốc / Truyện Ngắn Tuổi Xế Chiều Cực Hay. Riêng chuyện tâm linh có thể dùng: Nghe Mà Lạnh Gáy / Chuyện Ông Bà Kể / Nghe Mà Rùng Mình / Chuyện Làng Quê Kỳ Bí

Mệnh đề viết hoa nêu đúng tình huống sốc nhất, 8 đến 14 từ, có số liệu nếu phù hợp.
Đuôi từ khóa chọn trong: Kể Chuyện Đêm Khuya / Đọc Truyện Đêm Khuya / Kể Chuyện Tuổi Già / Kể Chuyện Làng Quê / Kể Chuyện Tâm Linh

Tổng tiêu đề không quá 100 ký tự; không đưa tên kênh, số tập hoặc kết thúc. Trả về 8 dòng, không giải thích.`;
const STORY={
  imgs:[],                 // đường dẫn ảnh / thư mục, đúng thứ tự cảnh
  pack:localStorage.getItem("advn_story_image_pack")||"",
  packTitle:"",            // tiêu đề sở hữu gói ảnh; chặn mượn ảnh truyện trước
  sel:-1,                  // ảnh đang xem trước
  step:Math.max(0,Math.min(7,Number(localStorage.getItem("advn_story_step"))||0)),
  aspect:localStorage.getItem("advn_aspect")||"16:9",
  fps:30, kieu:"chuyen_dong",
  nhac_enabled:true, sub_enabled:true,
  auto_images:localStorage.getItem("advn_auto_images")!=="0",
  auto_youtube_thumb:localStorage.getItem("advn_auto_yt_thumb")!=="0",
  auto_youtube_desc:localStorage.getItem("advn_auto_yt_desc")!=="0",
  scene_count:Math.max(6,Math.min(14,Number(localStorage.getItem("advn_story_scene_count"))||8)),
  auto_voice:localStorage.getItem("advn_auto_voice")!=="0",
  multi_voice:localStorage.getItem("advn_multi_voice")!=="0",
  cta_enabled:localStorage.getItem("advn_story_cta_enabled")!=="0",
  cta_text:DEFAULT_STORY_CTA,
  cta_positions:localStorage.getItem("advn_story_cta_positions")||"12,55",
  cta_speed:Math.max(1,Math.min(2,Number(localStorage.getItem("advn_story_cta_speed"))||2)),
  logo_enabled:localStorage.getItem("advn_story_logo_enabled")==="1",
  logo_path:localStorage.getItem("advn_story_logo_path")||"",
  logo_position:localStorage.getItem("advn_story_logo_position")||"top-right",
  logo_width:Math.max(4,Math.min(40,Number(localStorage.getItem("advn_story_logo_width"))||12)),
  logo_opacity:Math.max(5,Math.min(100,Number(localStorage.getItem("advn_story_logo_opacity"))||82)),
  source_keyword:"", source_count:10, source_provider:"all", source_results:[], source_selected:[],
  source_links:"", source_videos:[], source_status:"", source_sel:0,
  source_clips:[], cut_status:"", cut_done:0, cut_total:0,
  source_effect:"tinh", source_cover:"none",
  source_clip_min_minutes:Math.max(.1,Number(localStorage.getItem("advn_source_clip_min"))||5),
  source_clip_max_minutes:Math.max(.1,Number(localStorage.getItem("advn_source_clip_max"))||10),
  source_random:localStorage.getItem("advn_source_random")!=="0",
  source_random_seed:Number(localStorage.getItem("advn_source_seed"))||Date.now(),
  source_zoom:Math.max(40,Math.min(220,Number(localStorage.getItem("advn_source_zoom"))||100)),
  source_x:Math.max(0,Math.min(100,Number(localStorage.getItem("advn_source_x"))||50)),
  source_y:Math.max(0,Math.min(100,Number(localStorage.getItem("advn_source_y"))||50)),
  source_crop_left:0, source_crop_right:0, source_crop_top:0, source_crop_bottom:0,
  character_enabled:localStorage.getItem("advn_story_character_enabled")==="1",
  character_scale:Math.max(.55,Math.min(1.8,Number(localStorage.getItem("advn_story_character_scale"))||1)),
  character_opacity:Math.max(25,Math.min(100,Number(localStorage.getItem("advn_story_character_opacity"))||92)),
  sub:{size:48,color:"#FFFFFF",outline:2,bold:true,
       align:"bottom-center",margin_v:90},
};
function rerenderMode(){
  if(MODE==="story") renderStory();
  else if(MODE==="tools") renderVideoTools();
  else if(MODE==="ideas") renderIdeas();
  else renderPanel();
}
function setMode(m){
  MODE=["dub","story","tools","ideas"].includes(m)?m:"dub";
  localStorage.setItem("advn_mode",MODE);
  document.body.dataset.mode=MODE;
  document.getElementById("storyMain").style.display=MODE==="story"?"grid":"none";
  document.getElementById("videoToolsMain").style.display=MODE==="tools"?"block":"none";
  document.getElementById("ideasMain").style.display=MODE==="ideas"?"block":"none";
  document.getElementById("mDub").classList.toggle("on",MODE==="dub");
  document.getElementById("mStory").classList.toggle("on",MODE==="story");
  document.getElementById("mVideoTools").classList.toggle("on",MODE==="tools");
  document.getElementById("mIdeas").classList.toggle("on",MODE==="ideas");
  if(MODE==="story"){ loadManualVoices(false); loadNhacNen(false); renderStory(); }
  else if(MODE==="tools") renderVideoTools();
  else if(MODE==="ideas"){
    const provider=document.getElementById("ideaProvider");
    if(provider) provider.value=IDEAS.provider||"browser";
    ideasLoadCatalog();
    ideasLoad(false);
  }
  else renderPanel();
}
