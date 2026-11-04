'use strict';
let printerPresetData=null,slicerFileId=null;
async function loadPrinterPresetPicker(){
 if(!printerPresetData)printerPresetData=await api('/api/printer-presets');
 const picker=$('#printer-preset-picker'),chosen=picker.value;
 picker.innerHTML='<option value="">Custom printer</option>'+printerPresetData.presets.map(p=>`<option value="${p.id}">${escapeHTML(p.name)}</option>`).join('');picker.value=chosen;
 picker.onchange=()=>{const p=printerPresetData.presets.find(p=>p.id===picker.value);if(!p)return;const form=$('#printer-form');for(const k of ['name','width','depth','height','nozzle','notes','slicer'])form.elements[k].value=p[k]??'';};
}
function invalidateSlicerLink(){ $('#slicer-ready').hidden=true;$('#slicer-launch').removeAttribute('href');$('#slicer-message').textContent=''; }
async function openSlicerFile(id){
 const file=models.flatMap(m=>m.files).find(f=>f.id===id);if(!file)return;
 slicerFileId=id;$('#slicer-form').reset();$('#slicer-file-name').textContent=file.name;invalidateSlicerLink();$('#slicer-download').href='/api/files/'+encodeURIComponent(id)+'?download=1';
 $('#slicer-platform').value=/Mac|iPhone|iPad/i.test(navigator.platform)?'mac':/Win/i.test(navigator.platform)?'windows':'linux';
 try{
  const printers=await api('/api/printers');let lastPrinter='',lastTarget='bambu';try{lastPrinter=localStorage.getItem('forma-slicer-printer')||'';lastTarget=localStorage.getItem('forma-slicer-target')||'bambu';}catch{}
  $('#slicer-printer').innerHTML='<option value="">Choose in the slicer</option>'+printers.map(p=>`<option value="${p.id}">${escapeHTML(p.name)}</option>`).join('');
  $('#slicer-printer').value=printers.some(p=>p.id===lastPrinter)?lastPrinter:printers[0]?.id||'';
  const selected=printers.find(p=>p.id===$('#slicer-printer').value);$('#slicer-target').value=selected?.slicer||(['bambu','snapmaker'].includes(lastTarget)?lastTarget:'bambu');
  $('#slicer-printer').onchange=()=>{const p=printers.find(p=>p.id===$('#slicer-printer').value);if(p?.slicer)$('#slicer-target').value=p.slicer;invalidateSlicerLink();};
  $('#slicer-target').onchange=$('#slicer-platform').onchange=invalidateSlicerLink;
  if(!$('#slicer-dialog').open)$('#slicer-dialog').showModal();
 }catch(error){notify(error.message);}
}
document.addEventListener('click',e=>{const button=e.target.closest('[data-slicer-file]');if(button)openSlicerFile(button.dataset.slicerFile);});
$('#slicer-form').onsubmit=async e=>{
 e.preventDefault();const button=$('#prepare-slicer');button.disabled=true;$('#slicer-message').textContent='Preparing file handoff…';
 const slicer=$('#slicer-target').value,platform=$('#slicer-platform').value,id=slicerFileId;
 try{
  const result=await api('/api/files/'+encodeURIComponent(id)+'/slicer-link',{method:'POST',body:JSON.stringify({slicer,platform})});
  if(id!==slicerFileId||slicer!==$('#slicer-target').value||platform!==$('#slicer-platform').value)return;
  $('#slicer-launch').href=result.launch_url;$('#slicer-launch').textContent='Open '+result.slicer_name;$('#slicer-ready').hidden=false;$('#slicer-message').textContent=result.converted?'Geometry-only 3MF copy prepared for Bambu Studio. Millimeters assumed; check scale and use Arrange in the slicer. Original file kept.':'File handoff ready.';
  try{localStorage.setItem('forma-slicer-printer',$('#slicer-printer').value);localStorage.setItem('forma-slicer-target',slicer);}catch{}
 }catch(error){$('#slicer-message').textContent=error.message;}finally{button.disabled=false;}
};
