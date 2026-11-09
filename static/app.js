'use strict';
const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];
const escapeHTML = (value) =>
  String(value ?? '').replace(
    /[&<>"']/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]
  );
let models = [],
  view = 'all',
  activeTag = '',
  activeCollection = '',
  editingId = null,
  remoteImage = '',
  remoteImages = [],
  listMode = false,
  toastTimer;
const draftCollections = new Set();
const selectedModels = new Set();
let selectionMode = false,
  visibleModelIds = [],
  duplicatePoll = null;
const sourceName = (m) => {
  try {
    return new URL(m.source_url).hostname.replace(/^www\./, '');
  } catch {
    return 'Local files';
  }
};
const modelCollections = (m) => [...new Set([...(m.collections || []), ...(m.collection ? [m.collection] : [])])];
const prettySource = (m) => sourceName(m).replace(/\.(com|org|net)$/, '');
const canPreview = (m) => m.files.some((f) => /\.(3mf|stl|obj|zip)$/i.test(f.name));
function previewMarkup(m, className, detail = false) {
  const url = m.cover_id ? '/api/files/' + m.cover_id : canPreview(m) ? '/api/models/' + m.id + '/thumbnail' : '';
  // A thumbnail the server has not drawn yet waits its turn; see pumpThumbnails.
  const source = !detail && !m.cover_id && !loadedThumbnails.has(url) ? `data-thumbnail="${url}"` : `src="${url}"`;
  return url
    ? `<img class="${className}" ${detail ? 'id="detail-cover"' : ''} data-model-image="${m.id}" data-photo-cover="${m.cover_id ? '1' : '0'}" ${source} alt="${escapeHTML(m.title)}" ${detail ? '' : 'loading="lazy"'}>`
    : `<div class="${className} image-placeholder" ${detail ? 'id="detail-cover"' : ''}><span>⬡</span><small>No model preview · add a photo</small></div>`;
}
// Drawing a new thumbnail can take the server several seconds. Browsers allow six
// connections per server, so a screenful of new models would leave none for anything
// else and Forma would appear to hang. Only a few load at once, nearest the screen only.
const THUMBNAIL_LOADS = 3;
const loadedThumbnails = new Set();
const thumbnailQueue = new Set();
let thumbnailsLoading = 0;
const thumbnailObserver =
  'IntersectionObserver' in window
    ? new IntersectionObserver(
        (entries) => {
          for (const { target, isIntersecting } of entries)
            isIntersecting ? thumbnailQueue.add(target) : thumbnailQueue.delete(target);
          pumpThumbnails();
        },
        { rootMargin: '400px' }
      )
    : null;
function pumpThumbnails() {
  for (const img of thumbnailQueue) {
    if (thumbnailsLoading >= THUMBNAIL_LOADS) return;
    thumbnailQueue.delete(img);
    thumbnailObserver?.unobserve(img);
    startThumbnail(img);
  }
}
function startThumbnail(img) {
  const url = img.dataset.thumbnail;
  let finished = false;
  const finish = (loaded) => {
    if (finished) return;
    finished = true;
    if (loaded) loadedThumbnails.add(url);
    thumbnailsLoading--;
    pumpThumbnails();
  };
  thumbnailsLoading++;
  img.addEventListener('load', () => finish(true), { once: true });
  img.addEventListener('error', () => finish(false), { once: true });
  img.removeAttribute('data-thumbnail');
  img.src = url;
}
function loadThumbnails(root) {
  root.querySelectorAll('img[data-thumbnail]').forEach((img) => {
    if (thumbnailObserver) thumbnailObserver.observe(img);
    else thumbnailQueue.add(img);
  });
  pumpThumbnails();
}
function watchPreviewErrors(root = document) {
  root.querySelectorAll('[data-model-image]:not([data-watched])').forEach((img) => {
    img.dataset.watched = '1';
    img.addEventListener('error', () => {
      const m = models.find((m) => m.id === img.dataset.modelImage);
      if (img.dataset.photoCover === '1' && m && canPreview(m)) {
        img.dataset.photoCover = '0';
        img.src = '/api/models/' + m.id + '/thumbnail';
        return;
      }
      const replacement = document.createElement('div');
      replacement.className = img.className + ' image-placeholder';
      if (img.id) replacement.id = img.id;
      replacement.innerHTML = '<span>⬡</span><small>Preview unavailable · add a photo</small>';
      img.replaceWith(replacement);
    });
  });
}
const bytes = (n) =>
  n >= 1073741824
    ? (n / 1073741824).toFixed(1) + ' GB'
    : n >= 1048576
      ? (n / 1048576).toFixed(1) + ' MB'
      : n >= 1024
        ? Math.round(n / 1024) + ' KB'
        : n + ' B';
function notify(message) {
  $('#toast').textContent = message;
  $('#toast').hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => ($('#toast').hidden = true), 4500);
}
async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: {
      'X-Forma-Request': '1',
      ...(options.body instanceof FormData ? {} : { 'Content-Type': 'application/json' }),
      ...options.headers,
    },
  });
  const result = await response.json().catch(() => ({ error: 'The server returned an unexpected response.' }));
  if (response.status === 401) {
    const next = location.pathname + location.search;
    location.assign('/login?next=' + encodeURIComponent(next));
    throw new Error('Sign in to Forma to continue.');
  }
  if (!response.ok) throw new Error(result.error || 'Request failed.');
  return result;
}
// A home-screen app has no reload button and can sit in the background for days.
let lastRefresh = 0;
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible' && Date.now() - lastRefresh > 5 * 60_000) refresh();
});
async function refresh() {
  lastRefresh = Date.now();
  try {
    models = await api('/api/models');
    for (const id of selectedModels) if (!models.some((m) => m.id === id)) selectedModels.delete(id);
    $('#error-banner').hidden = true;
    render();
    refreshWatchStatus();
  } catch (error) {
    $('#error-banner').textContent = 'Could not load your library. ' + error.message;
    $('#error-banner').hidden = false;
  }
}
function chooseView(next, collection = '') {
  if (modelPageId) leaveModelPage();
  view = next;
  activeCollection = collection;
  activeTag = '';
  $$('.nav-item[data-view]').forEach((b) => b.classList.toggle('active', b.dataset.view === view));
  render();
}
function cardMarkup(m) {
  const count = m.files.filter((f) => f.kind === 'model').length;
  const statusClass = 'status-' + m.status.toLowerCase().replace(/\s+/g, '-');
  const added = new Date(m.created_at).toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
  const tags = m.tags
    .slice(0, 3)
    .map((t) => `<span>${escapeHTML(t)}</span>`)
    .join('');
  return `<article class="model-card ${selectedModels.has(m.id) ? 'is-selected' : ''}">${selectionMode ? `<label class="card-selection"><input type="checkbox" data-select-id="${m.id}" aria-label="Select ${escapeHTML(m.title)}" ${selectedModels.has(m.id) ? 'checked' : ''}></label>` : ''}<a class="card-open" href="/models/${m.id}" data-open="${m.id}" aria-label="Open ${escapeHTML(m.title)}">${previewMarkup(m, 'card-image')}<div class="card-body"><div class="card-title-row"><h3>${escapeHTML(m.title)}</h3><span class="status-pill ${statusClass}">${escapeHTML(m.status)}</span></div><div class="creator">${escapeHTML(m.creator || 'Your collection')}<span class="source-label"> · ${escapeHTML(prettySource(m))}</span></div><div class="card-tags">${tags}${m.tags.length > 3 ? `<span>+${m.tags.length - 3}</span>` : ''}</div></div></a><button class="favorite-button ${m.favorite ? 'is-favorite' : ''}" data-favorite="${m.id}" aria-label="${m.favorite ? 'Remove from' : 'Add to'} favorites"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 20s-7-4.4-7-10a4 4 0 0 1 7-2.6A4 4 0 0 1 19 10c0 5.6-7 10-7 10z"/></svg></button><div class="card-footer"><span>${count} file${count === 1 ? '' : 's'}</span><span>${escapeHTML(added)}</span></div></article>`;
}
// Cards are added as you scroll: drawing thousands at once is what makes a big
// library slow. Filtering and search still cover the whole library.
const CARD_BATCH = 60;
let listedModels = [],
  listKey = '',
  shownCards = CARD_BATCH;
