// Quick adding: drop or paste files anywhere, or paste a model link.
// In the library, files become a new model (after a one-step confirmation).
// On a model page, files are added to that model.
const QUICK_ACCEPTED = /\.(stl|3mf|obj|step|stp|zip|jpe?g|png|webp|gif|pdf)$/i,
  QUICK_MODEL = /\.(stl|3mf|obj|step|stp|zip)$/i,
  QUICK_LIMIT = 510 * 1024 * 1024;
let quickFiles = [],
  quickBusy = false,
  quickDuplicates = new Map(),
  quickCheck = Promise.resolve(),
  quickCheckId = 0,
  dragDepth = 0;

// ---------- File fingerprints ----------
// Browsers only offer crypto.subtle on HTTPS or localhost, and Forma usually runs
// on plain HTTP on a home network, so SHA-256 is implemented here. Reading in
// chunks also keeps large models out of memory.
const SHA256_K = new Int32Array([
  0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5, 0xd807aa98,
  0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174, 0xe49b69c1, 0xefbe4786,
  0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da, 0x983e5152, 0xa831c66d, 0xb00327c8,
  0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967, 0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13,
  0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85, 0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819,
  0xd6990624, 0xf40e3585, 0x106aa070, 0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a,
  0x5b9cca4f, 0x682e6ff3, 0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7,
  0xc67178f2,
]);
function sha256Update(state, words, bytes, end) {
  for (let p = 0; p < end; p += 64) {
    for (let t = 0; t < 16; t++)
      words[t] =
        (bytes[p + t * 4] << 24) | (bytes[p + t * 4 + 1] << 16) | (bytes[p + t * 4 + 2] << 8) | bytes[p + t * 4 + 3];
    for (let t = 16; t < 64; t++) {
      const x = words[t - 15],
        y = words[t - 2],
        s0 = ((x >>> 7) | (x << 25)) ^ ((x >>> 18) | (x << 14)) ^ (x >>> 3),
        s1 = ((y >>> 17) | (y << 15)) ^ ((y >>> 19) | (y << 13)) ^ (y >>> 10);
      words[t] = (words[t - 16] + s0 + words[t - 7] + s1) | 0;
    }
    let [a, b, c, d, e, f, g, h] = state;
    for (let t = 0; t < 64; t++) {
      const big1 = ((e >>> 6) | (e << 26)) ^ ((e >>> 11) | (e << 21)) ^ ((e >>> 25) | (e << 7)),
        temp1 = (h + big1 + ((e & f) ^ (~e & g)) + SHA256_K[t] + words[t]) | 0,
        big0 = ((a >>> 2) | (a << 30)) ^ ((a >>> 13) | (a << 19)) ^ ((a >>> 22) | (a << 10)),
        temp2 = (big0 + ((a & b) ^ (a & c) ^ (b & c))) | 0;
      h = g;
      g = f;
      f = e;
      e = (d + temp1) | 0;
      d = c;
      c = b;
      b = a;
      a = (temp1 + temp2) | 0;
    }
    [a, b, c, d, e, f, g, h].forEach((value, i) => (state[i] = (state[i] + value) | 0));
  }
}
async function sha256File(file, stopped) {
  const state = new Int32Array([
      0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19,
    ]),
    words = new Int32Array(64),
    chunk = 4 * 1024 * 1024;
  let position = 0,
    tail = new Uint8Array(0);
  do {
    if (stopped?.()) throw new Error('Cancelled.');
    const bytes = new Uint8Array(await file.slice(position, position + chunk).arrayBuffer()),
      whole = bytes.length - (bytes.length % 64);
    sha256Update(state, words, bytes, whole);
    tail = bytes.subarray(whole);
    position += bytes.length;
  } while (position < file.size);
  const padded = new Uint8Array(tail.length < 56 ? 64 : 128),
    view = new DataView(padded.buffer);
  padded.set(tail);
  padded[tail.length] = 0x80;
  view.setUint32(padded.length - 8, Math.floor(file.size / 0x20000000));
  view.setUint32(padded.length - 4, (file.size % 0x20000000) * 8);
  sha256Update(state, words, padded, padded.length);
  return [...state].map((n) => (n >>> 0).toString(16).padStart(8, '0')).join('');
}
// Which of these model files are already in the library? Returns Map(file -> matches).
async function findExisting(files, stopped) {
  const candidates = files.filter((f) => QUICK_MODEL.test(f.name) && f.size <= QUICK_LIMIT).slice(0, 200),
    found = new Map();
  if (!candidates.length) return found;
  const hashes = [];
  for (const file of candidates) hashes.push(await sha256File(file, stopped));
  const result = await api('/api/files/lookup', {
    method: 'POST',
    body: JSON.stringify({ files: candidates.map((f, i) => ({ sha256: hashes[i], size: f.size })) }),
  });
  candidates.forEach((file, i) => {
    const matches = result.matches?.[hashes[i]];
    if (matches?.length) found.set(file, matches);
  });
  return found;
}

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
  return `Skipped ${names.join(', ')}: Forma keeps STL, 3MF, OBJ, STEP, ZIP, image and PDF files.`;
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
  try {
    showProgressToast('Checking files…');
    const found = await findExisting(files).catch(() => new Map()),
      onThisModel = files.filter((f) => found.get(f)?.some((match) => match.model_id === id)),
      fresh = files.filter((f) => !onThisModel.includes(f));
    if (!fresh.length)
      return notify(
        onThisModel.length === 1
          ? `${onThisModel[0].name} is already on this model.`
          : 'Those files are already on this model.'
      );
    const label = fresh.length === 1 ? fresh[0].name : fresh.length + ' files';
    showProgressToast(`Uploading ${label}…`);
    await uploadFiles(id, fresh, (p) => showProgressToast(`Uploading ${label}… ${Math.round(p * 100)}%`));
    await refresh();
    if (modelPageId === id) openDetail(id, { push: false });
    const notes = [];
    if (onThisModel.length) notes.push(`${onThisModel.length} already on this model.`);
    if (skipped.length) notes.push(skippedText(skipped));
    notify(`Added ${label} to ${m.title}.` + (notes.length ? ' ' + notes.join(' ') : ''));
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
  setQuickSaveLabel(false);
  $('#quick-add').showModal();
  // Focus the title (not the close button) so Enter adds the model straight away.
  form.elements.title.focus();
  form.elements.title.select();
  quickDuplicates = new Map();
  $('#quick-duplicates').hidden = true;
  $('#quick-duplicates').textContent = '';
  quickCheck = checkQuickDuplicates(files);
}
function setQuickSaveLabel(asCopy) {
  const button = $('#quick-save');
  button.disabled = false;
  button.textContent = asCopy ? 'Add a copy anyway' : 'Add model';
  button.classList.toggle('primary', !asCopy);
  button.classList.toggle('secondary', asCopy);
}
async function checkQuickDuplicates(files) {
  const id = ++quickCheckId,
    box = $('#quick-duplicates'),
    stale = () => id !== quickCheckId,
    timer = setTimeout(() => {
      if (stale()) return;
      box.textContent = 'Checking for duplicates…';
      box.hidden = false;
    }, 250);
  const found = await findExisting(files, stale).catch(() => new Map());
  clearTimeout(timer);
  if (stale()) return;
  quickDuplicates = found;
  renderQuickDuplicates(files);
}
function renderQuickDuplicates(files) {
  const box = $('#quick-duplicates'),
    modelFiles = files.filter((f) => QUICK_MODEL.test(f.name)),
    repeats = modelFiles.filter((f) => quickDuplicates.has(f));
  box.textContent = '';
  box.hidden = !repeats.length;
  if (!repeats.length) return;
  files.forEach((file, index) => {
    const match = quickDuplicates.get(file)?.[0];
    if (match)
      $('#quick-files').children[index].firstChild.insertAdjacentHTML(
        'beforeend',
        `<small class="dup-note">Already in <a href="/models/${escapeHTML(match.model_id)}" data-existing="${escapeHTML(match.model_id)}">${escapeHTML(match.title)}</a></small>`
      );
  });
  if (repeats.length < modelFiles.length) {
    // Name the model after what will be added, unless the title has been edited.
    const title = $('#quick-add-form').elements.title;
    if (title.value === titleFromFiles(files))
      title.value = titleFromFiles(files.filter((f) => !quickDuplicates.has(f)));
    box.innerHTML = `<label class="checkbox-label"><input type="checkbox" id="quick-skip-duplicates" checked> Skip ${repeats.length === 1 ? 'the file' : `the ${repeats.length} files`} already in your library</label>`;
    return;
  }
  const first = quickDuplicates.get(repeats[0])[0];
  box.innerHTML = `<p>${repeats.length === 1 ? 'This file is' : 'These files are'} already in your library.</p><button type="button" class="button primary" id="quick-open-existing" data-existing="${escapeHTML(first.model_id)}">Open ${escapeHTML(first.title)}</button>`;
  setQuickSaveLabel(true);
  // Enter should open what you already have, not add a second copy by accident.
  $('#quick-open-existing').focus();
}
function quickUploadList() {
  return $('#quick-skip-duplicates')?.checked ? quickFiles.filter((f) => !quickDuplicates.has(f)) : quickFiles;
}
$('#quick-add-form').addEventListener('click', (event) => {
  const link = event.target.closest('[data-existing]');
  if (!link || event.metaKey || event.ctrlKey || event.shiftKey || event.button > 0) return;
  event.preventDefault();
  $('#quick-add').close();
  openDetail(link.dataset.existing);
});
$('#quick-add').addEventListener('close', () => quickCheckId++);

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
    // If Enter beat the duplicate check, show what it found before adding anything.
    const knownBefore = quickDuplicates.size;
    await quickCheck;
    if (quickDuplicates.size && !knownBefore) return;
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
    await uploadFiles(created.id, quickUploadList(), setProgress);
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
    fields = Object.fromEntries(new FormData(form)),
    files = quickUploadList();
  $('#quick-add').close();
  openEditor();
  const editor = $('#model-form');
  editor.elements.title.value = fields.title;
  editor.elements.collection.value = fields.collection;
  editor.elements.status.value = fields.status;
  const transfer = new DataTransfer();
  files.forEach((f) => transfer.items.add(f));
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
    overlay.querySelector('span').textContent = 'STL, 3MF, OBJ, STEP, ZIP, photos or PDFs';
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
