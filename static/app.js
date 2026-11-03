'use strict';
const $ = s => document.querySelector(s);
const $$ = s => [...document.querySelectorAll(s)];
const escapeHTML = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let models = [], view = 'all', activeTag = '', activeCollection = '', editingId = null, remoteImage = '', remoteImages = [], listMode = false, toastTimer;
const draftCollections = new Set();
const selectedModels = new Set();
let selectionMode=false, visibleModelIds=[], duplicatePoll=null;
const sourceName = m => { try { return new URL(m.source_url).hostname.replace(/^www\./,''); } catch { return 'Local files'; } };
const modelCollections=m=>[...new Set([...(m.collections||[]),...(m.collection?[m.collection]:[])])];
const prettySource = m => sourceName(m).replace(/\.(com|org|net)$/,'');
const canPreview = m => m.files.some(f=>/\.(3mf|stl|obj|zip)$/i.test(f.name));
function previewMarkup(m, className, detail=false) {
 const url=m.cover_id?'/api/files/'+m.cover_id:canPreview(m)?'/api/models/'+m.id+'/thumbnail':'';
 return url?`<img class="${className}" ${detail?'id="detail-cover"':''} data-model-image="${m.id}" data-photo-cover="${m.cover_id?'1':'0'}" src="${url}" alt="${escapeHTML(m.title)}" ${detail?'':'loading="lazy"'}>`:`<div class="${className} image-placeholder" ${detail?'id="detail-cover"':''}><span>⬡</span><small>No model preview · add a photo</small></div>`;
}
function watchPreviewErrors(root=document) {
 root.querySelectorAll('[data-model-image]').forEach(img=>{
   img.addEventListener('error',()=>{
     const m=models.find(m=>m.id===img.dataset.modelImage);
     if(img.dataset.photoCover==='1'&&m&&canPreview(m)){img.dataset.photoCover='0';img.src='/api/models/'+m.id+'/thumbnail';return;}
     const replacement=document.createElement('div');replacement.className=img.className+' image-placeholder';
     if(img.id)replacement.id=img.id;
     replacement.innerHTML='<span>⬡</span><small>Preview unavailable · add a photo</small>';img.replaceWith(replacement);
   });
 });
}
const bytes = n => n >= 1073741824 ? (n/1073741824).toFixed(1)+' GB' : n >= 1048576 ? (n/1048576).toFixed(1)+' MB' : n >= 1024 ? Math.round(n/1024)+' KB' : n+' B';
function notify(message) { $('#toast').textContent=message; $('#toast').hidden=false; clearTimeout(toastTimer); toastTimer=setTimeout(()=>$('#toast').hidden=true,4500); }
async function api(path, options={}) {
 const response=await fetch(path,{...options,headers:{'X-Forma-Request':'1',...(options.body instanceof FormData?{}:{'Content-Type':'application/json'}),...options.headers}});
 const result=await response.json().catch(()=>({error:'The server returned an unexpected response.'}));
 if(!response.ok) throw new Error(result.error || 'Request failed.');
 return result;
}
async function refresh() {
 try { models=await api('/api/models'); for(const id of selectedModels)if(!models.some(m=>m.id===id))selectedModels.delete(id); $('#error-banner').hidden=true; render(); refreshWatchStatus(); }
 catch(error) { $('#error-banner').textContent='Could not load your library. '+error.message; $('#error-banner').hidden=false; }
}
function chooseView(next,collection='') {
 view=next; activeCollection=collection; activeTag='';
 $$('.nav-item[data-view]').forEach(b=>b.classList.toggle('active',b.dataset.view===view));
 render();
}
function render() {
 const favorites=models.filter(m=>m.favorite).length, queued=models.filter(m=>m.status==='To print').length, printed=models.filter(m=>m.status==='Printed').length;
 $('#total-count').textContent=models.length; $('#favorite-count').textContent=favorites; $('#queue-count').textContent=queued; $('#printed-count').textContent=printed;
 $('#stat-models').textContent=models.length; $('#stat-printed').textContent=printed; $('#stat-queue').textContent=queued;
 const collections=[...new Set([...models.flatMap(modelCollections),...draftCollections])].sort();
 $('#collections').innerHTML=collections.map(name=>`<button class="collection-item ${view==='collection'&&activeCollection===name?'active':''}" data-collection="${escapeHTML(name)}"><span class="folder">▱</span>${escapeHTML(name)}<b>${models.filter(m=>modelCollections(m).includes(name)).length}</b></button>`).join('')||'<div class="collection-empty">Your collections will live here</div>';
 $('#collection-options').innerHTML=collections.map(name=>`<option value="${escapeHTML(name)}"></option>`).join('');
 const previousSource=$('#source-filter').value;
 $('#source-filter').innerHTML='<option value="">All sources</option>'+[...new Set([...models.map(sourceName),...(previousSource?[previousSource]:[])])].sort().map(s=>`<option value="${escapeHTML(s)}">${escapeHTML(s)}</option>`).join('');
 $('#source-filter').value=previousSource;
 const names={all:'Your model library',favorites:'Your favorites',queue:'The print list',printed:'Brought to life',collection:activeCollection};
 $('#page-title').innerHTML=escapeHTML(names[view]||names.all)+'<span>.</span>';
 const subtitles={all:'All those “I should print this” moments, in one place.',favorites:'The designs you keep coming back to.',queue:'Good ideas, waiting for their turn on the build plate.',printed:'From a little inspiration to something you can hold.',collection:'A little corner of your collection, thoughtfully organized.'};
 $('#page-subtitle').textContent=subtitles[view];
 const base=models.filter(m=>view==='all'||view==='favorites'&&m.favorite||view==='queue'&&m.status==='To print'||view==='printed'&&m.status==='Printed'||view==='collection'&&modelCollections(m).includes(activeCollection));
 const tags=[...new Set(base.flatMap(m=>m.tags))].sort();
 $('#tag-filters').innerHTML=`<button class="tag-filter ${!activeTag?'active':''}" data-tag="">All models</button>`+tags.map(t=>`<button class="tag-filter ${activeTag===t?'active':''}" data-tag="${escapeHTML(t)}">${escapeHTML(t)}</button>`).join('');
 const q=$('#search').value.toLocaleLowerCase().trim(), source=$('#source-filter').value, status=$('#status-filter').value;
 let filtered=base.filter(m=>(!activeTag||m.tags.includes(activeTag))&&(!source||sourceName(m)===source)&&(!status||m.status===status)&&(!q||[m.title,m.creator,m.description,...modelCollections(m),...m.tags].join(' ').toLocaleLowerCase().includes(q)));
 const sort=$('#sort').value;
 filtered.sort((a,b)=>sort==='az'?a.title.localeCompare(b.title):sort==='oldest'?a.created_at.localeCompare(b.created_at):b.created_at.localeCompare(a.created_at));
 visibleModelIds=filtered.map(m=>m.id);
 $('#bulk-bar').hidden=!selectionMode;$('#selection-count').textContent=selectedModels.size+' selected';$('#open-bulk').disabled=!selectedModels.size;$('#select-mode').classList.toggle('active',selectionMode);
 $('#result-count').textContent=`${filtered.length} model${filtered.length===1?'':'s'}`;
 $('#empty-state').hidden=models.length!==0; $('#no-results').hidden=filtered.length!==0||models.length===0;
 $('#model-grid').classList.toggle('list-mode',listMode);
 $('#model-grid').innerHTML=filtered.map(m=>{
 const count=m.files.filter(f=>f.kind==='model').length;
 return `<article class="model-card ${selectedModels.has(m.id)?'is-selected':''}">${selectionMode?`<label class="card-selection"><input type="checkbox" data-select-id="${m.id}" aria-label="Select ${escapeHTML(m.title)}" ${selectedModels.has(m.id)?'checked':''}></label>`:''}<button class="card-open" data-open="${m.id}" aria-label="Open ${escapeHTML(m.title)}">${previewMarkup(m,"card-image")}<div class="card-body"><div class="card-meta"><span class="source-label">${escapeHTML(prettySource(m).toUpperCase())}</span><span class="status-pill ${m.status==='Printed'?'printed':''}">${m.status==='Printed'?'✓ ':''}${escapeHTML(m.status)}</span></div><h3>${escapeHTML(m.title)}</h3><div class="creator">${escapeHTML(m.creator?'by '+m.creator:'Your personal collection')}</div><div class="card-tags">${m.tags.slice(0,3).map(t=>`<span>${escapeHTML(t)}</span>`).join('')}${m.tags.length>3?`<span>+${m.tags.length-3}</span>`:''}</div></div></button><button class="favorite-button ${m.favorite?'is-favorite':''}" data-favorite="${m.id}" aria-label="${m.favorite?'Remove from':'Add to'} favorites">${m.favorite?'♥':'♡'}</button><div class="card-footer"><span>▱ &nbsp; ${count} file${count===1?'':'s'}</span><span>${escapeHTML(new Date(m.created_at).toLocaleDateString(undefined,{month:'short',day:'numeric'}))}</span></div></article>`;
 }).join('');
 watchPreviewErrors($('#model-grid'));
}
function openEditor(id=null,importing=false) {
 editingId=id; remoteImage='';remoteImages=[];
 const form=$('#model-form'); form.reset();
 $('#editor-title').textContent=id?'Edit model':'Add a model';
 $('#save-model').textContent=id?'Save changes':'Save to library';
 $('#import-section').hidden=!importing;
 $('#import-url').value=''; $('#import-message').textContent=''; $('#editor-message').textContent=''; $('#file-summary').textContent='Choose files or drop them here';
 const m=models.find(x=>x.id===id);
 if(m) { for(const k of ['title','creator','source_url','description','collection','status','license','license_url','attribution']) form.elements[k].value=m[k]||''; form.elements.tags.value=m.tags.join(', '); form.elements.favorite.checked=m.favorite; }
 else if(activeCollection) form.elements.collection.value=activeCollection;
 if($('#detail').open) $('#detail').close();
 $('#editor').showModal();
 setTimeout(()=>importing?$('#import-url').focus():form.elements.title.focus(),10);
}
async function getMetadata() {
 const input=$('#import-url'), button=$('#fetch-metadata');
 if(!input.value||!input.checkValidity()) { input.reportValidity(); $('#import-message').textContent='Paste the full model page URL first.'; return; }
 button.disabled=true; button.textContent='Reading page…'; $('#import-message').textContent='';
 $('#model-form').elements.source_url.value=input.value;
 try {
 const m=await api('/api/metadata',{method:'POST',body:JSON.stringify({url:input.value})});
 const form=$('#model-form'); for(const k of ['title','creator','description','source_url','license','license_url','attribution']) if(m[k]) form.elements[k].value=m[k];
 form.elements.tags.value=(m.tags||[]).join(', '); remoteImage=m.image_url||'';remoteImages=m.image_urls|| (remoteImage?[remoteImage]:[]);
 $('#import-message').textContent=remoteImage?'Details found. The source photo will be saved when you add the model.':'Details found. Add a cover photo below to make it yours.';
 } catch(error) { $('#import-message').textContent=error.message; }
 finally {button.disabled=false;button.textContent='Get details ↗';}
}
async function saveModel(event) {
 event.preventDefault(); const form=event.currentTarget, button=$('#save-model');
 const files=[...$('#upload-files').files];
 if(files.reduce((n,f)=>n+f.size,0)>510*1024*1024) {$('#editor-message').textContent='Upload up to 510 MB at a time. Use the server folder scanner for larger files.';return;}
 const payload={}; for(const k of ['title','creator','source_url','description','collection','status','license','license_url','attribution']) payload[k]=form.elements[k].value;
 payload.tags=form.elements.tags.value.split(',').map(t=>t.trim()).filter(Boolean); payload.favorite=form.elements.favorite.checked;
 button.disabled=true;button.textContent='Saving…';$('#editor-message').textContent='';
 try {
 let m=await api(editingId?'/api/models/'+editingId:'/api/models',{method:editingId?'PATCH':'POST',body:JSON.stringify(payload)});
 editingId=m.id; // Retain the saved record if an attachment fails; retry updates it.
 if(files.length) {const fd=new FormData();files.forEach(f=>fd.append('files',f));await api('/api/models/'+m.id+'/files',{method:'POST',body:fd});$('#upload-files').value='';}
 let warning='';
 if(remoteImages.length) {try {await api('/api/models/'+m.id+'/source-import',{method:'POST',body:JSON.stringify({image_urls:remoteImages})});remoteImage='';remoteImages=[];}catch(error){warning='Model saved. Source photos could not be queued. Use Retry source import in its details.';}}
 $('#editor').close(); await refresh();notify(warning||'A new idea, safely on your shelf.'); openDetail(m.id);
 } catch(error) {$('#editor-message').textContent=error.message+(editingId?' Your catalog entry is saved; retry to finish attaching files.':''); await refresh();}
 finally {button.disabled=false;button.textContent=editingId?'Save changes':'Save to library';}
}
function openDetail(id) {
 const m=models.find(x=>x.id===id);if(!m)return;
 const images=m.files.filter(f=>f.kind==='image'), files=m.files.filter(f=>f.kind!=='image');
 $('#detail-content').innerHTML=`<div class="detail-top">${previewMarkup(m,"detail-cover",true)}<button class="close" data-close aria-label="Close">×</button></div><div class="detail-body"><div class="detail-heading"><div><div class="eyebrow">${escapeHTML(prettySource(m).toUpperCase())}</div><h2>${escapeHTML(m.title)}</h2><div class="creator">${escapeHTML(m.creator?'by '+m.creator:'Your personal collection')}${modelCollections(m).length?' · '+escapeHTML(modelCollections(m).join(' · ')):''}</div></div><div class="detail-actions"><button class="button secondary" id="detail-favorite">${m.favorite?'♥':'♡'}</button><button class="button primary" id="edit-model">Edit model</button></div></div>${images.length?`<div class="detail-gallery">${images.map(f=>`<button data-photo="${f.id}" title="View ${escapeHTML(f.name)}"><img src="/api/files/${f.id}" alt="${escapeHTML(f.name)}"></button>`).join('')}</div><button class="text-button" id="set-cover" hidden>Use this photo as cover</button>`:''}<div class="card-tags"><span class="status-pill ${m.status==='Printed'?'printed':''}">${escapeHTML(m.status)}</span>${m.tags.map(t=>`<span>${escapeHTML(t)}</span>`).join('')}</div><p class="detail-description">${escapeHTML(m.description||'A little inspiration, ready for your notes.')}</p>${m.source_url?`<a class="detail-source" href="${escapeHTML(m.source_url)}" target="_blank" rel="noopener noreferrer">View original model page ↗</a>`:''}<h3 class="file-heading">MODEL FILES · ${files.length}</h3>${files.map(f=>`<div class="detail-file"><span>▱ &nbsp; ${escapeHTML(f.name)}</span><small>${bytes(f.size)}</small><a href="/api/files/${f.id}?download=1" download>Download ↧</a></div>`).join('')||'<p class="muted">No model files yet. Upload downloaded files using Edit model.</p>'}<div class="detail-end"><span>Collected ${escapeHTML(new Date(m.created_at).toLocaleDateString())}</span><button class="danger-button" id="delete-model">Remove from library</button></div></div>`;
 $('#edit-model').onclick=()=>openEditor(m.id);
 $('#detail-favorite').onclick=async()=>{await toggleFavorite(m.id);openDetail(m.id);};
 $('#delete-model').onclick=async()=>{
 if(!confirm('Remove “'+m.title+'” and its uploaded files? Original files on your server share are kept.'))return;
 try {await api('/api/models/'+m.id,{method:'DELETE'});$('#detail').close();await refresh();notify('Model removed from your library.');}catch(error){notify(error.message);}
 };
 $$('[data-photo]').forEach(b=>b.onclick=()=>{const img=$('#detail-cover');img.src='/api/files/'+b.dataset.photo;$('#set-cover').hidden=false;$('#set-cover').onclick=async()=>{try {await api('/api/models/'+m.id,{method:'PATCH',body:JSON.stringify({cover_id:b.dataset.photo})});await refresh();openDetail(m.id);notify('Cover photo updated.');}catch(error){notify(error.message);}};});
 if(!$('#detail').open)$('#detail').showModal();
 if(typeof loadCatalogDetails==='function')loadCatalogDetails(id);
 if(typeof loadSourceImport==='function')loadSourceImport(id);
 watchPreviewErrors($('#detail'));
}
async function toggleFavorite(id){try{const m=models.find(m=>m.id===id);await api('/api/models/'+id,{method:'PATCH',body:JSON.stringify({favorite:!m.favorite})});await refresh();}catch(error){notify(error.message);}}
function clearFilters() {$('#search').value='';$('#source-filter').value='';$('#status-filter').value='';activeTag='';render();}
$('#add-button').onclick=$('#empty-add').onclick=()=>openEditor();
$('#import-button').onclick=$('#empty-import').onclick=()=>openEditor(null,true);
$('#fetch-metadata').onclick=getMetadata;
$('#import-url').onkeydown=e=>{if(e.key==='Enter'){e.preventDefault();getMetadata();}};
$('#model-form').onsubmit=saveModel;
$('#upload-files').onchange=()=>{$('#file-summary').textContent=[...$('#upload-files').files].map(f=>f.name).join(' · ')||'Choose files or drop them here';};
$('#search').oninput=render;
for(const id of ['source-filter','status-filter','sort'])$('#'+id).onchange=render;
$('#grid-view').onclick=()=>{listMode=false;$('#grid-view').classList.add('selected');$('#list-view').classList.remove('selected');render();};
$('#list-view').onclick=()=>{listMode=true;$('#list-view').classList.add('selected');$('#grid-view').classList.remove('selected');render();};
$('#clear-filters').onclick=clearFilters;
$('#settings-button').onclick=$('#about-button').onclick=()=>$('#settings').showModal();
const mobileScan=document.createElement('button');mobileScan.className='button secondary';mobileScan.textContent='Import server folder ↥';mobileScan.onclick=()=>{$('#settings').close();$('#scan').showModal();};$('#settings .settings-card:nth-of-type(3)').append(mobileScan);
$('#scan-button').onclick=()=>{$('#scan').showModal();};
$('#new-collection').onclick=()=>{$('#collection-form').reset();$('#collection-dialog').showModal();};
$('#collection-form').onsubmit=e=>{e.preventDefault();const name=$('#collection-name').value.trim();if(!name)return;draftCollections.add(name);$('#collection-dialog').close();chooseView('collection',name);openEditor();};
$('#start-scan').onclick=async()=>{
 const b=$('#start-scan');b.disabled=true;b.textContent='Scanning…';$('#scan-message').textContent='';$('#scan-results').innerHTML='';$('#confirm-scan').hidden=true;
 try{const result=await api('/api/scan');$('#scan-results').innerHTML=result.groups.map(g=>`<label class="scan-item"><input type="checkbox" value="${escapeHTML(g.key)}" checked><span>${escapeHTML(g.title)}</span><small>${g.files} files</small></label>`).join('');$('#confirm-scan').hidden=!result.groups.length;$('#scan-message').textContent=result.groups.length?result.groups.length+' new model folders found.':'No new model folders found. Previously imported folders are skipped.';}
 catch(error){$('#scan-message').textContent=error.message;}finally{b.disabled=false;b.textContent='Scan for models';}
};
$('#confirm-scan').onclick=async()=>{const b=$('#confirm-scan'),keys=$$('#scan-results input:checked').map(i=>i.value);if(!keys.length){$('#scan-message').textContent='Select at least one folder.';return;}b.disabled=true;try{const result=await api('/api/scan',{method:'POST',body:JSON.stringify({keys})});$('#scan').close();await refresh();notify(result.imported+' models added from your server.');}catch(error){$('#scan-message').textContent=error.message;}finally{b.disabled=false;}};
document.addEventListener('click',e=>{
 const close=e.target.closest('[data-close]');if(close)close.closest('dialog').close();
 const nav=e.target.closest('[data-view]');if(nav){clearFilters();chooseView(nav.dataset.view);}
 const collection=e.target.closest('[data-collection]');if(collection){clearFilters();chooseView('collection',collection.dataset.collection);}
 const tag=e.target.closest('[data-tag]');if(tag){activeTag=tag.dataset.tag;render();}
 const open=e.target.closest('[data-open]');if(open){if(selectionMode){const id=open.dataset.open;selectedModels.has(id)?selectedModels.delete(id):selectedModels.add(id);render();}else openDetail(open.dataset.open);}
 const favorite=e.target.closest('[data-favorite]');if(favorite)toggleFavorite(favorite.dataset.favorite);
});
document.addEventListener('keydown',e=>{if(e.key==='/'&&!['INPUT','TEXTAREA','SELECT'].includes(document.activeElement.tagName)&&!$$('dialog').some(d=>d.open)){e.preventDefault();$('#search').focus();}});
// Appearance is a device preference. Catalog data always stays on the server.
const themeSelect=$('#theme-choice');
themeSelect.value=document.documentElement.dataset.themeChoice||'dark';
function applyTheme(choice){
 const resolved=choice==='system'?(matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light'):choice;
 document.documentElement.dataset.theme=resolved;document.documentElement.dataset.themeChoice=choice;
 try{localStorage.setItem('forma-theme',choice);}catch{}
 document.querySelector('meta[name="theme-color"]').content=resolved==='dark'?'#101217':'#f5f6fa';
}
themeSelect.onchange=()=>applyTheme(themeSelect.value);
matchMedia('(prefers-color-scheme: dark)').addEventListener('change',()=>{if(themeSelect.value==='system')applyTheme('system');});
$('#select-mode').onclick=()=>{selectionMode=!selectionMode;if(!selectionMode)selectedModels.clear();render();};
$('#exit-selection').onclick=()=>{selectionMode=false;selectedModels.clear();render();};
$('#select-visible').onclick=()=>{visibleModelIds.forEach(id=>selectedModels.add(id));render();};
$('#clear-selection').onclick=()=>{selectedModels.clear();render();};
document.addEventListener('change',e=>{if(e.target.matches('[data-select-id]')){const id=e.target.dataset.selectId;e.target.checked?selectedModels.add(id):selectedModels.delete(id);render();}});
$('#open-bulk').onclick=()=>{$('#bulk-form').reset();$('#bulk-summary').textContent=selectedModels.size+' models selected. Make one update across your collection.';$('#bulk-message').textContent='';$('#bulk-editor').showModal();};
$('#bulk-form').onsubmit=async e=>{
 e.preventDefault();const form=e.currentTarget,changes={};
 for(const key of ['tags_add','tags_remove']){const tags=form.elements[key].value.split(',').map(t=>t.trim()).filter(Boolean);if(tags.length)changes[key]=tags;}
 if(form.elements.status.value)changes.status=form.elements.status.value;
 if(form.elements.favorite.value)changes.favorite=form.elements.favorite.value==='true';
 if(form.elements.change_collection.checked)changes.collection=form.elements.collection.value.trim();
 if(!Object.keys(changes).length){$('#bulk-message').textContent='Choose at least one field to update.';return;}
 const button=$('#save-bulk');button.disabled=true;$('#bulk-message').textContent='';
 try{const result=await api('/api/models/bulk',{method:'POST',body:JSON.stringify({ids:[...selectedModels],changes})});$('#bulk-editor').close();selectionMode=false;selectedModels.clear();await refresh();notify(result.updated+' models updated.');}
 catch(error){$('#bulk-message').textContent=error.message;}finally{button.disabled=false;}
};
async function refreshWatchStatus(){
 try{const status=await api('/api/watch');$('#review-count').textContent=status.pending;$('#watch-banner').hidden=!status.pending;$('#watch-banner-text').textContent=status.pending+' folder change'+(status.pending===1?'':'s')+' ready to review.';}
 catch{}
}
async function loadReviews(){
 const [status,result]=await Promise.all([api('/api/watch'),api('/api/watch/review')]);
 $('#watch-enabled').checked=status.enabled;$('#watch-interval').value=String(status.interval);
 $('#watch-last-scan').textContent=status.error||('Last checked: '+(status.last_scan?new Date(status.last_scan).toLocaleString():'Waiting for the first scan.'));
 $('#review-total').textContent=result.items.length+' items';$('#review-select-all').checked=false;
 $('#review-items').innerHTML=result.items.map(item=>{
   const p=item.payload;const label=item.kind==='new'?'New model':item.kind==='changed'?'Files changed':'Files missing';
   const summary=item.kind==='new'?p.files.length+' files ready to import.':item.kind==='changed'?p.previous_count+' linked files before · '+p.files.length+' files now. Your organization will be kept.':'The source files are unavailable. Dismiss to acknowledge and keep the catalog entry.';
   return `<article class="review-item"><input type="checkbox" class="review-check" value="${item.id}" aria-label="Review ${escapeHTML(p.title)}"><div class="review-item-content"><strong>${escapeHTML(p.title)}</strong><span class="review-kind ${item.kind}">${label}</span><p>${escapeHTML(summary)}</p><small>${escapeHTML(item.folder_key)}</small></div>${p.model_id?`<button class="text-button" data-inbox-model="${p.model_id}">View</button>`:''}</article>`;
 }).join('')||'<div class="inbox-empty"><h3>You’re all caught up.</h3><p>New files and changes will appear here after your next folder scan.</p></div>';
 $('#review-count').textContent=status.pending;await refreshWatchStatus();
 $$('[data-inbox-model]').forEach(b=>b.onclick=()=>{$('#review-dialog').close();openDetail(b.dataset.inboxModel);});
}
async function openInbox(){
 $('#review-message').textContent='';$('#review-dialog').showModal();
 try{await loadReviews();}catch(error){$('#review-message').textContent=error.message;}
}
$('#review-button').onclick=$('#review-banner-button').onclick=openInbox;
$('#review-select-all').onchange=e=>$$('.review-check').forEach(i=>i.checked=e.target.checked);
$('#save-watch').onclick=async()=>{
 try{await api('/api/watch',{method:'PATCH',body:JSON.stringify({enabled:$('#watch-enabled').checked,interval:Number($('#watch-interval').value)})});notify('Folder watching preferences saved.');}
 catch(error){$('#review-message').textContent=error.message;}
};
$('#watch-scan-now').onclick=async()=>{const b=$('#watch-scan-now');b.disabled=true;b.textContent='Scanning…';$('#review-message').textContent='';try{await api('/api/watch/scan',{method:'POST',body:'{}'});await loadReviews();}catch(error){$('#review-message').textContent=error.message;}finally{b.disabled=false;b.textContent='Scan now';}};
async function reviewSelection(action){
 const ids=$$('.review-check:checked').map(i=>Number(i.value));if(!ids.length){$('#review-message').textContent='Select items to review first.';return;}
 $('#approve-review').disabled=$('#ignore-review').disabled=true;$('#review-message').textContent='';
 try{const result=await api('/api/watch/review',{method:'POST',body:JSON.stringify({ids,action})});await refresh();await loadReviews();notify(result.reviewed+' changes '+(action==='approve'?'reviewed.':'dismissed.'));}
 catch(error){$('#review-message').textContent=error.message;}finally{$('#approve-review').disabled=$('#ignore-review').disabled=false;}
}
$('#approve-review').onclick=()=>reviewSelection('approve');$('#ignore-review').onclick=()=>reviewSelection('ignore');
function renderDuplicates(result){
 const state=result.state;$('#scan-duplicates').disabled=state.running;
 $('#duplicate-progress').textContent=state.running?'Checking '+state.processed+' of '+state.total+' files…':state.completed_at?'Checked '+state.total+' files'+(state.errors?' · '+state.errors+' unavailable':'')+' · '+result.groups.length+' duplicate groups':'Ready to compare file contents.';
 $('#duplicate-groups').innerHTML=result.groups.map((group,index)=>`<section class="duplicate-group"><header><strong>Match ${index+1} · ${group.files.length} identical files</strong><small>${bytes(group.extra_bytes)} in extra copies</small></header>${group.files.map(f=>`<div class="duplicate-file"><img src="/api/models/${f.model_id}/thumbnail" alt="" loading="lazy"><div><strong>${escapeHTML(f.title)}</strong><small>${escapeHTML(f.name)} · ${bytes(f.size)}</small></div><button data-duplicate-model="${f.model_id}">View model ↗</button></div>`).join('')}</section>`).join('')||(!state.running&&state.completed_at?'<div class="inbox-empty"><h3>No identical files found.</h3><p>Your indexed model files are unique.</p></div>':'');
 $$('[data-duplicate-model]').forEach(b=>b.onclick=()=>{$('#duplicates-dialog').close();openDetail(b.dataset.duplicateModel);});
 clearTimeout(duplicatePoll);if(state.running&&$('#duplicates-dialog').open)duplicatePoll=setTimeout(loadDuplicates,1500);
}
async function loadDuplicates(){try{renderDuplicates(await api('/api/duplicates'));}catch(error){$('#duplicate-message').textContent=error.message;}}
async function openDuplicates(){$('#duplicate-message').textContent='';$('#duplicates-dialog').showModal();await loadDuplicates();}
$('#duplicates-button').onclick=openDuplicates;
$('#scan-duplicates').onclick=async()=>{try{$('#duplicate-message').textContent='';renderDuplicates(await api('/api/duplicates/scan',{method:'POST',body:'{}'}));}catch(error){$('#duplicate-message').textContent=error.message;}};
$('#duplicates-dialog').addEventListener('close',()=>clearTimeout(duplicatePoll));
const libraryTools=document.createElement('div');libraryTools.className='settings-card';libraryTools.innerHTML='<h3>Library tools</h3><p>Review new files and folder changes, or find identical model files.</p><button class="button secondary" id="settings-inbox">Open import inbox</button><button class="button secondary" id="settings-duplicates">Find duplicates</button>';
$('#settings').append(libraryTools);$('#settings-inbox').onclick=()=>{$('#settings').close();openInbox();};$('#settings-duplicates').onclick=()=>{$('#settings').close();openDuplicates();};
setInterval(refreshWatchStatus,30000);

refresh();
