const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict'),{JSDOM,VirtualConsole}=require('jsdom');
const root=path.resolve(__dirname,'..'),errors=[],vc=new VirtualConsole();vc.on('jsdomError',e=>errors.push(e.message));
const dom=new JSDOM(fs.readFileSync(path.join(root,'static/index.html'),'utf8'),{url:'http://forma.test',runScripts:'outside-only',virtualConsole:vc}),w=dom.window,$=s=>w.document.querySelector(s);
w.matchMedia=()=>({matches:true,addEventListener(){}});w.HTMLDialogElement.prototype.showModal=function(){this.open=true;};w.HTMLDialogElement.prototype.close=function(){this.open=false;this.dispatchEvent(new w.Event('close'));};
const originalTimer=w.setTimeout.bind(w);w.setTimeout=(fn,ms,...args)=>originalTimer(fn,ms===1500?1:ms,...args);
let state='idle',polls=0;
let model={id:'a',title:'Tray',creator:'My creator',source_url:'https://example.com/model',description:'My notes',tags:[],collections:[],collection:'',status:'Saved',favorite:false,cover_id:'',created_at:'2026-11-03',license:'',license_url:'',attribution:'',files:[]};
const response=value=>({ok:true,json:async()=>value});
w.fetch=async(url,opt={})=>{
 if(url==='/api/models')return response([model]);if(url==='/api/watch')return response({pending:0});if(url==='/api/saved-views'||url==='/api/printers'||url==='/api/models/a/prints')return response([]);if(url==='/api/models/a/details')return response({files:[]});
 if(url==='/api/models/a/source-import'){
  if(opt.method==='POST'){state='running';polls=0;return response({queued:true});}
  if(state==='idle')return response({job:null,photos:[],model});
  if(polls++===0)return response({model,photos:[],job:{state:'running',phase:'Fetching photos',done:0,total:2,filled:[],notes:[]}});
  state='partial';model={...model,license:'Creator terms',attribution:'<b>Credit</b>',cover_id:'photo1',files:[{id:'photo1',name:'Source photo.png',kind:'image',size:100}]};
  return response({model,photos:[{state:'imported',file_id:'photo1'},{state:'failed',error:'Requires sign-in'}],job:{state:'partial',phase:'Finished',done:2,total:2,filled:['license','attribution'],notes:['One photo needs attention.']}});
 }
 throw Error('Unexpected request '+url);
};
const tick=()=>new Promise(r=>setTimeout(r,10));
(async()=>{
 w.eval(['app.js','catalog.js','source.js'].map(n=>fs.readFileSync(path.join(root,'static',n),'utf8')).join('\n'));await tick();$('[data-open="a"]').click();await tick();assert.match($('#source-import-panel').textContent,/Not supplied/);
 $('#retry-source-import').click();await tick();await tick();await tick();
 assert.match($('#source-import-panel').textContent,/Finished with notes/);assert.match($('#source-import-panel').textContent,/Creator terms/);assert.match($('#source-import-panel').textContent,/<b>Credit<\/b>/);assert.equal($('#source-import-panel b'),null);
 assert.equal(w.document.querySelectorAll('.detail-gallery img').length,1);assert.equal($('#retry-source-import').disabled,false);assert.match($('#source-import-panel').textContent,/Requires sign-in/);assert.equal(model.creator,'My creator');assert.equal(model.description,'My notes');
 assert.deepEqual(errors,[]);console.log('PASS: background progress, partial-photo errors, retry availability, gallery refresh and escaped attribution.');dom.window.close();
})().catch(e=>{console.error(e);dom.window.close();process.exitCode=1;});
