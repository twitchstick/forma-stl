// These internal, read-only API paths are intentionally isolated for future changes.
// MakerWorld's session token is used only with api.bambulab.com and never persisted.
(function(root){
 const API='https://api.bambulab.com/v1';
 const MAX_MODELS=500, PAGE_SIZE=20;
 function list(data){return Array.isArray(data)?data:Array.isArray(data?.hits)?data.hits:Array.isArray(data?.data?.hits)?data.data.hits:Array.isArray(data?.items)?data.items:[];}
 function id(value){const s=String(value??'');return /^\d{1,20}$/.test(s)?s:'';}
 function text(value){return typeof value==='string'?value:'';}
 function normalize(raw,collection){
   const d=raw.design||raw;const remote=id(d.id??d.designId??raw.designId);if(!remote)return null;
   const creator=d.designCreator||d.creator||{};
   const instances=Array.isArray(d.instances)?d.instances:[];
   const profile=instances.find(i=>String(i.id)===String(d.defaultInstanceId))||instances.find(i=>i.isDefault)||instances[0];
   const image=d.coverUrl||d.coverLandscape||d.cover||d.thumbnail||d.images?.[0]?.url||d.images?.[0]||'';
   return {id:remote,title:text(d.title||d.name)||'MakerWorld '+remote,creator:text(creator.name||creator.handle),
     description:text(d.description||d.summary).replace(/<[^>]*>/g,' ').replace(/\s+/g,' ').trim(),
     tags:(Array.isArray(d.tags)?d.tags:[]).map(t=>typeof t==='string'?t:t?.name||t?.title||'').filter(Boolean),
     image_url:typeof image==='string'?image:'',profile_id:id(profile?.id||d.defaultInstanceId),
     favorite:collection?.id==='liked',collections:collection?[{id:String(collection.id),name:collection.name}]:[]};
 }
 async function api(path,fetcher=fetch,token=''){
   if(!path.startsWith('/')||path.startsWith('//'))throw Error('Invalid MakerWorld API path.');
   const response=await fetcher(API+path,{headers:token?{Authorization:'Bearer '+token}:{},credentials:'omit',redirect:'error'});
   if([401,403].includes(response.status))throw Error('MakerWorld could not authorize this request. Sign in at makerworld.com in this browser, then try again. If the site asks for verification, complete it there.');
   if(response.status===429)throw Error('MakerWorld asked us to slow down. Wait a few minutes before trying again.');
   if(!response.ok)throw Error('MakerWorld returned HTTP '+response.status+'. Nothing has been changed on MakerWorld.');
   return response.json();
 }
 async function collections(fetcher,token){
   const result=await api('/design-service/my/favorites/listlite?query=',fetcher,token);
   if(!Array.isArray(result?.hits)&&!Array.isArray(result))throw Error('MakerWorld changed its collection response. The importer needs an update.');
   return list(result).filter(c=>id(c.id)).map(c=>({id:String(c.id),name:text(c.title)||'Collection '+c.id,count:Number(c.designCnt)||0,private:c.status===0||c.status==='private'}));
 }
 async function paged(path,collection,fetcher,token,progress=()=>{},cancel=()=>false){
   const models=[],seen=new Set();
   for(let offset=0;offset<MAX_MODELS;offset+=PAGE_SIZE){
     if(cancel())throw Error('Cancelled. No changes were made to MakerWorld.');
     const data=await api(path+(path.includes('?')?'&':'?')+'limit='+PAGE_SIZE+'&offset='+offset,fetcher,token);
     if(!Array.isArray(data)&&!Array.isArray(data?.hits)&&!Array.isArray(data?.data?.hits)&&!Array.isArray(data?.items))throw Error('MakerWorld changed its model-list response. The importer needs an update.');
     const entries=list(data);if(!entries.length)break;
     let fresh=0;
     for(const raw of entries){const m=normalize(raw,collection);if(m&&!seen.has(m.id)){seen.add(m.id);models.push(m);fresh++;}}
     progress(models.length);
     if(!fresh)throw Error('MakerWorld repeated a page; stopped to avoid missing models. Retry this collection later.');
     if(entries.length<PAGE_SIZE||(Number.isFinite(Number(data.total))&&offset+entries.length>=Number(data.total)))break;
     if(offset+PAGE_SIZE>=MAX_MODELS)throw Error('This list exceeds 500 models. Import a smaller collection first.');
     await new Promise(resolve=>setTimeout(resolve,400));
   }
   return models;
 }
 function merge(groups){
   const result=new Map();
   for(const model of groups.flat()){
     const old=result.get(model.id);
     if(old){old.collections=[...new Map([...old.collections,...model.collections].map(c=>[c.id,c])).values()];old.favorite=old.favorite||model.favorite;}
     else result.set(model.id,{...model,collections:[...model.collections]});
   }
   if(result.size>MAX_MODELS)throw Error('Choose collections totaling at most 500 distinct models per batch.');
   return [...result.values()];
 }
 root.FormaMakerWorld={API,list,id,normalize,api,collections,paged,merge};
 if(typeof module!=='undefined')module.exports=root.FormaMakerWorld;
})(typeof window!=='undefined'?window:globalThis);