const moreObserver =
  'IntersectionObserver' in window
    ? new IntersectionObserver((entries) => entries.some((e) => e.isIntersecting) && showMoreCards(), {
        rootMargin: '800px',
      })
    : null;
function drawCards(from, to) {
  const grid = $('#model-grid');
  const html = listedModels.slice(from, to).map(cardMarkup).join('');
  if (from) grid.insertAdjacentHTML('beforeend', html);
  else grid.innerHTML = html;
  watchPreviewErrors(grid);
  loadThumbnails(grid);
  const more = $('#show-more');
  more.hidden = to >= listedModels.length;
  if (moreObserver) {
    // Observing again reports the position now, even if the button did not move out of range.
    moreObserver.unobserve(more);
    if (!more.hidden) moreObserver.observe(more);
  }
}
function showMoreCards() {
  const from = Math.min(shownCards, listedModels.length);
  shownCards = from + CARD_BATCH;
  drawCards(from, shownCards);
}
function render() {
  let favorites = 0,
    queued = 0,
    printed = 0;
  const collectionCounts = new Map();
  for (const m of models) {
    if (m.favorite) favorites++;
    if (m.status === 'To print') queued++;
    if (m.status === 'Printed') printed++;
    for (const name of modelCollections(m)) collectionCounts.set(name, (collectionCounts.get(name) || 0) + 1);
  }
  $('#total-count').textContent = models.length;
  $('#favorite-count').textContent = favorites;
  $('#queue-count').textContent = queued;
  $('#printed-count').textContent = printed;
  const collections = [...new Set([...collectionCounts.keys(), ...draftCollections])].sort();
  $('#collections').innerHTML =
    collections
      .map(
        (name) =>
          `<button class="collection-item ${view === 'collection' && activeCollection === name ? 'active' : ''}" data-collection="${escapeHTML(name)}"><span class="folder">▱</span>${escapeHTML(name)}<b>${collectionCounts.get(name) || 0}</b></button>`
      )
      .join('') || '<div class="collection-empty">Your collections will live here</div>';
  $('#collection-options').innerHTML = collections
    .map((name) => `<option value="${escapeHTML(name)}"></option>`)
    .join('');
  const previousSource = $('#source-filter').value;
  $('#source-filter').innerHTML =
    '<option value="">All sources</option>' +
    [...new Set([...models.map(sourceName), ...(previousSource ? [previousSource] : [])])]
      .sort()
      .map((s) => `<option value="${escapeHTML(s)}">${escapeHTML(s)}</option>`)
      .join('');
  $('#source-filter').value = previousSource;
  const names = { all: 'Library', favorites: 'Favorites', queue: 'To print', printed: 'Printed' };
  $('#page-title').textContent = view === 'collection' ? activeCollection : names[view] || names.all;
  if (!modelPageId) document.title = ($('#page-title').textContent || 'Library') + ' · Forma';
  const base = models.filter(
    (m) =>
      view === 'all' ||
      (view === 'favorites' && m.favorite) ||
      (view === 'queue' && m.status === 'To print') ||
      (view === 'printed' && m.status === 'Printed') ||
      (view === 'collection' && modelCollections(m).includes(activeCollection))
  );
  const tags = [...new Set(base.flatMap((m) => m.tags))].sort();
  $('#tag-filters').innerHTML =
    `<button class="tag-filter ${!activeTag ? 'active' : ''}" data-tag="">All models</button>` +
    tags
      .map(
        (t) =>
          `<button class="tag-filter ${activeTag === t ? 'active' : ''}" data-tag="${escapeHTML(t)}">${escapeHTML(t)}</button>`
      )
      .join('');
  const q = $('#search').value.toLocaleLowerCase().trim(),
    source = $('#source-filter').value,
    status = $('#status-filter').value;
  let filtered = base.filter(
    (m) =>
      (!activeTag || m.tags.includes(activeTag)) &&
      (!source || sourceName(m) === source) &&
      (!status || m.status === status) &&
      (!q ||
        [m.title, m.creator, m.description, ...modelCollections(m), ...m.tags]
          .join(' ')
          .toLocaleLowerCase()
          .includes(q))
  );
  const sort = $('#sort').value;
  filtered.sort((a, b) =>
    sort === 'az'
      ? a.title.localeCompare(b.title)
      : sort === 'oldest'
        ? a.created_at.localeCompare(b.created_at)
        : b.created_at.localeCompare(a.created_at)
  );
  visibleModelIds = filtered.map((m) => m.id);
  $('#bulk-bar').hidden = !selectionMode;
  $('#selection-count').textContent = selectedModels.size + ' selected';
  $('#open-bulk').disabled = !selectedModels.size;
  $('#select-mode').classList.toggle('active', selectionMode);
  $('#result-count').textContent = `${filtered.length} model${filtered.length === 1 ? '' : 's'}`;
  $('#empty-state').hidden = models.length !== 0;
  $('#no-results').hidden = filtered.length !== 0 || models.length === 0;
  $('#model-grid').classList.toggle('list-mode', listMode);
  // Keep what is already drawn when the list is unchanged, so returning from a model
  // page or marking a favorite does not lose your place.
  const key = JSON.stringify([view, activeCollection, activeTag, q, source, status, sort]);
  if (key !== listKey) {
    listKey = key;
    shownCards = CARD_BATCH;
  }
  listedModels = filtered;
  thumbnailObserver?.disconnect();
  thumbnailQueue.clear();
  drawCards(0, shownCards);
}
function openEditor(id = null, importing = false) {
  editingId = id;
  remoteImage = '';
  remoteImages = [];
  const form = $('#model-form');
  form.reset();
  $('#editor-title').textContent = id ? 'Edit model' : 'Add a model';
  $('#save-model').textContent = id ? 'Save changes' : 'Save to library';
  $('#import-section').hidden = !importing;
  $('#import-url').value = '';
  $('#import-message').textContent = '';
  $('#editor-message').textContent = '';
  $('#file-summary').textContent = 'Choose files or drop them here';
  const m = models.find((x) => x.id === id);
  if (m) {
    for (const k of [
      'title',
      'creator',
      'source_url',
      'description',
      'collection',
      'status',
      'license',
      'license_url',
      'attribution',
    ])
      form.elements[k].value = m[k] || '';
    form.elements.tags.value = m.tags.join(', ');
    form.elements.favorite.checked = m.favorite;
  } else if (activeCollection) form.elements.collection.value = activeCollection;
  $('#editor').showModal();
  setTimeout(() => (importing ? $('#import-url').focus() : form.elements.title.focus()), 10);
}
async function getMetadata() {
  const input = $('#import-url'),
    button = $('#fetch-metadata');
  if (!input.value || !input.checkValidity()) {
    input.reportValidity();
    $('#import-message').textContent = 'Paste the full model page URL first.';
    return;
  }
  button.disabled = true;
  button.textContent = 'Reading page…';
  $('#import-message').textContent = '';
  $('#model-form').elements.source_url.value = input.value;
  try {
    const m = await api('/api/metadata', { method: 'POST', body: JSON.stringify({ url: input.value }) });
    const form = $('#model-form');
    for (const k of ['title', 'creator', 'description', 'source_url', 'license', 'license_url', 'attribution'])
      if (m[k]) form.elements[k].value = m[k];
    form.elements.tags.value = (m.tags || []).join(', ');
    remoteImage = m.image_url || '';
    remoteImages = m.image_urls || (remoteImage ? [remoteImage] : []);
    $('#import-message').textContent = remoteImage
      ? 'Details found. The source photo will be saved when you add the model.'
      : 'Details found. Add a cover photo below to make it yours.';
  } catch (error) {
    $('#import-message').textContent = error.message;
  } finally {
    button.disabled = false;
    button.textContent = 'Get details ↗';
  }
}
async function saveModel(event) {
  event.preventDefault();
  const form = event.currentTarget,
    button = $('#save-model');
  const files = [...$('#upload-files').files];
  if (files.reduce((n, f) => n + f.size, 0) > 510 * 1024 * 1024) {
    $('#editor-message').textContent = 'Upload up to 510 MB at a time. Use the server folder scanner for larger files.';
    return;
  }
  const payload = {};
  for (const k of [
    'title',
    'creator',
    'source_url',
    'description',
    'collection',
    'status',
    'license',
    'license_url',
    'attribution',
  ])
    payload[k] = form.elements[k].value;
  payload.tags = form.elements.tags.value
    .split(',')
    .map((t) => t.trim())
    .filter(Boolean);
  payload.favorite = form.elements.favorite.checked;
  button.disabled = true;
  button.textContent = 'Saving…';
  $('#editor-message').textContent = '';
  try {
    let m = await api(editingId ? '/api/models/' + editingId : '/api/models', {
      method: editingId ? 'PATCH' : 'POST',
      body: JSON.stringify(payload),
    });
    editingId = m.id; // Retain the saved record if an attachment fails; retry updates it.
    if (files.length) {
      const fd = new FormData();
      files.forEach((f) => fd.append('files', f));
      await api('/api/models/' + m.id + '/files', { method: 'POST', body: fd });
      $('#upload-files').value = '';
    }
    let warning = '';
    if (remoteImages.length) {
      try {
        await api('/api/models/' + m.id + '/source-import', {
          method: 'POST',
          body: JSON.stringify({ image_urls: remoteImages }),
        });
        remoteImage = '';
        remoteImages = [];
      } catch (error) {
        warning = 'Model saved. Source photos could not be queued. Use Retry source import in its details.';
      }
    }
    $('#editor').close();
    await refresh();
    notify(warning || 'A new idea, safely on your shelf.');
    openDetail(m.id, { push: !modelPageId });
  } catch (error) {
    $('#editor-message').textContent =
      error.message + (editingId ? ' Your catalog entry is saved; retry to finish attaching files.' : '');
    await refresh();
  } finally {
    button.disabled = false;
    button.textContent = editingId ? 'Save changes' : 'Save to library';
  }
}
// ---------- Model page ----------
// Each model has its own address (/models/<id>) so Back, bookmarks and links work.
let modelPageId = null,
  libraryScroll = 0,
  viewer = null,
  viewerScript = null;
