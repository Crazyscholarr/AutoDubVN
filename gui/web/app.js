/* ===================================================================
   AutoDubVN desktop - logic giao diện. Gọi Python qua window.pywebview.api.*
   và nhận sự kiện đẩy ngược từ Python qua các hàm window.onLog/onState/...
   =================================================================== */
"use strict";

const $ = (sel) => document.querySelector(sel);

const el = {
  video: $("#videoInput"),
  browse: $("#browseBtn"),
  start: $("#startBtn"),
  stop: $("#stopBtn"),
  save: $("#saveBtn"),
  saveHint: $("#saveHint"),
  clear: $("#clearBtn"),
  console: $("#console"),
  statusDot: $("#statusDot"),
  statusText: $("#statusText"),
  resultBar: $("#resultBar"),
  resultPath: $("#resultPath"),
  play: $("#playBtn"),
  folder: $("#folderBtn"),
};

let lastResultPath = "";

/* ---------- chờ pywebview sẵn sàng ---------- */
function whenReady(cb) {
  if (window.pywebview && window.pywebview.api) return cb();
  window.addEventListener("pywebviewready", cb, { once: true });
}

/* ---------- log ---------- */
function classify(line) {
  if (/\[x\]|\bLỗi\b|CẢNH BÁO|Traceback|Error/i.test(line)) return "l-err";
  if (/\[!\]|thiếu|cảnh báo/i.test(line)) return "l-warn";
  if (/\[✓\]|\[i\].*(xong|Đã)|XONG|Hoàn tất|Đã lưu|Đã tải/i.test(line)) return "l-ok";
  if (/^==>|\bBước\b/i.test(line)) return "l-step";
  if (/^\[i\]/.test(line)) return "l-info";
  return "";
}
function appendLog(line) {
  const div = document.createElement("div");
  const cls = classify(line);
  if (cls) div.className = cls;
  div.textContent = line;
  el.console.appendChild(div);
  el.console.scrollTop = el.console.scrollHeight;
}

/* ---------- trạng thái ---------- */
function setRunning(running) {
  el.start.disabled = running;
  el.stop.disabled = !running;
  el.browse.disabled = running;
  el.statusDot.className = "status-dot " + (running ? "run" : "idle");
  el.statusText.textContent = running ? "Đang chạy…" : "Sẵn sàng";
}

/* ---------- sự kiện Python -> JS ---------- */
window.onLog = (p) => appendLog(p.line);
window.onState = (p) => setRunning(!!p.running);
window.onResult = (p) => { lastResultPath = p.path || ""; };
window.onDone = (p) => {
  setRunning(false);
  if (p.code === 0) {
    el.statusDot.className = "status-dot done";
    el.statusText.textContent = "Hoàn tất";
    if (lastResultPath) {
      el.resultPath.textContent = lastResultPath;
      el.resultBar.classList.remove("hidden");
    }
  } else {
    el.statusDot.className = "status-dot err";
    el.statusText.textContent = "Có lỗi (mã " + p.code + ")";
  }
};

/* ---------- config <-> form ---------- */
const KEYS = () => Array.from(document.querySelectorAll("[data-key]"));

function toStr(v) { return v === null || v === undefined ? "" : String(v); }

function fillForm(cfg) {
  KEYS().forEach((node) => {
    const path = node.getAttribute("data-key").split(".");
    let v = cfg;
    for (const p of path) { v = (v == null ? undefined : v[p]); }
    if (node.type === "checkbox") node.checked = !!v;
    else node.value = toStr(v);
  });
}

function collectForm() {
  const out = {};
  KEYS().forEach((node) => {
    const key = node.getAttribute("data-key");
    out[key] = node.type === "checkbox" ? node.checked : node.value;
  });
  return out;
}

/* ---------- gắn nút ---------- */
function bind() {
  el.browse.addEventListener("click", async () => {
    const r = await window.pywebview.api.browse_video();
    if (r && r.ok && r.path) el.video.value = r.path;
  });

  el.start.addEventListener("click", async () => {
    el.resultBar.classList.add("hidden");
    lastResultPath = "";
    const r = await window.pywebview.api.start(el.video.value);
    if (!r || !r.ok) appendLog("[x] " + (r ? r.error : "Không khởi chạy được."));
  });

  el.stop.addEventListener("click", async () => {
    await window.pywebview.api.stop();
  });

  el.save.addEventListener("click", async () => {
    el.saveHint.textContent = "Đang lưu…";
    el.saveHint.className = "hint";
    const r = await window.pywebview.api.save_config(collectForm());
    if (r && r.ok) {
      el.saveHint.textContent = "✓ Đã lưu vào config.yaml";
      el.saveHint.className = "hint ok";
    } else {
      el.saveHint.textContent = "✗ " + (r ? r.error : "Lỗi lưu");
      el.saveHint.className = "hint err";
    }
    setTimeout(() => (el.saveHint.textContent = ""), 4000);
  });

  el.clear.addEventListener("click", () => (el.console.innerHTML = ""));
  el.play.addEventListener("click", () => lastResultPath && window.pywebview.api.open_file(lastResultPath));
  el.folder.addEventListener("click", () => lastResultPath && window.pywebview.api.open_folder(lastResultPath));

  el.video.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !el.start.disabled) el.start.click();
  });
}

/* ---------- khởi động ---------- */
whenReady(async () => {
  bind();
  try {
    const r = await window.pywebview.api.get_config();
    if (r && r.config) fillForm(r.config);
    if (r && !r.ok) appendLog("[!] Không đọc được config.yaml: " + r.error);
  } catch (e) { appendLog("[x] " + e); }
  appendLog("[i] Sẵn sàng. Nhập video/link rồi bấm ▶ Bắt đầu.");
});
