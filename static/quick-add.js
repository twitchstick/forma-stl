// Quick adding: drop or paste files anywhere, or paste a model link.
// In the library, files become a new model (after a one-step confirmation).
// On a model page, files are added to that model.
const QUICK_ACCEPTED = /\.(stl|3mf|obj|step|stp|zip|jpe?g|png|webp|gif)$/i,
  QUICK_MODEL = /\.(stl|3mf|obj|step|stp|zip)$/i,
  QUICK_LIMIT = 510 * 1024 * 1024;
let quickFiles = [],
  quickBusy = false,
  dragDepth = 0;

function anyDialogOpen() {
  return $$('dialog').some((d) => d.open);
}
function typingTarget(target) {
  return target instanceof Element && target.closest('input, textarea, select, [contenteditable="true"]');
}
function titleFromFiles(files) {
  const named = files.find((f) => QUICK_MODEL.test(f.name)) || files[0];
  return (
    named.name
      .replace(/\.[^.]+$/, '')
      .replace(/[_-]+/g, ' ')
      .replace(/\s+/g, ' ')
      .trim() || 'New model'
  );
}
function sortIncoming(list) {
  const files = [...list],
    accepted = files.filter((f) => QUICK_ACCEPTED.test(f.name)),
    skipped = files.filter((f) => !QUICK_ACCEPTED.test(f.name));
  return { accepted, skipped };
}
function skippedText(skipped) {
  if (!skipped.length) return '';
  const names = skipped.slice(0, 3).map((f) => f.name);
  if (skipped.length > 3) names.push(`${skipped.length - 3} more`);
  return `Skipped ${names.join(', ')}: Forma keeps STL, 3MF, OBJ, STEP, ZIP and image files.`;
}
function showProgressToast(text) {
  clearTimeout(toastTimer);
  $('#toast').textContent = text;
  $('#toast').hidden = false;
}

// Upload with progress. fetch() cannot report upload progress, so this uses XMLHttpRequest.
function uploadFiles(modelId, files, onProgress = () => {}) {
  return new Promise((resolve, reject) => {
    const body = new FormData();
    files.forEach((f) => body.append('files', f));
    const request = new XMLHttpRequest();
    request.open('POST', '/api/models/' + modelId + '/files');
    request.setRequestHeader('X-Forma-Request', '1');
    request.responseType = 'json';
    request.upload.onprogress = (e) => e.lengthComputable && onProgress(e.loaded / e.total);
    request.onload = () => {
      if (request.status === 401) {
        location.assign('/login?next=' + encodeURIComponent(location.pathname + location.search));
        return reject(new Error('Sign in to Forma to continue.'));
      }
      if (request.status >= 200 && request.status < 300) return resolve(request.response);
      reject(new Error(request.response?.error || 'The upload failed.'));
    };
    request.onerror = () => reject(new Error('The upload was interrupted. Check your connection and try again.'));
    request.send(body);
  });
}

async function addFilesToModel(id, files, skipped) {
  const m = models.find((x) => x.id === id);
  if (!m || quickBusy) return;
  if (files.reduce((n, f) => n + f.size, 0) > QUICK_LIMIT)
    return notify('Upload up to 510 MB at a time. Use the server folder scanner for larger files.');
  quickBusy = true;
  const label = files.length === 1 ? files[0].name : files.length + ' files';
  try {
    showProgressToast(`Uploading ${label}…`);
    await uploadFiles(id, files, (p) => showProgressToast(`Uploading ${label}… ${Math.round(p * 100)}%`));
    await refresh();
    if (modelPageId === id) openDetail(id, { push: false });
    notify(`Added ${label} to ${m.title}.` + (skipped.length ? ' ' + skippedText(skipped) : ''));
  } catch (error) {
    notify(error.message);
  } finally {
    quickBusy = false;
  }
}

function openQuickAdd(files, skipped) {
  quickFiles = files;
  const form = $('#quick-add-form');
  form.reset();
  form.elements.title.value = titleFromFiles(files);
  if (view === 'collection' && activeCollection) form.elements.collection.value = activeCollection;
  if (view === 'queue') form.elements.status.value = 'To print';
  if (view === 'printed') form.elements.status.value = 'Printed';
  $('#quick-files').innerHTML = files
    .map((f) => `<li><span>${escapeHTML(f.name)}</span><small>${bytes(f.size)}</small></li>`)
    .join('');
  $('#quick-skipped').textContent = skippedText(skipped);
  $('#quick-skipped').hidden = !skipped.length;
  $('#quick-message').textContent = '';
  $('#quick-progress').hidden = true;
  $('#quick-save').disabled = false;
  $('#quick-add').showModal();
  // Focus the title (not the close button) so Enter adds the model straight away.
  form.elements.title.focus();
  form.elements.title.select();
}