const VIEWABLE = /\.(stl|obj|3mf|zip)$/i;
const backIcon = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m15 6-6 6 6 6"/></svg>';
const heartIcon =
  '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 20s-7-4.4-7-10a4 4 0 0 1 7-2.6A4 4 0 0 1 19 10c0 5.6-7 10-7 10z"/></svg>';
function loadViewerScript() {
  if (window.FormaViewer) return Promise.resolve(window.FormaViewer);
  viewerScript ||= new Promise((resolve, reject) => {
    const script = document.createElement('script');
    script.src = '/static/vendor/forma-viewer.js';
    script.onload = () => resolve(window.FormaViewer);
    script.onerror = () => {
      viewerScript = null;
      reject(new Error('The 3D viewer could not be loaded.'));
    };
    document.head.append(script);
  });
  return viewerScript;
}
function closeViewer() {
  viewer?.dispose();
  viewer = null;
}
function showPhoto(fileId) {
  closeViewer();
  $('#viewer-stage').hidden = true;
  $('#viewer-meta').textContent = '';
  const img = $('#detail-cover');
  img.hidden = false;
  if (fileId && img.tagName === 'IMG') img.src = '/api/files/' + fileId;
  $$('#detail [data-photo], #detail [data-view3d]').forEach((b) =>
    b.classList.toggle('selected', b.dataset.photo === fileId)
  );
}
async function show3D(file) {
  closeViewer();
  $('#detail-cover').hidden = true;
  $('#set-cover') && ($('#set-cover').hidden = true);
  const stage = $('#viewer-stage'),
    status = $('#viewer-status'),
    meta = $('#viewer-meta');
  stage.hidden = false;
  status.hidden = false;
  status.textContent = 'Loading 3D preview…';
  meta.textContent = '';
  $$('#detail [data-photo], #detail [data-view3d]').forEach((b) =>
    b.classList.toggle('selected', b.dataset.view3d === file.id)
  );
  try {
    const FormaViewer = await loadViewerScript();
    if (!stage.isConnected) return;
    viewer = FormaViewer.mount($('#viewer-canvas-host'), {
      url: '/api/files/' + file.id + '/mesh',
      onStatus: ({ state, progress, size, message }) => {
        if (state === 'loading')
          status.textContent = progress
            ? `Loading 3D preview… ${Math.round(progress * 100)}%`
            : 'Preparing 3D preview…';
        if (state === 'ready') {
          status.hidden = true;
          meta.textContent = `${size.map((n) => (n >= 100 ? Math.round(n) : n.toFixed(1))).join(' × ')} mm · ${file.name} · Drag to turn, scroll or pinch to zoom`;
        }
        if (state === 'error') status.textContent = message;
      },
    });
  } catch (error) {
    status.textContent = error.message;
  }
}
function openDetail(id, { push = true } = {}) {
  const m = models.find((x) => x.id === id);
  if (!m) return;
  const arriving = modelPageId !== id;
  if (!modelPageId) libraryScroll = window.scrollY;
  modelPageId = id;
  if (push && location.pathname !== '/models/' + id)
    history.pushState({ model: id, fromLibrary: true }, '', '/models/' + id);
  closeViewer();
  const images = m.files.filter((f) => f.kind === 'image'),
    files = m.files.filter((f) => f.kind !== 'image'),
    viewable = files.filter((f) => VIEWABLE.test(f.name));
  const thumbs =
    viewable
      .map(
        (f) =>
          `<button class="thumb-3d" data-view3d="${f.id}" title="3D preview of ${escapeHTML(f.name)}" aria-label="3D preview of ${escapeHTML(f.name)}"><span>3D</span><small>${escapeHTML(f.name.replace(/\.[^.]+$/, ''))}</small></button>`
      )
      .join('') +
    images
      .map(
        (f) =>
          `<button data-photo="${f.id}" title="View ${escapeHTML(f.name)}"><img src="/api/files/${f.id}" alt="${escapeHTML(f.name)}" loading="lazy"></button>`
      )
      .join('');
  const statusClass = 'status-' + m.status.toLowerCase().replace(/\s+/g, '-');
  $('#detail-content').innerHTML =
    `<div class="model-page-bar"><a class="back-link" href="/" data-back>${backIcon}Library</a><div class="detail-actions"><button class="button secondary icon-button ${m.favorite ? 'is-favorite' : ''}" id="detail-favorite" aria-label="${m.favorite ? 'Remove from' : 'Add to'} favorites">${heartIcon}</button><button class="button primary" id="edit-model">Edit model</button></div></div>` +
    `<div class="model-layout"><div class="model-media"><div class="model-stage">${previewMarkup(m, 'detail-cover', true)}<div class="viewer-stage" id="viewer-stage" hidden><div id="viewer-canvas-host"></div><p class="viewer-status" id="viewer-status" role="status"></p></div></div>` +
    `${thumbs ? `<div class="detail-gallery">${thumbs}</div>` : ''}<div class="media-footer"><p class="viewer-meta" id="viewer-meta"></p><button class="text-button" id="set-cover" hidden>Use as cover</button></div></div>` +
    `<div class="detail-body"><div class="detail-heading"><div><div class="eyebrow">${escapeHTML(prettySource(m))}</div><h2>${escapeHTML(m.title)}</h2><div class="creator">${escapeHTML(m.creator ? 'by ' + m.creator : 'Your collection')}${modelCollections(m).length ? ' · ' + escapeHTML(modelCollections(m).join(' · ')) : ''}</div></div></div>` +
    `<div class="card-tags"><span class="status-pill ${statusClass}">${escapeHTML(m.status)}</span>${m.tags.map((t) => `<span>${escapeHTML(t)}</span>`).join('')}</div>` +
    `${m.description ? `<p class="detail-description">${escapeHTML(m.description)}</p>` : ''}${m.source_url ? `<a class="detail-source" href="${escapeHTML(m.source_url)}" target="_blank" rel="noopener noreferrer">View original model page ↗</a>` : ''}` +
    `<h3 class="file-heading">Files · ${files.length}</h3>${files.map((f) => `<div class="detail-file"><span>${escapeHTML(f.name)}</span><small>${bytes(f.size)}</small><div class="file-actions">${/\.(stl|3mf|obj|step|stp)$/i.test(f.name) ? `<button class="button secondary" data-slicer-file="${f.id}">Open in slicer</button>` : ''}<a href="/api/files/${f.id}?download=1" download>Download</a></div></div>`).join('') || '<p class="muted">No model files yet. Add downloaded files with Edit model.</p>'}` +
    `<div class="detail-end"><span>Added ${escapeHTML(new Date(m.created_at).toLocaleDateString())}</span><button class="danger-button" id="delete-model">Remove from library</button></div></div></div>`;
  $('#edit-model').onclick = () => openEditor(m.id);
  $('#detail-favorite').onclick = async () => {
    await toggleFavorite(m.id);
    openDetail(m.id, { push: false });
  };
  $('#delete-model').onclick = async () => {
    if (!confirm('Remove “' + m.title + '” and its uploaded files? Original files on your server share are kept.'))
      return;
    try {
      await api('/api/models/' + m.id, { method: 'DELETE' });
      leaveModelPage({ replace: true });
      await refresh();
      notify('Model removed from your library.');
    } catch (error) {
      notify(error.message);
    }
  };
  $$('#detail [data-photo]').forEach(
    (b) =>
      (b.onclick = () => {
        showPhoto(b.dataset.photo);
        const setCover = $('#set-cover');
        setCover.hidden = b.dataset.photo === m.cover_id;
        setCover.onclick = async () => {
          try {
            await api('/api/models/' + m.id, { method: 'PATCH', body: JSON.stringify({ cover_id: b.dataset.photo }) });
            await refresh();
            openDetail(m.id, { push: false });
            notify('Cover photo updated.');
          } catch (error) {
            notify(error.message);
          }
        };
      })
  );
  $$('#detail [data-view3d]').forEach((b) => (b.onclick = () => show3D(files.find((f) => f.id === b.dataset.view3d))));
  document.body.classList.add('viewing-model');
  $('#detail').hidden = false;
  document.title = m.title + ' · Forma';
  if (arriving) window.scrollTo(0, 0);
  // Without a cover photo, the 3D preview is the best first view.
  if (!m.cover_id && viewable.length) show3D(viewable[0]);
  else $$('#detail [data-photo]').forEach((b) => b.classList.toggle('selected', b.dataset.photo === m.cover_id));
  if (typeof loadCatalogDetails === 'function') loadCatalogDetails(id);
  if (typeof loadSourceImport === 'function') loadSourceImport(id);
  watchPreviewErrors($('#detail'));
}
function showLibraryView() {
  if (!modelPageId) return;
  closeViewer();
  modelPageId = null;
  document.body.classList.remove('viewing-model');
  $('#detail').hidden = true;
  $('#detail-content').innerHTML = '';
  $('#detail').dispatchEvent(new Event('close'));
  render();
  window.scrollTo(0, libraryScroll);
}
function leaveModelPage({ replace = false } = {}) {
  if (!replace && history.state?.fromLibrary) return history.back();
  history[replace ? 'replaceState' : 'pushState']({}, '', '/');
  showLibraryView();
}
function route() {
  const match = location.pathname.match(/^\/models\/([A-Za-z0-9-]+)$/);
  if (match && models.some((m) => m.id === match[1])) return openDetail(match[1], { push: false });
  if (match) {
    history.replaceState({}, '', '/');
    if (models.length) notify('That model is no longer in your library.');
  }
  showLibraryView();
}
window.addEventListener('popstate', route);
document.addEventListener('click', (e) => {
  const back = e.target.closest('[data-back]');
  if (!back || e.metaKey || e.ctrlKey || e.shiftKey || e.button > 0) return;
  e.preventDefault();
  leaveModelPage();
});
async function toggleFavorite(id) {
  try {
    const m = models.find((m) => m.id === id);
    const fresh = await api('/api/models/' + id, { method: 'PATCH', body: JSON.stringify({ favorite: !m.favorite }) });
    models = models.map((x) => (x.id === id ? fresh : x));
    render();
  } catch (error) {
    notify(error.message);
  }
}
function clearFilters() {
  $('#search').value = '';
  $('#source-filter').value = '';
  $('#status-filter').value = '';
  activeTag = '';
  render();
}
$('#add-button').onclick = $('#empty-add').onclick = () => openEditor();
$('#import-button').onclick = $('#empty-import').onclick = () => openEditor(null, true);
$('#fetch-metadata').onclick = getMetadata;
$('#import-url').onkeydown = (e) => {
  if (e.key === 'Enter') {
    e.preventDefault();
    getMetadata();
  }
};
$('#model-form').onsubmit = saveModel;
$('#upload-files').onchange = () => {
  $('#file-summary').textContent =
    [...$('#upload-files').files].map((f) => f.name).join(' · ') || 'Choose files or drop them here';
};
$('#search').oninput = render;
$('#show-more').onclick = showMoreCards;
for (const id of ['source-filter', 'status-filter', 'sort']) $('#' + id).onchange = render;
$('#grid-view').onclick = () => {
  listMode = false;
  $('#grid-view').classList.add('selected');
  $('#list-view').classList.remove('selected');
  render();
};
$('#list-view').onclick = () => {
  listMode = true;
  $('#list-view').classList.add('selected');
  $('#grid-view').classList.remove('selected');
  render();
};
$('#clear-filters').onclick = clearFilters;
$('#settings-button').onclick = $('#about-button').onclick = () => $('#settings').showModal();
$('#scan-button').onclick = () => {
  $('#scan').showModal();
};
$('#new-collection').onclick = () => {
  $('#collection-form').reset();
  $('#collection-dialog').showModal();
};
$('#collection-form').onsubmit = (e) => {
  e.preventDefault();
  const name = $('#collection-name').value.trim();
  if (!name) return;
  draftCollections.add(name);
  $('#collection-dialog').close();
  chooseView('collection', name);
  openEditor();
};
$('#start-scan').onclick = async () => {
  const b = $('#start-scan');
  b.disabled = true;
  b.textContent = 'Scanning…';
  $('#scan-message').textContent = '';
  $('#scan-results').innerHTML = '';
  $('#confirm-scan').hidden = true;
  try {
    const result = await api('/api/scan');
    $('#scan-results').innerHTML = result.groups
      .map(
        (g) =>
          `<label class="scan-item"><input type="checkbox" value="${escapeHTML(g.key)}" checked><span>${escapeHTML(g.title)}</span><small>${g.files} files</small></label>`
      )
      .join('');
    $('#confirm-scan').hidden = !result.groups.length;
    $('#scan-message').textContent = result.groups.length
      ? result.groups.length + ' new model folders found.'
      : 'No new model folders found. Previously imported folders are skipped.';
  } catch (error) {
    $('#scan-message').textContent = error.message;
  } finally {
    b.disabled = false;
    b.textContent = 'Scan for models';
  }
};
$('#confirm-scan').onclick = async () => {
  const b = $('#confirm-scan'),
    keys = $$('#scan-results input:checked').map((i) => i.value);
  if (!keys.length) {
    $('#scan-message').textContent = 'Select at least one folder.';
    return;
  }
  b.disabled = true;
  try {
    const result = await api('/api/scan', { method: 'POST', body: JSON.stringify({ keys }) });
    $('#scan').close();
    await refresh();
    notify(result.imported + ' models added from your server.');
  } catch (error) {
    $('#scan-message').textContent = error.message;
  } finally {
    b.disabled = false;
  }
};
document.addEventListener('click', (e) => {
  const close = e.target.closest('[data-close]');
  if (close) close.closest('dialog').close();
  const nav = e.target.closest('[data-view]');
  if (nav) {
    clearFilters();
    chooseView(nav.dataset.view);
  }
  const collection = e.target.closest('[data-collection]');
  if (collection) {
    clearFilters();
    chooseView('collection', collection.dataset.collection);
  }
  const tag = e.target.closest('[data-tag]');
  if (tag) {
    activeTag = tag.dataset.tag;
    render();
  }
  const open = e.target.closest('[data-open]');
  // Let Cmd/Ctrl/middle clicks open the model page in a new tab.
  if (open && (e.metaKey || e.ctrlKey || e.shiftKey || e.button > 0)) return;
  if (open) {
    e.preventDefault();
    if (selectionMode) {
      const id = open.dataset.open;
      selectedModels.has(id) ? selectedModels.delete(id) : selectedModels.add(id);
      render();
    } else openDetail(open.dataset.open);
  }
  const favorite = e.target.closest('[data-favorite]');
  if (favorite) toggleFavorite(favorite.dataset.favorite);
});
document.addEventListener('keydown', (e) => {
  if (
    e.key === '/' &&
    !['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement.tagName) &&
    !$$('dialog').some((d) => d.open)
  ) {
    e.preventDefault();
    $('#search').focus();
  }
});
// Appearance is a device preference. Catalog data always stays on the server.
const themeSelect = $('#theme-choice');
themeSelect.value = document.documentElement.dataset.themeChoice || 'system';
function applyTheme(choice) {
  const resolved =
    choice === 'system' ? (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light') : choice;
  document.documentElement.dataset.theme = resolved;
  document.documentElement.dataset.themeChoice = choice;
  try {
    localStorage.setItem('forma-theme', choice);
  } catch {}
  document.querySelector('meta[name="theme-color"]').content = resolved === 'dark' ? '#141311' : '#f7f5f0';
}
themeSelect.onchange = () => applyTheme(themeSelect.value);
matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
  if (themeSelect.value === 'system') applyTheme('system');
});
$('#select-mode').onclick = () => {
  selectionMode = !selectionMode;
  if (!selectionMode) selectedModels.clear();
  render();
};
$('#exit-selection').onclick = () => {
  selectionMode = false;
  selectedModels.clear();
  render();
};
$('#select-visible').onclick = () => {
  visibleModelIds.forEach((id) => selectedModels.add(id));
  render();
};
$('#clear-selection').onclick = () => {
  selectedModels.clear();
  render();
};
document.addEventListener('change', (e) => {
  if (e.target.matches('[data-select-id]')) {
    const id = e.target.dataset.selectId;
    e.target.checked ? selectedModels.add(id) : selectedModels.delete(id);
    render();
  }
});
$('#open-bulk').onclick = () => {
  $('#bulk-form').reset();
  $('#bulk-summary').textContent = selectedModels.size + ' models selected. Make one update across your collection.';
  $('#bulk-message').textContent = '';
  $('#bulk-editor').showModal();
};
$('#bulk-form').onsubmit = async (e) => {
  e.preventDefault();
  const form = e.currentTarget,
    changes = {};
  for (const key of ['tags_add', 'tags_remove']) {
    const tags = form.elements[key].value
      .split(',')
      .map((t) => t.trim())
      .filter(Boolean);
    if (tags.length) changes[key] = tags;
  }
  if (form.elements.status.value) changes.status = form.elements.status.value;
  if (form.elements.favorite.value) changes.favorite = form.elements.favorite.value === 'true';
  if (form.elements.change_collection.checked) changes.collection = form.elements.collection.value.trim();
  if (!Object.keys(changes).length) {
    $('#bulk-message').textContent = 'Choose at least one field to update.';
    return;
  }
  const button = $('#save-bulk');
  button.disabled = true;
  $('#bulk-message').textContent = '';
  try {
    const result = await api('/api/models/bulk', {
      method: 'POST',
      body: JSON.stringify({ ids: [...selectedModels], changes }),
    });
    $('#bulk-editor').close();
    selectionMode = false;
    selectedModels.clear();
    await refresh();
    notify(result.updated + ' models updated.');
  } catch (error) {
    $('#bulk-message').textContent = error.message;
  } finally {
    button.disabled = false;
  }
};
async function refreshWatchStatus() {
  try {
    const status = await api('/api/watch');
    $('#review-count').textContent = status.pending;
    $('#watch-banner').hidden = !status.pending;
    $('#watch-banner-text').textContent =
      status.pending + ' folder change' + (status.pending === 1 ? '' : 's') + ' ready to review.';
  } catch {}
}
async function loadReviews() {
  const [status, result] = await Promise.all([api('/api/watch'), api('/api/watch/review')]);
  // The list can refresh while it is open, so keep what has been ticked.
  const ticked = new Set($$('.review-check:checked').map((i) => i.value));
  $('#watch-enabled').checked = status.enabled;
  $('#watch-interval').value = String(status.interval);
  $('#watch-last-scan').textContent =
    status.error ||
    'Last checked: ' + (status.last_scan ? new Date(status.last_scan).toLocaleString() : 'Waiting for the first scan.');
  $('#review-total').textContent = result.items.length + ' items';
  $('#review-select-all').checked = result.items.length > 0 && result.items.every((i) => ticked.has(String(i.id)));
  $('#review-items').innerHTML =
    result.items
      .map((item) => {
        const p = item.payload;
        const label = item.kind === 'new' ? 'New model' : item.kind === 'changed' ? 'Files changed' : 'Files missing';
        const summary =
          item.kind === 'new'
            ? p.files.length + ' files ready to import.'
            : item.kind === 'changed'
              ? p.previous_count +
                ' linked files before · ' +
                p.files.length +
                ' files now. Your organization will be kept.'
              : 'The source files are unavailable. Dismiss to acknowledge and keep the catalog entry.';
        return `<article class="review-item"><input type="checkbox" class="review-check" value="${item.id}" aria-label="Review ${escapeHTML(p.title)}" ${ticked.has(String(item.id)) ? 'checked' : ''}><div class="review-item-content"><strong>${escapeHTML(p.title)}</strong><span class="review-kind ${item.kind}">${label}</span><p>${escapeHTML(summary)}</p><small>${escapeHTML(item.folder_key)}</small></div>${p.model_id ? `<button class="text-button" data-inbox-model="${p.model_id}">View</button>` : ''}</article>`;
      })
      .join('') ||
    '<div class="inbox-empty"><h3>You’re all caught up.</h3><p>New files and folder changes will appear here.</p></div>';
  $('#review-count').textContent = status.pending;
  await refreshWatchStatus();
  $$('[data-inbox-model]').forEach(
    (b) =>
      (b.onclick = () => {
        $('#review-dialog').close();
        openDetail(b.dataset.inboxModel);
      })
  );
}
let inboxScan = null;
function scanInbox() {
  // One scan at a time, whether it came from opening the inbox or from Scan now.
  inboxScan ||= (async () => {
    const b = $('#watch-scan-now');
    b.disabled = true;
    b.textContent = 'Scanning…';
    $('#watch-last-scan').textContent = 'Checking your folders…';
    try {
      await api('/api/watch/scan', { method: 'POST', body: '{}' });
      await loadReviews();
    } catch (error) {
      $('#review-message').textContent = error.message;
      await loadReviews().catch(() => {});
    } finally {
      b.disabled = false;
      b.textContent = 'Scan now';
      inboxScan = null;
    }
  })();
  return inboxScan;
}
async function openInbox() {
  $('#review-message').textContent = '';
  $('#review-dialog').showModal();
  try {
    await loadReviews(); // Show what is already known straight away, then look for more.
  } catch (error) {
    $('#review-message').textContent = error.message;
  }
  await scanInbox();
}
$('#review-button').onclick = $('#review-banner-button').onclick = openInbox;
$('#review-select-all').onchange = (e) => $$('.review-check').forEach((i) => (i.checked = e.target.checked));
$('#save-watch').onclick = async () => {
  try {
    await api('/api/watch', {
      method: 'PATCH',
      body: JSON.stringify({ enabled: $('#watch-enabled').checked, interval: Number($('#watch-interval').value) }),
    });
    notify('Folder watching preferences saved.');
  } catch (error) {
    $('#review-message').textContent = error.message;
  }
};
$('#watch-scan-now').onclick = () => {
  $('#review-message').textContent = '';
  scanInbox();
};
async function reviewSelection(action) {
  const ids = $$('.review-check:checked').map((i) => Number(i.value));
  if (!ids.length) {
    $('#review-message').textContent = 'Select items to review first.';
    return;
  }
  $('#approve-review').disabled = $('#ignore-review').disabled = true;
  $('#review-message').textContent = '';
  try {
    await inboxScan; // The server refuses changes while a scan is running.
    const result = await api('/api/watch/review', { method: 'POST', body: JSON.stringify({ ids, action }) });
    await refresh();
    await loadReviews();
    notify(result.reviewed + ' changes ' + (action === 'approve' ? 'reviewed.' : 'dismissed.'));
  } catch (error) {
    $('#review-message').textContent = error.message;
  } finally {
    $('#approve-review').disabled = $('#ignore-review').disabled = false;
  }
}
$('#approve-review').onclick = () => reviewSelection('approve');
$('#ignore-review').onclick = () => reviewSelection('ignore');
function renderDuplicates(result) {
  const state = result.state;
  $('#scan-duplicates').disabled = state.running;
  $('#duplicate-progress').textContent = state.running
    ? 'Checking ' + state.processed + ' of ' + state.total + ' files…'
    : state.completed_at
      ? 'Checked ' +
        state.total +
        ' files' +
        (state.errors ? ' · ' + state.errors + ' unavailable' : '') +
        ' · ' +
        result.groups.length +
        ' duplicate groups'
      : 'Ready to compare file contents.';
  $('#duplicate-groups').innerHTML =
    result.groups
      .map(
        (group, index) =>
          `<section class="duplicate-group"><header><strong>Match ${index + 1} · ${group.files.length} identical files</strong><small>${bytes(group.extra_bytes)} in extra copies</small></header>${group.files.map((f) => `<div class="duplicate-file"><img src="/api/models/${f.model_id}/thumbnail" alt="" loading="lazy"><div><strong>${escapeHTML(f.title)}</strong><small>${escapeHTML(f.name)} · ${bytes(f.size)}</small></div><button data-duplicate-model="${f.model_id}">View model ↗</button></div>`).join('')}</section>`
      )
      .join('') ||
    (!state.running && state.completed_at
      ? '<div class="inbox-empty"><h3>No identical files found.</h3><p>Your indexed model files are unique.</p></div>'
      : '');
  $$('[data-duplicate-model]').forEach(
    (b) =>
      (b.onclick = () => {
        $('#duplicates-dialog').close();
        openDetail(b.dataset.duplicateModel);
      })
  );
  clearTimeout(duplicatePoll);
  if (state.running && $('#duplicates-dialog').open) duplicatePoll = setTimeout(loadDuplicates, 1500);
}
async function loadDuplicates() {
  try {
    renderDuplicates(await api('/api/duplicates'));
  } catch (error) {
    $('#duplicate-message').textContent = error.message;
  }
}
async function openDuplicates() {
  $('#duplicate-message').textContent = '';
  $('#duplicates-dialog').showModal();
  await loadDuplicates();
}
$('#duplicates-button').onclick = openDuplicates;
$('#scan-duplicates').onclick = async () => {
  try {
    $('#duplicate-message').textContent = '';
    renderDuplicates(await api('/api/duplicates/scan', { method: 'POST', body: '{}' }));
  } catch (error) {
    $('#duplicate-message').textContent = error.message;
  }
};
$('#duplicates-dialog').addEventListener('close', () => clearTimeout(duplicatePoll));
setInterval(refreshWatchStatus, 30000);

