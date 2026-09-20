"use strict";
/* ======================= kho ý tưởng & kế hoạch ======================= */
function setIdeasTab(tab){
  IDEAS.tab=tab==="library"?"library":"collect";
  localStorage.setItem("advn_ideas_tab",IDEAS.tab);
  const collect=document.getElementById("ideaCollectView"),library=document.getElementById("ideaLibraryView");
  if(collect) collect.style.display=IDEAS.tab==="collect"?"grid":"none";
  if(library) library.style.display=IDEAS.tab==="library"?"flex":"none";
  document.getElementById("ideaTabCollect")?.classList.toggle("on",IDEAS.tab==="collect");
  document.getElementById("ideaTabLibrary")?.classList.toggle("on",IDEAS.tab==="library");
  if(IDEAS.tab==="collect") renderIdeaSourceResults();
  else {renderIdeaList();renderIdeaDetail();}
}
function ideaSourceKeys(group){
  const chineseReal=["zhihu_yanxuan","douban_groups"];
  const chineseLiterary=["douban_read","hongxiu"];
  const chineseSerial=["fanqie","qidian","hongxiu","qimao","zongheng"];
  const chineseFolklore=["660i_story","gushi365","minjian6mj"];
  const chinese=[...new Set([...chineseReal,...chineseLiterary,...chineseSerial,...chineseFolklore])];
  const vietnamSpiritual=["dantri_tamlinh","vnexpress_tamlinh","phunuonline"];
  const groups={
    zhihu:["zhihu_yanxuan"],
    chinese_real:chineseReal,
    chinese_literary:chineseLiterary,
    chinese_serial:chineseSerial,
    chinese_folklore:chineseFolklore,
    spiritual:[...chineseFolklore,"zhihu_yanxuan","douban_groups"],
    spiritual_vi:vietnamSpiritual,
    vietnam:["vnexpress_tamsu","dantri_tinhyeu","webtretho_honnhan","phunuonline","dantri_tamlinh","vnexpress_tamlinh"],
    reddit:["reddit_aita"],
    chinese,
    all:[...chinese,"vnexpress_tamsu","dantri_tinhyeu","webtretho_honnhan","phunuonline","dantri_tamlinh","vnexpress_tamlinh","reddit_aita"]
  };
  return groups[group]||groups.zhihu;
}
async function ideasLoadCatalog(){
  if(IDEAS.catalogLoaded) return;
  try{
    const data=await api("/api/content/catalog");
    IDEAS.sources=Array.isArray(data.sources)?data.sources:[];
    IDEAS.chineseKeywords=Array.isArray(data.chinese_keywords)?data.chinese_keywords:[];
    IDEAS.catalogLoaded=true;
    const select=document.getElementById("ideaKeywordPreset");
    if(select){
      const rows=IDEAS.chineseKeywords.map((x,i)=>({x,i}));
      const isSpirit=r=>(r.x.source_group||"").startsWith("spiritual");
      const spirit=rows.filter(isSpirit);
      const family=rows.filter(r=>!isSpirit(r));
      const opt=r=>`<option value="${r.i}">${esc(r.x.keyword)} — ${esc(r.x.meaning)}</option>`;
      select.innerHTML=`<option value="">— Chọn chủ đề hoặc tự gõ bên dưới —</option>`+
        (spirit.length?`<optgroup label="Tâm linh · ông bà kể (${spirit.length})">${spirit.map(opt).join("")}</optgroup>`:"")+
        (family.length?`<optgroup label="Gia đình · tuổi già (${family.length})">${family.map(opt).join("")}</optgroup>`:"");
      if(IDEAS.keywordIndex>=0) select.value=String(IDEAS.keywordIndex);
    }
    renderIdeaKeywordInfo();
  }catch(e){toast("Không tải được danh mục nguồn: "+e.message,"err");}
}
function ideasChooseKeyword(value){
  const index=value===""?-1:Number(value);
  IDEAS.keywordIndex=Number.isFinite(index)?index:-1;
  const item=IDEAS.chineseKeywords[IDEAS.keywordIndex];
  if(item){
    IDEAS.onlineKeyword=item.keyword||"";
    const input=document.getElementById("ideaOnlineKeyword");if(input)input.value=IDEAS.onlineKeyword;
    if(item.source_group){
      IDEAS.sourceGroup=item.source_group;
      const group=document.getElementById("ideaSourceGroup");if(group)group.value=IDEAS.sourceGroup;
    }
  }
  renderIdeaKeywordInfo();
}
function renderIdeaKeywordInfo(){
  const box=document.getElementById("ideaKeywordInfo");if(!box)return;
  const x=IDEAS.chineseKeywords[IDEAS.keywordIndex];
  box.innerHTML=x?`<b>${esc(x.meaning)}</b> · Nhóm: ${esc(x.topic)}<br>${esc(x.note||"")}`:
    "Chọn một từ khóa để xem nghĩa và nhóm chủ đề.";
}
async function ideasSearchSources(){
  const keyword=String(document.getElementById("ideaOnlineKeyword")?.value||IDEAS.onlineKeyword||"").trim();
  if(!keyword) return toast("Hãy chọn hoặc nhập từ khóa tìm chuyện","warn");
  IDEAS.onlineKeyword=keyword;
  IDEAS.sourceGroup=document.getElementById("ideaSourceGroup")?.value||IDEAS.sourceGroup||"zhihu";
  const preset=IDEAS.chineseKeywords[IDEAS.keywordIndex]||{};
  const limit=Math.max(1,Math.min(50,Number(document.getElementById("ideaSearchLimit")?.value)||30));
  IDEAS.sourceResults=[];renderIdeaSourceResults();
  try{
    await api("/api/content/search",{keyword,limit,source_keys:ideaSourceKeys(IDEAS.sourceGroup),
      topic:preset.topic||"",meaning:preset.meaning||""});
    IDEAS.server={working:true,active:"source_search",status:`Đang tìm “${keyword}”…`,progress:0};
    _ideasLastActive="source_search";renderIdeaStatus();
  }catch(e){toast(e.message,"err");}
}
function ideasToggleSource(index,selected){
  const item=IDEAS.sourceResults[index];
  if(item&&!item.used_before&&!item.already_downloaded)
    item.selected=selected;
  renderIdeaSourceResults();
}
function ideasToggleAllSources(selected){
  IDEAS.sourceResults.forEach(x=>x.selected=selected&&!x.used_before&&!x.already_downloaded);
  renderIdeaSourceResults();
}
async function ideasOpenSource(encoded){
  const url=decodeURIComponent(String(encoded||""));if(!url)return;
  if(DESK()&&pywebview.api.open_url) await pywebview.api.open_url(url);
  else window.open(url,"_blank","noopener");
}
function renderIdeaSourceResults(){
  const box=document.getElementById("ideaSourceResults"),summary=document.getElementById("ideaResultSummary");
  if(!box)return;
  const rows=IDEAS.sourceResults||[], chosen=rows.filter(x=>x.selected===true).length;
  const usedCount=rows.filter(x=>x.used_before).length;
  const knownReads=rows.filter(x=>Number(x.read_count)>0).length;
  const errors=new Map((IDEAS.server?.download_errors||[]).map(x=>[x.url,x.error]));
  if(summary) summary.textContent=rows.length?`${rows.length} kết quả · ${knownReads} có lượt đọc · ${usedCount} đã dùng · ${chosen} đang chọn`:
    (IDEAS.server?.active==="source_search"&&IDEAS.server?.working?"Đang tìm trên các nguồn đã chọn…":"Chọn từ khóa và bấm Tìm nguồn nội dung.");
  box.innerHTML=rows.map((x,i)=>{
    const failed=errors.get(x.url)||x.fetch_error,done=x.download_status==="Đã tải";
    const blocked=x.used_before||x.already_downloaded;
    const reads=Number(x.read_count)||0,engagement=Number(x.engagement_count)||0;
    return `<article class="idea-source-row ${blocked?"blocked":""}" onclick="ideasToggleSource(${i},!${x.selected===true})">
      <input type="checkbox" ${x.selected===true?"checked":""} ${blocked?"disabled":""}
        onclick="event.stopPropagation()" onchange="ideasToggleSource(${i},this.checked)">
      <div><h4>${esc(x.title||"Bài chưa có tiêu đề")}</h4><p>${esc(x.excerpt||"Chưa có mô tả; hệ thống sẽ kiểm tra toàn văn khi tải.")}</p>
        <div class="idea-source-meta"><span>#${Number(x.popularity_rank)||i+1} ưu tiên</span>
          ${Number(x.relevance_score)>0?`<span class="relevance">Khớp chủ đề ${Number(x.relevance_score)}/100</span>`:""}<span>${esc(x.source_name||x.channel||"web")}</span>
          ${x.language?`<span>${esc(String(x.language).toUpperCase())}</span>`:""}
          ${x.narrative_style?`<span>${esc(x.narrative_style)}</span>`:""}
          <span class="${reads?"reads":"unknown"}">${reads?`◉ ${reads.toLocaleString("vi-VN")} lượt đọc`:
            engagement?`★ ${engagement.toLocaleString("vi-VN")} tương tác`:"Lượt đọc không công khai"}</span>
          ${x.used_before?`<span class="fail">Đã dùng · khóa lặp</span>`:x.already_downloaded?`<span class="done">Đã có trong kho</span>`:""}
          ${done?`<span class="done">Đã tải</span>`:""}${failed&&!x.used_before?`<span class="fail" title="${esc(failed)}">Chưa đọc được</span>`:""}</div></div>
      <div class="idea-source-row-actions">
        ${x.used_before?`<button class="btn sm" disabled>Đã dùng</button>`:
          x.already_downloaded?`<button class="btn sm" onclick="event.stopPropagation();ideasOpenExisting('${esc(x.existing_id||"")}')">Mở trong kho</button>`:
          `<button class="btn pri sm" onclick="event.stopPropagation();ideasDownloadOne(${i})">⇩ ${x.content_ready===false?"Thử tải với Cookie":"Tải chuyện này"}</button>`}
        <button class="btn sm idea-source-open" onclick="event.stopPropagation();ideasOpenSource('${encodeURIComponent(x.url||"")}')" title="Xem bài gốc">↗</button>
      </div>
    </article>`;
  }).join("")||`<div class="idea-source-empty"><div><b>Chưa có kết quả tìm kiếm</b>Chọn một từ khóa Trung ở cột trái hoặc tự nhập từ khóa.</div></div>`;
}
function ideaDownloadCookie(){return String(document.getElementById("ideaZhihuCookie")?.value||"").trim();}
async function ideasDownloadSelected(){
  const items=IDEAS.sourceResults.filter(x=>x.selected===true&&!x.used_before&&!x.already_downloaded);
  if(!items.length) return toast("Hãy chọn ít nhất một bài cần tải","warn");
  try{
    const r=await api("/api/content/download",{items,cookie:ideaDownloadCookie(),concurrency:3});
    IDEAS.server={working:true,active:"source_download",status:`Đang tải ${r.total} bài vào kho…`,
      total:r.total,done:0,progress:0,provider:r.provider,provider_model:r.provider_model,
      provider_configured:r.provider_configured,started_at:Date.now()/1000};
    _ideasLastActive="source_download";renderIdeaStatus();
  }catch(e){toast(e.message,"err");}
}
async function ideasDownloadOne(index){
  const item=IDEAS.sourceResults[index];
  if(!item||item.used_before||item.already_downloaded)return;
  item.selected=true;renderIdeaSourceResults();
  try{
    const r=await api("/api/content/download",{items:[item],cookie:ideaDownloadCookie(),concurrency:1});
    IDEAS.server={working:true,active:"source_download",status:"Đang đưa chuyện đã chọn vào kho…",
      total:r.total,done:0,progress:0,provider:r.provider,provider_model:r.provider_model,
      provider_configured:r.provider_configured,started_at:Date.now()/1000};
    _ideasLastActive="source_download";renderIdeaStatus();
  }catch(e){toast(e.message,"err");}
}
async function ideasOpenExisting(id){
  if(!id)return;setIdeasTab("library");await ideasSelect(id);
}
async function ideasDownloadUrls(){
  const urls=String(document.getElementById("ideaSourceUrls")?.value||"").split(/\r?\n/).map(x=>x.trim()).filter(Boolean);
  if(!urls.length) return toast("Hãy dán ít nhất một link bài viết","warn");
  try{
    const r=await api("/api/content/download",{urls,cookie:ideaDownloadCookie(),concurrency:3});
    IDEAS.server={working:true,active:"source_download",status:`Đang tải ${r.total} link vào kho…`,
      total:r.total,done:0,progress:0,provider:r.provider,provider_model:r.provider_model,
      provider_configured:r.provider_configured,started_at:Date.now()/1000};
    _ideasLastActive="source_download";renderIdeaStatus();
  }catch(e){toast(e.message,"err");}
}
function ideaArray(value){
  if(Array.isArray(value)) return value.map(x=>String(x||"").trim()).filter(Boolean);
  return String(value||"").split(/[;,\n]+/).map(x=>x.trim()).filter(Boolean);
}
function ideaLines(value){
  return String(value||"").split(/\r?\n+/).map(x=>x.trim()).filter(Boolean);
}
function ideaWasUsed(item){
  if(!item)return false;
  return !!item.used_at||Number(item.usage_count)>0||["đã dùng","used","rendered","published"].includes(String(item.status||"").toLocaleLowerCase("vi"));
}
function ideaSelectedIds(){ return IDEAS.items.filter(x=>x.selected&&!ideaWasUsed(x)).map(x=>x.id); }
function ideaStoryTargetId(){
  const selected=ideaSelectedIds(),detailId=String(IDEAS.detail?.id||"");
  // Một ô được tick là chỉ định rõ ràng nhất của người dùng, kể cả khung chi
  // tiết chưa kịp đổi theo. Với nhiều ô, mục đang mở phải nằm trong số đã tick.
  if(selected.length===1) return selected[0];
  if(selected.length>1) return selected.includes(detailId)?detailId:"";
  return detailId;
}
function ideaStatusClass(item){
  const score=Number(item.priority_score)||0;
  return score>=7.2?"good":"";
}
async function ideasLoad(keepDetail){
  try{
    const data=await api("/api/content/list?limit=10000");
    if(data.default_provider){
      IDEAS.provider=data.default_provider;
      const sel=document.getElementById("ideaProvider");
      if(sel) sel.value=IDEAS.provider;
    }
    IDEAS.items=Array.isArray(data.items)?data.items:[];
    IDEAS.loaded=true;
    if(!keepDetail&&!IDEAS.selectedId&&IDEAS.items.length)
      IDEAS.selectedId=IDEAS.items[0].id;
    if(IDEAS.selectedId&&!IDEAS.items.some(x=>x.id===IDEAS.selectedId)){
      IDEAS.selectedId=IDEAS.items[0]?.id||""; IDEAS.detail=null;
    }
    const visible=ideasFiltered();
    if(IDEAS.selectedId&&!visible.some(x=>x.id===IDEAS.selectedId)){
      IDEAS.selectedId=visible[0]?.id||"";IDEAS.detail=null;
    }
    if(IDEAS.selectedId&&(!IDEAS.detail||IDEAS.detail.id!==IDEAS.selectedId))
      await ideasSelect(IDEAS.selectedId,false);
    renderIdeas();
  }catch(e){ toast("Không đọc được kho ý tưởng: "+e.message,"err"); }
}
function ideasFiltered(){
  const q=String(IDEAS.search||"").toLocaleLowerCase("vi").trim();
  const usage=IDEAS.usageFilter||"unused";
  const usageRows=IDEAS.items.filter(x=>usage==="all"||(usage==="used"?ideaWasUsed(x):!ideaWasUsed(x)));
  if(!q) return usageRows;
  const tokens=q.split(/\s+/).filter(Boolean);
  return usageRows.filter(x=>{
    const haystack=[x.title_original,x.title_localized,x.source,
    x.primary_genre,x.emotion,x.series,(x.themes||[]).join(" ")]
      .join(" ").toLocaleLowerCase("vi");
    // Từ khóa dài như "婆媳矛盾 故事" vẫn tìm ra bài có phần chủ đề chính,
    // thay vì khiến người dùng tưởng kho trống vì thiếu đúng một từ phụ.
    return tokens.some(token=>haystack.includes(token));
  });
}
function renderIdeaStatus(){
  const box=document.getElementById("ideaStatus"); if(!box) return;
  const s=IDEAS.server||{};
  if(!s.working&&!s.status&&!s.error){box.innerHTML="";return;}
  const pct=Math.max(0,Math.min(100,Number(s.progress)||0));
  const providerNames={tokenharbor:"TokenHarbor",nvidia:"NVIDIA",zenmux:"ZenMux",zai:"Z.AI",gemini:"Gemini API",
    tokenrouter:"TokenRouter",heuristic:"Quy tắc local",offline:"Offline",
    browser:"Gemini Web",perplexity_browser:"Claude · Perplexity Web"};
  const providerKey=String(s.provider||"").toLowerCase();
  const providerName=providerNames[providerKey]||String(s.provider||"").toUpperCase();
  const providerText=providerName?providerName+(s.provider_model?` · ${esc(s.provider_model)}`:""):"";
  const stageNames={prepare:"Chuẩn bị",download:"Tải toàn văn",downloaded:"Đã tải nguồn",
    ai_sending:"Gửi yêu cầu AI",ai_waiting:"Chờ AI phản hồi",ai_parsing:"Kiểm tra phản hồi AI",
    ai_done:"AI hoàn tất",ai_warning:"AI chuyển offline",ai_error:"AI lỗi",
    offline:"Phân tích offline",saved:"Lưu SQLite",done:"Hoàn tất",error:"Có lỗi"};
  const stage=stageNames[String(s.current_stage||"")]||String(s.current_stage||"").replace(/^ai_/,"AI · ");
  const elapsed=s.working&&Number(s.started_at)>0?
    Math.max(0,Math.floor(Date.now()/1000-Number(s.started_at))):0;
  const elapsedText=elapsed>=60?`${Math.floor(elapsed/60)}p ${elapsed%60}s`:`${elapsed}s`;
  const activity=(Array.isArray(s.activity)?s.activity:[]).slice(-10);
  const logRows=activity.map(row=>{
    const time=Number(row.t)>0?new Date(Number(row.t)*1000).toLocaleTimeString("vi-VN",{hour12:false}):"";
    return `<div class="idea-log-row ${esc(row.kind||"info")}"><time>${esc(time)}</time><span>${esc(row.message||"")}</span></div>`;
  }).join("");
  const planOutput=s.export_xlsx?`<button class="btn sm" onclick="openFolder('${esc(s.output_dir||s.export_xlsx).replace(/\\/g,"\\\\")}')">Mở thư mục kế hoạch</button>`:"";
  const calendarOutput=s.calendar_path?`<button class="btn sm" onclick="openOut('${esc(s.calendar_path).replace(/\\/g,"\\\\")}')">Mở lịch nội dung Excel</button>`:"";
  const warning=String(s.warning||"");
  box.innerHTML=`<section class="idea-status-card ${s.error?"err":warning?"warn":""}">
    <div class="idea-status-main"><b>${s.working?"Đang xử lý":"Trạng thái"}</b>
      ${providerText?`<span class="idea-ai-chip ${s.provider_configured?"ready":"offline"}">Trình phân tích: ${providerText}</span>`:""}
      ${stage?`<span class="idea-stage-chip">${esc(stage)}</span>`:""}
      ${s.working?`<span class="idea-elapsed">Đã chạy ${elapsedText}</span>`:""}
      <strong>${Math.round(pct)}%</strong></div>
    <div class="idea-status-message">${esc(s.error||s.status||"Sẵn sàng")}${s.total?` · ${Number(s.done)||0}/${s.total}`:""}</div>
    ${s.current_item?`<div class="idea-current-item">Đang làm: ${esc(s.current_item)}</div>`:""}
    ${s.working?`<div class="idea-status-bar"><i style="width:${pct}%"></i></div>`:""}
    ${s.error?`<div class="idea-status-alert error">${esc(s.error)}</div>`:""}
    ${warning&&!s.error?`<div class="idea-status-alert warning"><span>${esc(warning)}</span><button class="btn sm" onclick="openSettings('api')">Mở Cài đặt API</button></div>`:""}
    ${(Number(s.ai_success)||Number(s.ai_failed))?`<div class="idea-ai-counts"><span>AI thành công: ${Number(s.ai_success)||0}</span><span>Offline/lỗi AI: ${Number(s.ai_failed)||0}</span></div>`:""}
    ${activity.length?`<details class="idea-live-log" ${s.working||s.error||warning?"open":""}><summary>Nhật ký tiến trình (${activity.length} dòng gần nhất)</summary><div>${logRows}</div></details>`:""}
    ${(calendarOutput||planOutput)?`<div class="idea-status-actions">${calendarOutput}${planOutput}</div>`:""}
  </section>`;
}
function renderIdeaList(){
  const list=document.getElementById("ideaList"), count=document.getElementById("ideaCount");
  if(!list) return;
  const rows=ideasFiltered();
  if(count) count.textContent=`${rows.length}/${IDEAS.items.length} mục · ${ideaSelectedIds().length} chọn`;
  document.getElementById("ideaClearSearch")?.classList.toggle("show",!!String(IDEAS.search||"").trim());
  list.innerHTML=rows.map(item=>{const used=ideaWasUsed(item);return `<article class="idea-card ${item.id===IDEAS.selectedId?"on":""} ${used?"used":""}" onclick="ideasSelect('${esc(item.id)}')">
    <input type="checkbox" ${item.selected?"checked":""} ${used?"disabled":""} onclick="event.stopPropagation()" onchange="ideasToggle('${esc(item.id)}',this.checked)">
    <div><h4>${esc(item.title_localized||item.title_original||"Chưa có tiêu đề")}</h4>
      <small>${esc(item.source||"manual")} · ${Number(item.word_count||0).toLocaleString("vi-VN")} từ</small>
      <div class="idea-card-meta">${used?`<span class="idea-pill used">Đã dùng ${item.used_at?`· ${esc(String(item.used_at).slice(0,10))}`:""}</span>`:""}<span class="idea-pill ${ideaStatusClass(item)}">${esc(item.recommendation||item.status||"Chưa phân tích")}</span>
      <span class="idea-pill idea-score">Hook ${item.hook_score||"–"} · Twist ${item.plot_twist_score||"–"}</span>
      ${item.primary_genre?`<span class="idea-pill">${esc(item.primary_genre)}</span>`:""}</div></div></article>`}).join("")||
      `<div class="idea-empty"><div><b>${IDEAS.items.length?"Không có mục trong bộ lọc này":"Kho chưa có nội dung"}</b>${IDEAS.items.length?
        (IDEAS.usageFilter==="used"?"Chưa có chuyện nào được ghi vào lịch sử sử dụng.":`Đổi bộ lọc hoặc bấm × để xem lại kho.`):"Sang tab Tìm & tải nội dung để lấy chuyện thật, hoặc nhập JSON/SQLite."}</div></div>`;
}
function ideaField(label,control,full){return `<div class="idea-field ${full?"full":""}"><label>${label}</label>${control}</div>`;}
function renderIdeaDetail(){
  const box=document.getElementById("ideaDetail"); if(!box) return;
  const x=IDEAS.detail;
  if(!x){box.innerHTML=`<div class="idea-empty"><div><b>Chọn một ý tưởng</b>Xem, chỉnh và duyệt kế hoạch trước khi sản xuất.</div></div>`;return;}
  const titles=(x.titles||[]).join("\n");
  const titleChoices=(x.titles||[]).map((title,index)=>
    `<option value="${index}">${index+1}. ${esc(title)}</option>`).join("");
  const descriptions=(x.descriptions||[]).join("\n---\n");
  const encodedId=encodeURIComponent(String(x.id||""));
  box.innerHTML=`<div class="idea-detail-head"><div><h3>${esc(x.title_localized||x.title_original)}</h3>
      <p>${esc(x.source||"manual")}${x.source_url?` · ${esc(x.source_url)}`:""}</p></div>
    <div class="idea-detail-actions"><button class="btn" onclick="ideasSave()">Lưu chỉnh sửa</button>
      <button class="btn" ${ideaWasUsed(x)?"disabled":""} onclick="ideasReloadSelected([decodeURIComponent('${encodedId}')])">↻ Tải lại</button>
      <button class="btn danger" ${ideaWasUsed(x)?"disabled":""} onclick="ideasDeleteSelected([decodeURIComponent('${encodedId}')])">🗑 Xóa</button>
      <button class="btn pri" ${ideaWasUsed(x)?"disabled":""} onclick="ideasUseStory()">${ideaWasUsed(x)?"✓ Đã dùng · khóa lặp":"▶ DÙNG ĐÚNG TRUYỆN NÀY → AI STORY"}</button></div></div>
    <div class="idea-use-target"><span>AI Story sẽ nhận:</span><b>${esc(x.title_localized||x.title_original)}</b><small>${esc(x.source||"manual")}</small></div>
    <div class="idea-metrics">
      <div class="idea-metric"><span>Hook</span><b class="accent">${x.hook_score||"–"}/10</b></div>
      <div class="idea-metric"><span>Plot Twist</span><b class="accent">${x.plot_twist_score||"–"}/10</b></div>
      <div class="idea-metric"><span>Ưu tiên</span><b>${x.priority_score||"–"}</b></div>
      <div class="idea-metric"><span>Thời lượng</span><b>${x.estimated_duration_minutes||"–"} phút</b></div>
      <div class="idea-metric"><span>Khuyến nghị</span><b>${esc(x.recommendation||"Chưa có")}</b></div>
    </div>
    <div class="idea-form">
      ${ideaField("Tiêu đề Việt / tên làm việc",`<input id="ideaTitleLocalized" value="${esc(x.title_localized||"")}">`)}
      ${ideaField("Tiêu đề YouTube sẽ đưa sang Perplexity",`<select id="ideaTitleChoice">${titleChoices||`<option value="0">Dùng tên làm việc</option>`}</select>`,true)}
      ${ideaField("Series",`<input id="ideaSeries" value="${esc(x.series||"")}">`)}
      ${ideaField("Thể loại chính",`<input id="ideaGenre" value="${esc(x.primary_genre||"")}">`)}
      ${ideaField("Cảm xúc chủ đạo",`<input id="ideaEmotion" value="${esc(x.emotion||"")}">`)}
      ${ideaField("Chủ đề (ngăn bằng dấu phẩy)",`<input id="ideaThemes" value="${esc((x.themes||[]).join(", "))}">`,true)}
      ${ideaField("Từ khóa",`<textarea id="ideaKeywords">${esc((x.keywords||[]).join(", "))}</textarea>`)}
      ${ideaField("Tags",`<textarea id="ideaTags">${esc((x.tags||[]).join(", "))}</textarea>`)}
      ${ideaField("8 tiêu đề AI đúng prompt — mỗi dòng một tiêu đề",`<textarea id="ideaTitles" class="tall">${esc(titles)}</textarea>`,true)}
      ${ideaField("3 mô tả — phân cách bằng dòng ---",`<textarea id="ideaDescriptions" class="tall">${esc(descriptions)}</textarea>`,true)}
      ${ideaField("Hook chính rút từ nguồn",`<textarea id="ideaMainHook">${esc(x.main_hook||"")}</textarea>`,true)}
      ${ideaField("Các cảnh kịch tính đáng khai thác — mỗi dòng một cảnh",`<textarea id="ideaTensionScenes" class="tall">${esc((x.high_tension_scenes||[]).join("\n"))}</textarea>`,true)}
      ${ideaField("Các plot twist đáng khai thác — mỗi dòng một cú lật",`<textarea id="ideaPlotTwists">${esc((x.plot_twists||[]).join("\n"))}</textarea>`,true)}
      ${ideaField("Hồ sơ sáng tác Việt gửi sang Perplexity — KHÔNG chứa toàn văn nguồn",`<textarea id="ideaRewriteBrief" class="tall">${esc(x.rewrite_brief||"")}</textarea>`,true)}
      ${ideaField("Outline 3 phần",`<textarea id="ideaOutline" class="tall">${esc(x.outline||"")}</textarea>`,true)}
      ${ideaField("Nhân vật chính",`<input id="ideaCharacters" value="${esc((x.main_characters||[]).join(", "))}">`)}
      ${ideaField("Thời điểm đăng",`<input id="ideaPublish" value="${esc(x.best_publish_time||"")}">`)}
      ${ideaField("Ghi chú biên tập",`<textarea id="ideaNotes">${esc(x.notes||"")}</textarea>`,true)}
      <div class="idea-field full"><label>Trích đoạn nguồn để tham khảo — không đọc/sao chép nguyên văn</label><div class="idea-source-preview">${esc(x.content||x.raw_content||x.content_preview||"")}</div></div>
    </div>`;
}
function renderIdeas(){
  renderIdeaStatus();
  const badge=document.getElementById("ideaLibraryBadge");if(badge)badge.textContent=String(IDEAS.items.length||0);
  const online=document.getElementById("ideaOnlineKeyword");if(online&&document.activeElement!==online)online.value=IDEAS.onlineKeyword||"";
  const group=document.getElementById("ideaSourceGroup");if(group)group.value=IDEAS.sourceGroup||"zhihu";
  const usage=document.getElementById("ideaUsageFilter");if(usage)usage.value=IDEAS.usageFilter||"unused";
  setIdeasTab(IDEAS.tab);
}
async function ideasSelect(id,renderNow=true){
  IDEAS.selectedId=id;
  if(renderNow) renderIdeaList();
  try{
    const r=await api("/api/content/item?id="+encodeURIComponent(id));
    IDEAS.detail=r.item||null; renderIdeaDetail();return IDEAS.detail;
  }catch(e){toast(e.message,"err");return null;}
}
async function ideasToggle(id,selected){
  const item=IDEAS.items.find(x=>x.id===id);if(item&&ideaWasUsed(item))return;
  if(item) item.selected=selected;
  renderIdeaList();
  try{
    await api("/api/content/select",{ids:[id],selected});
    // Checkbox trước đây chỉ phục vụ chọn hàng loạt nên khung chi tiết vẫn trỏ
    // vào truyện cũ. Khi tick, đồng thời mở đúng truyện để mọi thao tác một mục
    // (đặc biệt Dùng trong AI Story) có cùng một nguồn chuẩn.
    if(selected&&IDEAS.selectedId!==id) await ideasSelect(id);
  }
  catch(e){if(item)item.selected=!selected;renderIdeaList();toast(e.message,"err");}
}
async function ideasSetVisibleSelection(selected){
  const rows=selected?ideasFiltered():IDEAS.items.filter(x=>x.selected);
  const ids=rows.filter(x=>!ideaWasUsed(x)).map(x=>x.id);
  if(!ids.length)return toast(selected?"Không có mục chưa dùng trong bộ lọc này":"Hàng đợi đang trống","warn");
  const idSet=new Set(ids);
  IDEAS.items.forEach(x=>{if(idSet.has(x.id))x.selected=!!selected;});
  renderIdeaList();
  try{await api("/api/content/select",{ids,selected});}
  catch(e){await ideasLoad(true);toast(e.message,"err");}
}
async function ideasReloadSelected(inputIds){
  const ids=(Array.isArray(inputIds)&&inputIds.length?inputIds:ideaSelectedIds())
    .filter(id=>{const item=IDEAS.items.find(x=>x.id===id);return item&&!ideaWasUsed(item);});
  if(!ids.length)return toast("Hãy tick ít nhất một mục chưa dùng cần tải lại","warn");
  if(!confirm(`Tải lại nội dung nguồn và thay kết quả phân tích hiện tại của ${ids.length} mục?`))return;
  const provider=document.getElementById("ideaProvider")?.value||IDEAS.provider||"browser";
  IDEAS.provider=provider;
  try{
    const r=await api("/api/content/reload",{ids,provider,use_ai:provider!=="heuristic",
      cookie:ideaDownloadCookie(),concurrency:3});
    IDEAS.server={working:true,active:"content_reload",status:`Đang tải lại và phân tích ${r.total} mục…`,
      total:r.total,done:0,progress:0,protected_count:r.protected||0};
    _ideasLastActive="content_reload";renderIdeaStatus();
    if(provider!=="heuristic"&&!r.provider_configured)
      toast("Chưa mở được phiên Gemini/Perplexity đã đăng nhập; hàng đợi sẽ dùng quy tắc local.","warn");
  }catch(e){toast(e.message,"err");}
}
async function ideasDeleteSelected(inputIds){
  const ids=(Array.isArray(inputIds)&&inputIds.length?inputIds:ideaSelectedIds())
    .filter(id=>{const item=IDEAS.items.find(x=>x.id===id);return item&&!ideaWasUsed(item);});
  if(!ids.length)return toast("Hãy tick ít nhất một mục chưa dùng cần xóa","warn");
  if(!confirm(`Xóa ${ids.length} mục khỏi Kho đã tải & phân tích? Hành động này không xóa lịch sử truyện đã dùng.`))return;
  try{
    const r=await api("/api/content/delete",{ids});
    const deleted=new Set(r.deleted||[]);
    if(deleted.has(IDEAS.selectedId)){IDEAS.selectedId="";IDEAS.detail=null;}
    await ideasLoad(false);
    toast(r.status||`Đã xóa ${deleted.size} mục`,deleted.size?"ok":"warn");
  }catch(e){toast(e.message,"err");}
}
function ideasSearch(value){IDEAS.search=value;renderIdeaList();}
function ideasSetUsageFilter(value){
  IDEAS.usageFilter=["unused","used","all"].includes(value)?value:"unused";
  localStorage.setItem("advn_idea_usage_filter",IDEAS.usageFilter);renderIdeaList();
}
function ideasClearSearch(){
  IDEAS.search="";const input=document.getElementById("ideaSearch");if(input)input.value="";renderIdeaList();
}
async function ideasImport(){
  let path="";
  try{
    if(DESK()&&pywebview.api.pick_story_data) path=await pywebview.api.pick_story_data();
    else path=prompt("Đường dẫn file JSON/SQLite:","")||"";
    if(path&&path.error) throw new Error(path.error);
    if(!path) return;
    await api("/api/content/import",{path});
    IDEAS.server={working:true,status:"Đang nhập kho truyện…",progress:0};renderIdeaStatus();
  }catch(e){toast(e.message,"err");}
}
async function ideasSample(){
  try{await api("/api/content/sample",{});IDEAS.server={working:true,status:"Đang tạo dữ liệu mẫu…"};renderIdeaStatus();}
  catch(e){toast(e.message,"err");}
}
async function ideasAnalyze(all){
  const ids=all?[]:ideaSelectedIds();
  if(!all&&!ids.length) return toast("Hãy tick ít nhất một ý tưởng","warn");
  const provider=document.getElementById("ideaProvider")?.value||IDEAS.provider||"browser";
  IDEAS.provider=provider;
  try{
    const r=await api("/api/content/analyze",{ids,all,provider,use_ai:provider!=="heuristic",concurrency:3});
    IDEAS.server={working:true,active:"analyze",status:`Đang phân tích ${r.total} ý tưởng bằng ${provider}…`,
      total:r.total,done:0,progress:0,provider:r.provider,provider_model:r.provider_model,
      provider_configured:r.provider_configured,started_at:Date.now()/1000};renderIdeaStatus();
    if(provider!=="heuristic"&&!r.provider_configured)
      toast("Chưa mở được phiên Gemini/Perplexity đã đăng nhập; lượt này dùng quy tắc local.","warn");
  }catch(e){toast(e.message,"err");}
}
async function ideasSave(silent){
  const x=IDEAS.detail;if(!x)return;
  const descriptions=String(document.getElementById("ideaDescriptions")?.value||"").split(/\n\s*---\s*\n/).map(v=>v.trim()).filter(Boolean);
  const values={
    title_localized:document.getElementById("ideaTitleLocalized")?.value||"",
    series:document.getElementById("ideaSeries")?.value||"",
    primary_genre:document.getElementById("ideaGenre")?.value||"",
    emotion:document.getElementById("ideaEmotion")?.value||"",
    themes:ideaArray(document.getElementById("ideaThemes")?.value),
    keywords:ideaArray(document.getElementById("ideaKeywords")?.value),
    tags:ideaArray(document.getElementById("ideaTags")?.value),
    titles:String(document.getElementById("ideaTitles")?.value||"").split("\n").map(v=>v.trim()).filter(Boolean).slice(0,8),
    descriptions:descriptions.slice(0,3),outline:document.getElementById("ideaOutline")?.value||"",
    main_hook:document.getElementById("ideaMainHook")?.value||"",
    high_tension_scenes:ideaLines(document.getElementById("ideaTensionScenes")?.value),
    plot_twists:ideaLines(document.getElementById("ideaPlotTwists")?.value),
    rewrite_brief:document.getElementById("ideaRewriteBrief")?.value||"",
    main_characters:ideaArray(document.getElementById("ideaCharacters")?.value),
    best_publish_time:document.getElementById("ideaPublish")?.value||"",
    notes:document.getElementById("ideaNotes")?.value||"",
  };
  try{const r=await api("/api/content/update",{id:x.id,values});IDEAS.detail=r.item;await ideasLoad(true);if(!silent)toast("Đã lưu chỉnh sửa","ok");return IDEAS.detail;}
  catch(e){toast(e.message,"err");return null;}
}
async function ideasExport(all){
  const ids=all?[]:ideaSelectedIds();if(!all&&!ids.length)return toast("Hãy chọn ý tưởng cần xuất","warn");
  try{const r=await api("/api/content/export",{ids,all});IDEAS.server={working:true,status:`Đang tạo Excel cho ${r.total} ý tưởng…`,total:r.total,progress:0};renderIdeaStatus();}
  catch(e){toast(e.message,"err");}
}
async function ideasUseStory(){
  try{
    const selectedIds=ideaSelectedIds();
    const targetId=ideaStoryTargetId();
    if(!targetId){
      if(selectedIds.length>1)
        throw new Error("Bạn đang tick nhiều truyện. Hãy bấm mở đúng một truyện trong số đã tick rồi mới đưa sang AI Story.");
      throw new Error("Hãy chọn một truyện cần đưa sang AI Story.");
    }
    if(!IDEAS.detail||IDEAS.detail.id!==targetId){
      const loaded=await ideasSelect(targetId,false);
      if(!loaded||loaded.id!==targetId) throw new Error("Không nạp được đúng truyện đã tick.");
    }
    const targetTitle=String(IDEAS.detail.title_localized||IDEAS.detail.title_original||"").trim();
    const titleIndex=Math.max(0,Number(document.getElementById("ideaTitleChoice")?.value)||0);
    const saved=await ideasSave(true);
    if(!saved||saved.id!==targetId) throw new Error("Không lưu được đúng truyện đã chọn; đã dừng để tránh viết nhầm.");
    let current=saved;
    toast(`Đang chuyển đúng truyện: ${targetTitle}`,"ok");
    const provider=document.getElementById("ideaProvider")?.value||IDEAS.provider||"browser";
    const analysisProvider=String(current.analysis_provider||"").toLowerCase();
    const needsAi=String(current.language||"").toLowerCase()==="zh"&&
      (!current.title_localized||!current.rewrite_brief||
       /heuristic|offline|fallback/.test(analysisProvider));
    if(needsAi){
      if(provider==="heuristic") throw new Error("Truyện Trung cần Gemini Web hoặc Claude trên Perplexity để rút hook/plot twist và Việt hóa tiêu đề.");
      const started=await api("/api/content/analyze",{ids:[current.id],provider,use_ai:true,concurrency:1});
      IDEAS.server={working:true,active:"analyze",status:"Đang rút hook, plot twist và tạo 8 tiêu đề Việt…",total:1,done:0,progress:0};
      renderIdeaStatus();
      if(!started.provider_configured)
        toast("Không mở được phiên trình duyệt đã đăng nhập; lượt này sẽ dùng quy tắc local.","warn");
      const deadline=Date.now()+12*60*1000;
      while(Date.now()<deadline){
        await new Promise(resolve=>setTimeout(resolve,900));
        const state=await api("/api/state");
        const task=state.content_pipeline||{};
        IDEAS.server=task;renderIdeaStatus();
        if(!task.working){
          if(task.error) throw new Error(task.error);
          break;
        }
      }
      const fresh=await api("/api/content/item?id="+encodeURIComponent(current.id));
      current=fresh.item||current;IDEAS.detail=current;
      if(current.id!==targetId) throw new Error("Dữ liệu phân tích trả về sai truyện; đã dừng an toàn.");
      const freshProvider=String(current.analysis_provider||"").toLowerCase();
      if(!current.title_localized||!current.rewrite_brief||/heuristic|offline|fallback/.test(freshProvider))
        throw new Error(current.analysis_error||"Trình duyệt chưa tạo được tiêu đề Việt và hồ sơ sáng tác. Kiểm tra đăng nhập Gemini/Perplexity rồi thử lại.");
    }
    if(current.id!==targetId) throw new Error("Lựa chọn đã thay đổi trong lúc xử lý; đã dừng để tránh viết nhầm.");
    const r=await api("/api/content/use_story",{id:targetId,title_index:titleIndex,description_index:0});
    const story=r.story||{};MANUAL.writer_title=story.title||current.title_localized||current.title_original;
    if(story.id!==targetId) throw new Error("Máy chủ trả về sai truyện; đã dừng an toàn.");
    MANUAL.name=MANUAL.writer_title;MANUAL.youtube_description=story.description||"";
    MANUAL.youtube_tags=story.tags||[];MANUAL.content_outline=story.outline||"";
    MANUAL.rewrite_brief=story.rewrite_brief||"";
    MANUAL.content_idea_id=story.id||current.id;
    MANUAL.status="Đã nhận hồ sơ; đang mở Perplexity để viết mới toàn bộ.";
    setMode("story");renderStory();
    toast("Đã chuyển tiêu đề + hook/plot twist; bắt đầu quy trình Perplexity 4 bước","ok");
    await storyGenerateAndRun();
  }catch(e){toast(e.message,"err");}
}