$('#quick-add-form').onsubmit = async (event) => {
  event.preventDefault();
  if (quickBusy) return;
  const form = event.currentTarget,
    button = $('#quick-save'),
    progress = $('#quick-progress');
  if (quickFiles.reduce((n, f) => n + f.size, 0) > QUICK_LIMIT) {
    $('#quick-message').textContent = 'Upload up to 510 MB at a time. Use the server folder scanner for larger files.';
    return;
  }
  quickBusy = true;
  button.disabled = true;
  $('#quick-message').textContent = '';
  let created = null;
  try {
    created = await api('/api/models', {
      method: 'POST',
      body: JSON.stringify({
        title: form.elements.title.value,
        collection: form.elements.collection.value.trim(),
        status: form.elements.status.value,
        tags: [],
      }),
    });
    progress.hidden = false;
    const setProgress = (p) => {
      progress.querySelector('progress').value = p;
      progress.querySelector('span').textContent = p < 1 ? `Uploading… ${Math.round(p * 100)}%` : 'Finishing…';
    };
    setProgress(0);
    await uploadFiles(created.id, quickFiles, setProgress);
    $('#quick-add').close();
    await refresh();
    openDetail(created.id);
    notify(`Added ${created.title}.`);
  } catch (error) {
    // Keep a model that was created even if its upload failed, so nothing is lost.
    if (created) {
      $('#quick-add').close();
      await refresh();
      openDetail(created.id);
      notify(`${created.title} was added, but its files were not: ${error.message} Drop them on this page to retry.`);
    } else $('#quick-message').textContent = error.message;
  } finally {
    quickBusy = false;
    button.disabled = false;
  }
};

// "More details" hands the files to the full editor.
$('#quick-more').onclick = () => {
  const form = $('#quick-add-form'),
    fields = Object.fromEntries(new FormData(form));
  $('#quick-add').close();
  openEditor();
  const editor = $('#model-form');
  editor.elements.title.value = fields.title;
  editor.elements.collection.value = fields.collection;
  editor.elements.status.value = fields.status;
  const transfer = new DataTransfer();
  quickFiles.forEach((f) => transfer.items.add(f));
  $('#upload-files').files = transfer.files;
  $('#upload-files').dispatchEvent(new Event('change'));
};

function receiveFiles(list) {
  const { accepted, skipped } = sortIncoming(list);
  if (!accepted.length) return notify(skippedText(skipped) || 'Nothing to add.');
  if (modelPageId) addFilesToModel(modelPageId, accepted, skipped);
  else openQuickAdd(accepted, skipped);
}

// ---------- Drag and drop ----------
const dragHasFiles = (event) => [...(event.dataTransfer?.types || [])].includes('Files');
function showDropOverlay(show) {
  const overlay = $('#drop-overlay');
  if (show) {
    const m = modelPageId && models.find((x) => x.id === modelPageId);
    overlay.querySelector('strong').textContent = m ? `Add files to ${m.title}` : 'Drop to add a model';
    overlay.querySelector('span').textContent = 'STL, 3MF, OBJ, STEP, ZIP or photos';
  }
  overlay.hidden = !show;
}
window.addEventListener('dragenter', (event) => {
  if (!dragHasFiles(event) || anyDialogOpen()) return;
  dragDepth++;
  showDropOverlay(true);
});
window.addEventListener('dragleave', (event) => {
  if (!dragHasFiles(event) || !dragDepth) return;
  if (--dragDepth === 0) showDropOverlay(false);
});
window.addEventListener('dragover', (event) => {
  // Without this, a missed drop opens the file in the browser instead of adding it.
  if (dragHasFiles(event) && !(event.target instanceof HTMLInputElement && event.target.type === 'file'))
    event.preventDefault();
});
window.addEventListener('drop', (event) => {
  if (!dragHasFiles(event)) return;
  const intoFileInput = event.target instanceof HTMLInputElement && event.target.type === 'file';
  if (!intoFileInput) event.preventDefault();
  dragDepth = 0;
  showDropOverlay(false);
  if (intoFileInput || anyDialogOpen()) return;
  receiveFiles(event.dataTransfer.files);
});

// ---------- Paste ----------
document.addEventListener('paste', (event) => {
  if (typingTarget(event.target) || anyDialogOpen()) return;
  const data = event.clipboardData;
  if (!data) return;
  if (data.files.length) {
    event.preventDefault();
    return receiveFiles(data.files);
  }
  const text = data.getData('text/plain').trim();
  if (/^https?:\/\/\S+$/i.test(text)) {
    event.preventDefault();
    openEditor(null, true);
    $('#import-url').value = text;
    getMetadata();
  }
});
