const test=require('node:test'), assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
function setup(running){
 const ctx=vm.createContext({ST:{running,busy:''}});
 vm.runInContext(fs.readFileSync(path.join(__dirname,'../ui/js/dub-panel.js'),'utf8'),ctx);
 vm.runInContext(`_captionReview[9]={ok:true,gaps:[{start:14054.1,end:14054.6,reason:'missing_speech_marks'}]}`,ctx);
 return vm.runInContext(`captionReviewHtml({id:9,result_status:'REVIEW_REQUIRED'})`,ctx);
}
test('short review range retains distinct millisecond timestamps',()=>{
 const html=setup(false);
 assert.ok(html.includes('03:54:14.100 → 03:54:14.600'));
 assert.ok(!html.includes('disabled'));
});
test('recognition and acknowledgement controls are disabled while a job runs',()=>{
 const html=setup(true);
 assert.equal((html.match(/disabled/g)||[]).length,3);
});
test('late review response cannot overwrite the new retry result',async()=>{
 let resolve;
 const ctx=vm.createContext({
   ST:{queue:[{id:9,review_dir:'old',result_status:'REVIEW_REQUIRED'}]},
   TAB:'tts',JID:9,api:()=>new Promise(r=>{resolve=r})
 });
 vm.runInContext(fs.readFileSync(path.join(__dirname,'../ui/js/dub-panel.js'),'utf8'),ctx);
 const pending=vm.runInContext('loadCaptionReview(9)',ctx);
 ctx.ST.queue[0].review_dir='new';
 resolve({review_dir:'old',gaps:[{start:10,end:12}]});
 await pending;
 assert.equal(vm.runInContext('_captionReview[9]',ctx),undefined);
});
test('server remaining list takes priority over locally matching acknowledgement',()=>{
 const ctx=vm.createContext({});
 vm.runInContext(fs.readFileSync(path.join(__dirname,'../ui/js/dub-panel.js'),'utf8'),ctx);
 assert.equal(vm.runInContext(`gapIsAcked({start:10,end:12},[{start:10,end:12}],
   [{start:10,end:12,reason:'invalid_clock'}])`,ctx),false);
 assert.equal(vm.runInContext(`gapIsAcked({start:10,end:12},[{start:10,end:12}],[])`,ctx),true);
});

test('ack invalidates a late response for the same review directory',async()=>{
 let resolve;
 const ctx=vm.createContext({ST:{queue:[{id:9,review_dir:'same',result_status:'REVIEW_REQUIRED'}]},
   TAB:'tts',JID:9,api:()=>new Promise(r=>{resolve=r})});
 vm.runInContext(fs.readFileSync(path.join(__dirname,'../ui/js/dub-panel.js'),'utf8'),ctx);
 const pending=vm.runInContext('loadCaptionReview(9)',ctx);
 vm.runInContext('invalidateCaptionReview(9)',ctx);
 resolve({remaining:[{start:10,end:11}]});
 await pending;
 assert.equal(vm.runInContext('_captionReview[9]',ctx),undefined);
});

test('effective per-issue state distinguishes overlapping diagnostic reasons',()=>{
 const ctx=vm.createContext({});
 vm.runInContext(fs.readFileSync(path.join(__dirname,'../ui/js/dub-panel.js'),'utf8'),ctx);
 assert.equal(vm.runInContext('gapIsAcked({blocking:false},[],[{blocking:true}])',ctx),true);
 assert.equal(vm.runInContext('gapIsAcked({blocking:true},[],[])',ctx),false);
});