refresh().then(route);

// Phone layout: the sidebar becomes a drawer opened from the header.
function setMenu(open) {
  document.body.classList.toggle('menu-open', open);
  $('#sidebar-scrim').hidden = !open;
  $('#menu-button').setAttribute('aria-expanded', String(open));
}
$('#menu-button').onclick = () => setMenu(!document.body.classList.contains('menu-open'));
$('#sidebar-scrim').onclick = () => setMenu(false);
$('#sidebar').addEventListener('click', (event) => {
  if (event.target.closest('button') && !event.target.closest('.collection-label')) setMenu(false);
});
document.addEventListener('keydown', (event) => {
  if (event.key !== 'Escape') return;
  if (document.body.classList.contains('menu-open')) setMenu(false);
  else if (modelPageId && !$$('dialog').some((d) => d.open)) leaveModelPage();
});

// Sidebar groups: remember which are open, and show waiting imports on the Import heading.
$$('.nav-group').forEach((group) => {
  try {
    group.open = localStorage.getItem('forma-nav-' + group.id) === 'open';
  } catch {}
  group.addEventListener('toggle', () => {
    try {
      localStorage.setItem('forma-nav-' + group.id, group.open ? 'open' : 'closed');
    } catch {}
  });
});
function syncImportBadge() {
  const pending = Number($('#review-count').textContent) || 0;
  $('#import-badge').textContent = pending;
  $('#import-badge').hidden = !pending;
  $('#review-count').hidden = !pending;
}
new MutationObserver(syncImportBadge).observe($('#review-count'), {
  childList: true,
  characterData: true,
  subtree: true,
});
syncImportBadge();
