// Offline probes executing the real UI functions in isolated VM contexts.
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '..');
const queue = fs.readFileSync(path.join(root, 'ui/js/dub-queue.js'), 'utf8');
const core = fs.readFileSync(path.join(root, 'ui/js/core.js'), 'utf8');
const selectCode = queue.slice(queue.indexOf('async function selectJob('), queue.indexOf('async function saveProject('));
const pickCode = queue.slice(queue.indexOf('async function pickFile('), queue.indexOf('async function addUrl('));
const nvidiaCode = core.slice(core.indexOf('async function setNvidiaEnabled('), core.indexOf('async function testNvidiaCfg('));

async function main() {
  const result = [];
  const pending = new Map();
  const video = {};
  const context = vm.createContext({
    JID: null, PR: null, ST: {queue: []},
    api: async (url) => url.startsWith('/api/project') ? new Promise(resolve => pending.set(url, resolve)) : {},
    V: () => video,
    document: {getElementById: () => ({style: {}})},
    renderPanel() {}, draw() {}, loadVoices() {}, saveNow() {},
    applyZoom() {}, fmt() {}, refresh() {},
  });
  vm.runInContext(selectCode, context);
  const first = vm.runInContext('selectJob(1)', context);
  await new Promise(resolve => setImmediate(resolve));
  const second = vm.runInContext('selectJob(2)', context);
  await new Promise(resolve => setImmediate(resolve));
  pending.get('/api/project?id=2')({id: 2});
  await second;
  pending.get('/api/project?id=1')({id: 1});
  await first;
  assert.equal(context.JID, 2);
  assert.equal(context.PR.id, 1);
  result.push({id: 'B01', reproduced: true, evidence: {selectedJob: context.JID, displayedProject: context.PR.id, video: video.src}});

  const messages = [];
  const pickContext = vm.createContext({
    DESK: () => true, pywebview: {api: {pick_video: async () => ['one.mp4', 'bad.mp4']}},
    api: async (url, payload) => {if(payload.path === 'bad.mp4') throw Error('bad file'); return {id: 1};},
    selectJob: async () => {}, refresh() {}, toast: (message, kind) => messages.push({message, kind}),
  });
  vm.runInContext(pickCode, pickContext);
  await vm.runInContext('pickFile()', pickContext);
  assert.ok(messages.some(x => x.message.includes('2 video') && x.kind === 'ok'));
  result.push({id: 'B02', reproduced: true, evidence: messages});

  const notices = [];
  const settings = vm.createContext({
    CFG: {translation: {}}, TAB: 'asr',
    applyProviderSideEffects() {}, saveTrCfg: async () => false,
    document: {getElementById: () => null},
    toast: (message, kind) => notices.push({message, kind}),
  });
  vm.runInContext(nvidiaCode, settings);
  await vm.runInContext('setNvidiaEnabled(true)', settings);
  assert.ok(notices.some(x => x.kind === 'ok'));
  result.push({id: 'B03', reproduced: true, evidence: {saveReturned: false, notices}});
  fs.writeFileSync(path.join(root, '_tmp/audit_ui_reproductions_20260920.json'), JSON.stringify(result, null, 2));
  console.log(JSON.stringify(result, null, 2));
}
main().catch(error => {console.error(error); process.exitCode = 1;});
