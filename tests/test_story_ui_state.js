"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

function element() {
  return {
    style: {}, dataset: {}, className: "", innerHTML: "", textContent: "",
    value: "", duration: 0, currentTime: 0, paused: true,
    classList: {add() {}, remove() {}, toggle() {}, contains() { return false; }},
    addEventListener() {}, setAttribute() {}, appendChild() {}, remove() {},
    querySelector() { return element(); }, querySelectorAll() { return []; },
    play() {}, pause() {},
  };
}

const elements = new Map();
const getElement = id => {
  if (!elements.has(id)) elements.set(id, element());
  return elements.get(id);
};
const storage = new Map();
const context = vm.createContext({
  console,
  setTimeout,
  clearTimeout,
  setInterval() { return 0; },
  clearInterval() {},
  URL,
  URLSearchParams,
  Audio: function Audio() { return element(); },
  fetch: async () => ({json: async () => ({})}),
  navigator: {clipboard: {writeText: async () => {}}},
  localStorage: {
    getItem: key => storage.has(key) ? storage.get(key) : null,
    setItem: (key, value) => storage.set(key, String(value)),
    removeItem: key => storage.delete(key),
  },
  window: {
    addEventListener() {}, open() {}, devicePixelRatio: 1,
  },
  document: {
    body: element(),
    getElementById: getElement,
    querySelector: () => element(),
    querySelectorAll: () => [],
    createElement: () => element(),
    addEventListener() {},
    execCommand() { return true; },
  },
});
context.window.document = context.document;
context.window.localStorage = context.localStorage;

const appPath = path.join(__dirname, "..", "ui", "app.js");
const source = fs.readFileSync(appPath, "utf8").replace(/\ninit\(\);\s*$/, "\n");
vm.runInContext(source, context, {filename: appPath});

async function run() {
  vm.runInContext(`
    MODE="story";
    MANUAL.writer_title="Truyện cũ";
    MANUAL.script_title="Truyện cũ";
    MANUAL.script_path="C:/old/KICH_BAN_DOC.txt";
    MANUAL.image_ready=3; MANUAL.image_total=14;
    STORY.pack="C:/old/manifest.json";
    STORY.packTitle="Truyện cũ";
    STORY.imgs=["C:/old/scene_001.png"];
    localStorage.setItem("advn_story_image_pack",STORY.pack);
    storyWriterTitleInput("Truyện hoàn toàn mới");
  `, context);

  const detached = vm.runInContext(`({
    pack:STORY.pack, packTitle:STORY.packTitle, images:STORY.imgs.length,
    ready:MANUAL.image_ready, total:MANUAL.image_total,
    scriptPath:MANUAL.script_path, canResume:storyCanResumeImages(),
    stored:localStorage.getItem("advn_story_image_pack")
  })`, context);
  assert.deepEqual(JSON.parse(JSON.stringify(detached)), {
    pack: "", packTitle: "", images: 0, ready: 0, total: 0,
    scriptPath: "", canResume: false, stored: null,
  });

  vm.runInContext(`
    let __openedOldPrompt=false;
    showStoryPrompt=async()=>{ __openedOldPrompt=true; };
    _manualRev=-1;
    MANUAL.writer_title="Truyện hoàn toàn mới";
    MANUAL.script_title="Truyện cũ";
    api=async(path,body)=>{
      if(path==="/api/state") return {
        queue:[],selected:null,running:false,busy:"",nvenc:false,rev:0,
        progress:{pct:0},log:[],manual:{
          rev:10,working:false,status:"Đang chờ ảnh",
          script_title:"Truyện cũ",script_path:"C:/old/KICH_BAN_DOC.txt",
          image_pack_path:"C:/old/manifest.json",image_scene_count:14,
          image_ready_count:3,image_prompt_ready:true,
          image_generation_status:"Prompt cũ sẵn sàng"
        }
      };
      if(path==="/api/story/image_pack") return {
        manifest_path:"C:/old/manifest.json",title:"Truyện cũ",
        images:["C:/old/scene_001.png"],prompt_text:"PROMPT CŨ"
      };
      return {};
    };
  `, context);
  await vm.runInContext("refresh()", context);
  const race = vm.runInContext(`({
    title:MANUAL.writer_title, pack:STORY.pack,
    opened:__openedOldPrompt, prompt:_storyPromptData.text,
    ready:MANUAL.image_ready, total:MANUAL.image_total,
    imageStatus:MANUAL.image_status
  })`, context);
  assert.equal(race.title, "Truyện hoàn toàn mới");
  assert.equal(race.pack, "");
  assert.equal(race.opened, false);
  assert.equal(race.prompt, "");
  assert.equal(race.ready, 0);
  assert.equal(race.total, 0);
  assert.equal(race.imageStatus, "");

  console.log("story UI state isolation: ok");
}

run().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
