(()=>{
 'use strict';
 const FORMA=__FORMA_ORIGIN__;
 if(!['makerworld.com','www.makerworld.com'].includes(location.hostname)){alert('Open your MakerWorld collections or a model page, then click this bookmark.');return;}
 const prior=document.getElementById('forma-makerworld-capture');if(prior){prior._formaCapture?.();return;}
 const host=document.createElement('div');host.id='forma-makerworld-capture';
 Object.assign(host.style,{position:'fixed',right:'20px',top:'70px',width:'410px',maxWidth:'calc(100vw - 25px)',maxHeight:'82vh',zIndex:'2147483647',background:'#151820',color:'#edf0f7',borderRadius:'13px',boxShadow:'0 15px 70px #0007',overflow:'auto',border:'1px solid #3b4152'});
 const shadow=host.attachShadow({mode:'open'});
 const E=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 shadow.innerHTML=`<style>:host{font:12px -apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;color:#edf0f7}*{box-sizing:border-box}.body{padding:18px}header{display:flex;align-items:center;justify-content:space-between}h2{font-size:21px;letter-spacing:-.6px;margin:0}button{cursor:pointer;font:inherit;color:#edf0f7;background:#252b39;border:1px solid #424b5f;border-radius:7px;padding:9px 11px}button.primary{background:#a3adff;color:#141929;border-color:#a3adff}button:disabled{opacity:.5;cursor:wait}p{font-size:11px;line-height:1.7;color:#acb4c7;margin:12px 0}.row{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin:11px 0}label{display:flex;align-items:center;gap:7px;font-size:11px;line-height:1.6}.lists{max-height:155px;overflow:auto}.item{border-bottom:1px solid #303747;padding:9px 0;display:flex;gap:8px}.item span{flex:1;min-width:0;overflow-wrap:anywhere}.models{max-height:240px;overflow:auto}.model{display:flex;gap:8px;padding:9px 0;border-bottom:1px solid #303747}.model div{flex:1;min-width:0}.model strong{display:block;font-size:11px;overflow-wrap:anywhere}.model small{display:block;color:#a3aec4;font-size:9px;margin-top:4px;line-height:1.5}input{accent-color:#a3adff}#status{white-space:pre-wrap;color:#e9bf9c;font-size:11px;line-height:1.7;margin-top:12px}small{color:#9ca8c0}a{color:#a3adff}#close{border:0;background:none;font-size:22px;padding:0 3px}</style><div class="body"><header><h2>forma. <small>MakerWorld import</small></h2><button id="close" aria-label="Close">×</button></header><p>Your session stays on MakerWorld. Select collections or capture models already loaded on this page, then review them in Forma.</p><div class="row"><button id="lists">Load my collections</button><button id="capture">Capture loaded models</button></div><div id="collections" class="lists"></div><div class="row"><button id="read" class="primary">Read selected collections</button></div><div class="row"><label><input id="all" type="checkbox" checked>Select all</label><small id="count">0 models</small></div><div id="models" class="models"></div><div class="row"><label><input id="files" type="checkbox">Include default 3MF profiles</label></div><p>File transfer uses normal MakerWorld downloads, up to 256 MB total per send. If direct transfer is blocked, JSON export preserves the catalog and collection names; attach downloaded files separately in Forma.</p><div class="row"><button id="send" class="primary">Send selected to Forma</button><button id="export">Export selected JSON</button></div><div id="status" role="status"></div><p><a href="${E(FORMA)}" target="_blank" rel="noopener">Open your Forma library ↗</a></p></div>`;
 document.body.append(host);
 const $=s=>shadow.querySelector(s),$$=s=>[...shadow.querySelectorAll(s)];
 let models=new Map(),selected=new Set(),collections=[],warnings=[],busy=false;
 const notice=s=>$('#status').textContent=s;
 const current=location.pathname.match(/\/collections\/(\d+)/)?.[1];
 const modelPage=location.pathname.match(/\/models\/(\d+)/)?.[1];
 const partialCollections=new Set();
 const title=()=>document.querySelector('h1')?.textContent?.trim()||document.title.replace(/\s*[-|]\s*MakerWorld.*$/i,'').trim();
 const source=()=>({id:current||'liked',name:current?title():'MakerWorld · Captured models'});
 function normalize(raw,collection){
   const d=raw.design||raw;const id=String(d.id??d.designId??'');if(!/^\d{1,20}$/.test(id))return null;
   const creator=d.designCreator||d.creator||{},instances=Array.isArray(d.instances)?d.instances:[];
   const profile=instances.find(i=>String(i.id)===String(d.defaultInstanceId))||instances.find(i=>i.isDefault)||instances[0];
   let image=d.coverUrl||d.coverLandscape||d.cover||d.images?.[0]?.url||d.images?.[0]||'';
   if(typeof image!=='string'||!/^https:\/\/([^/]+\.)?(bblmw\.com|bambulab\.com|makerworld\.com)\//i.test(image))image='';
   return {id,title:String(d.title||d.name||'MakerWorld '+id).slice(0,300),creator:String(creator.name||creator.handle||''),description:String(d.description||d.summary||'').replace(/<[^>]*>/g,' ').slice(0,30000),tags:(Array.isArray(d.tags)?d.tags:[]).map(t=>typeof t==='string'?t:t?.name||'').filter(Boolean).slice(0,100),image_url:image,image_urls:[...(Array.isArray(d.images)?d.images:[])].map(i=>typeof i==='string'?i:i?.url||i?.src||'').filter(Boolean).slice(0,20),license:typeof d.license==='string'?d.license:d.license?.name||d.licenseName||'',license_url:d.license?.url||d.licenseUrl||'',attribution:d.creditText||d.attribution||'',profile_id:/^\d+$/.test(String(profile?.id||d.defaultInstanceId||''))?String(profile?.id||d.defaultInstanceId):'',favorite:false,collections:collection?[{id:String(collection.id),name:String(collection.name).slice(0,100),source_url:/^\d+$/.test(String(collection.id))?'https://makerworld.com/en/collections/'+collection.id:''}]:[]};
 }
 function add(item){
   if(!item)return;
   const old=models.get(item.id);if(old)item.collections=[...new Map([...old.collections,...item.collections].map(c=>[c.id,c])).values()];
   if(models.size>=500&&!old)throw Error('Capture at most 500 distinct models per batch.');
   models.set(item.id,item);selected.add(item.id);
 }
 function render(){
   $('#count').textContent=selected.size+' selected / '+models.size;
   $('#models').innerHTML=[...models.values()].map(m=>`<label class="model"><input class="model-check" type="checkbox" value="${m.id}" ${selected.has(m.id)?'checked':''}><div><strong>${E(m.title)}</strong><small>${E(m.collections.map(c=>c.name).join(' · '))}</small></div></label>`).join('');
 }
 function discover(){
   const found=new Map(collections.map(c=>[c.id,c]));
   for(const a of document.querySelectorAll('a[href*="/collections/"]')){
     const id=a.href.match(/\/collections\/(\d+)/)?.[1];if(!id)continue;
     const name=a.querySelector('h2,h3,h4')?.textContent?.trim()||a.textContent.trim().split('\n').find(t=>t.trim())||'Collection '+id;
     if(!found.has(id))found.set(id,{id,name:name.slice(0,100)});
   }
   if(current&&!found.has(current))found.set(current,{id:current,name:title()});
   collections=[...found.values()];
   $('#collections').innerHTML=collections.map(c=>`<label class="item"><input class="collection-check" type="checkbox" value="${c.id}" ${c.id===current?'checked':''}><span>${E(c.name)}</span></label>`).join('');
 }
 function capture(){
   let found=0;
   for(const a of document.querySelectorAll('a[href*="/models/"]')){
     const url=new URL(a.href,location.href);if(!['makerworld.com','www.makerworld.com'].includes(url.hostname))continue;
     const id=url.pathname.match(/\/models\/(\d+)/)?.[1];if(!id||modelPage&&id!==modelPage)continue;
     const card=a.closest('article,[data-testid*="model"],[class*="ModelCard"],[class*="design-card"]')||a.parentElement;
     const img=a.querySelector('img')||card?.querySelector('img');
     const name=a.querySelector('h2,h3,h4')?.textContent?.trim()||card?.querySelector('h2,h3,h4')?.textContent?.trim()||img?.alt?.trim()||a.textContent.trim().split('\n').find(s=>s.trim())||'MakerWorld '+id;
     add(normalize({id,title:name,coverUrl:img?.currentSrc||img?.src||''},source()));found++;
   }
   const next=document.querySelector('script#__NEXT_DATA__');
   if(next){try{const props=JSON.parse(next.textContent).props?.pageProps;const design=props?.design;if(design){add(normalize(design,current?source():null));found++;}}catch{}}
   partialCollections.add(source().id);
   if(!warnings.includes('Captured loaded page content: this may be a partial collection.'))warnings.push('Captured loaded page content: this may be a partial collection.');
   render();notice(found?'Captured loaded models. Scroll MakerWorld to load more, then click Capture again. Only loaded page content is included unless Read selected collections succeeds.':'No loaded model cards found. Open one of your collection pages, scroll to load its models, then click Capture.');
 }
 async function api(path){
   const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),30000);
   try{
     const r=await fetch('/api/v1'+path,{credentials:'include',signal:controller.signal});
     if([401,403].includes(r.status))throw Error('MakerWorld did not authorize its web API. Open your collection normally and use Capture loaded models instead. Complete any site verification on MakerWorld.');
     if(r.status===429)throw Error('MakerWorld asked us to slow down. Wait before trying again.');
     if(!r.ok)throw Error('MakerWorld returned HTTP '+r.status+'.');return r.json();
   }finally{clearTimeout(timer);}
 }
 $('#lists').onclick=async()=>{try{notice('Reading your collections…');const data=await api('/design-service/my/favorites/listlite?query=');if(!Array.isArray(data.hits))throw Error('The collection list format changed. Open a collection and capture its loaded models instead.');collections=data.hits.filter(c=>/^\d+$/.test(String(c.id))).map(c=>({id:String(c.id),name:String(c.title||'Collection '+c.id)}));discover();notice(collections.length+' collections found. Choose collections and click Read selected collections.');}catch(e){discover();notice(e.message);}};
 $('#read').onclick=async()=>{
   if(busy)return;const ids=new Set($$('.collection-check:checked').map(i=>i.value));if(!ids.size){notice('Select a collection first, or capture this page’s loaded models.');return;}
   busy=true;$('#read').disabled=true;
   try{
     for(const c of collections.filter(c=>ids.has(c.id))){
       let seen=new Set();
       for(let offset=0;offset<500;offset+=20){
         notice('Reading '+c.name+' · '+offset+' models…');
         const data=await api('/design-service/favorites/'+c.id+'/designs?seed=0&collectionId='+c.id+'&limit=20&offset='+offset);
         if(!Array.isArray(data.hits)&&!Array.isArray(data.items))throw Error('MakerWorld changed its collection response. Capture the loaded page as a fallback.');const hits=Array.isArray(data.hits)?data.hits:data.items;if(!hits.length)break;
         let fresh=0;for(const raw of hits){const m=normalize(raw,c);if(m&&!seen.has(m.id)){seen.add(m.id);add(m);fresh++;}}
         if(!fresh)throw Error('MakerWorld repeated a page. Stopped to avoid an incomplete import.');
         if(hits.length<20||(Number.isFinite(Number(data.total))&&offset+hits.length>=Number(data.total)))break;
         if(offset+20>=500)throw Error('This collection exceeds 500 models. Import a smaller collection first.');
         await new Promise(resolve=>setTimeout(resolve,500));
       }
       partialCollections.delete(c.id);
     }
     if(!partialCollections.size)warnings=warnings.filter(w=>!w.startsWith('Captured loaded page'));
     render();notice('Collections loaded. Review the selected models below.');
   }catch(e){render();warnings.push('A collection API read did not finish; the captured list may be incomplete.');notice(e.message);}finally{busy=false;$('#read').disabled=false;}
 };
 function payload(){return {format:'forma-makerworld-v1',title:current?title():'MakerWorld · Safari import',items:[...models.values()].filter(m=>selected.has(m.id)),warnings:[...new Set(warnings)]};}
 $('#export').onclick=()=>{
   const data=payload();if(!data.items.length){notice('Capture or read some models first.');return;}
   const blob=new Blob([JSON.stringify(data,null,2)],{type:'application/json'}),url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download='forma-makerworld.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),30000);notice('Catalog exported. In Forma → MakerWorld import, choose Import Safari JSON. This export contains catalog details; no model files.');
 };
 async function fileFor(item){
   let profile=item.profile_id;
   if(!profile){const detail=await api('/design-service/design/'+item.id);profile=normalize(detail)?.profile_id;}
   if(!/^\d+$/.test(profile||''))throw Error('No default print profile was returned.');
   let r=await fetch('/api/v1/design-service/instance/'+profile+'/f3mf?type=download',{credentials:'include'});
   if(!r.ok)throw Error('MakerWorld download returned HTTP '+r.status+'.');
   if((r.headers.get('Content-Type')||'').includes('json')){
     const data=await r.json(),url=new URL(data.url||data.data?.url||data.link);
     if(url.protocol!=='https:'||url.username||url.password||!['bblmw.com','bambulab.com','makerworld.com'].some(h=>url.hostname===h||url.hostname.endsWith('.'+h)))throw Error('Unexpected download host.');
     r=await fetch(url.href,{credentials:'omit'});if(!r.ok)throw Error('File download returned HTTP '+r.status+'.');
   }
   const max=256*1024*1024;if(Number(r.headers.get('Content-Length')||0)>max)throw Error('File exceeds 256 MB.');
   const reader=r.body.getReader(),chunks=[];let size=0;
   while(true){const v=await reader.read();if(v.done)break;size+=v.value.length;if(size>max){await reader.cancel();throw Error('File exceeds 256 MB.');}chunks.push(v.value);}
   const blob=new Blob(chunks,{type:'application/octet-stream'}),prefix=new Uint8Array(await blob.slice(0,4).arrayBuffer());if(prefix[0]!==80||prefix[1]!==75)throw Error('MakerWorld did not return a 3MF archive.');return blob;
 }
 $('#send').onclick=async()=>{
   if(busy)return;const data=payload();if(!data.items.length){notice('Capture or read some models first.');return;}
   const nonce=crypto.randomUUID(),popup=window.open(FORMA+'/?makerworldBridge='+nonce,'_blank');
   if(!popup){notice('Safari blocked the Forma tab. Use Export selected JSON, then import that file in Forma.');return;}
   busy=true;$('#send').disabled=true;let ready=false,sent=false,finished=false,abandoned=false,filesReady=!$('#files').checked,files=[];
   const send=()=>{if(ready&&filesReady&&!sent&&!abandoned){sent=true;popup.postMessage({type:'forma-makerworld-import',nonce,payload:data,files},FORMA);notice('Sending to Forma. Keep this MakerWorld tab open until Forma confirms the batch.');}};
   const receive=e=>{if(e.source!==popup||e.origin!==FORMA||e.data?.nonce!==nonce)return;if(e.data.type==='forma-makerworld-ready'){ready=true;send();}if(e.data.type==='forma-makerworld-ack'){finished=true;clearInterval(ping);window.removeEventListener('message',receive);busy=false;$('#send').disabled=false;notice(e.data.ok?'Sent to Forma. Review and approve the batch in the Forma tab.':e.data.error||'Transfer failed. Export the JSON and import it in Forma.');}};
   window.addEventListener('message',receive);
   let tries=0;const ping=setInterval(()=>{if(++tries>600||popup.closed){abandoned=true;clearInterval(ping);window.removeEventListener('message',receive);busy=false;$('#send').disabled=false;if(!finished)notice('The direct handoff was blocked or closed. Export selected JSON and import it in Forma.');return;}if(!sent)popup.postMessage({type:'forma-makerworld-ping',nonce},FORMA);},1000);
   try{
     if($('#files').checked){let total=0;for(let i=0;i<data.items.length;i++){
       if(abandoned)break;notice('Preparing 3MF '+(i+1)+' / '+data.items.length+'…');
       try{const blob=await fileFor(data.items[i]);if(total+blob.size>256*1024*1024){data.warnings.push('Safari file-transfer limit reached. Remaining models are catalog-only.');break;}total+=blob.size;files.push({id:data.items[i].id,name:'MakerWorld-'+data.items[i].id+'.3mf',blob});}
       catch(e){data.warnings.push(data.items[i].title+': '+e.message);if(/401|403|429|authorize|slow down/.test(e.message)){data.warnings.push('Remaining file downloads stopped after an authorization or rate-limit response.');break;}}
       if(i<data.items.length-1)await new Promise(resolve=>setTimeout(resolve,1000));
     }}
     filesReady=true;send();
   }catch(e){notice(e.message+' Export the JSON as a fallback.');clearInterval(ping);window.removeEventListener('message',receive);busy=false;$('#send').disabled=false;}
 };
 $('#capture').onclick=capture;host._formaCapture=capture;
 $('#models').onchange=e=>{if(e.target.matches('.model-check')){e.target.checked?selected.add(e.target.value):selected.delete(e.target.value);$('#count').textContent=selected.size+' selected / '+models.size;}};
 $('#all').onchange=e=>{selected=e.target.checked?new Set(models.keys()):new Set();render();};
 $('#close').onclick=()=>host.remove();discover();capture();
})();
