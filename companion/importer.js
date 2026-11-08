'use strict';
const $ = (s) => document.querySelector(s),
  $$ = (s) => [...document.querySelectorAll(s)];
const E = (s) =>
  String(s ?? '').replace(
    /[&<>"']/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]
  );
const MW = FormaMakerWorld;
let connection = null,
  collections = [],
  models = [],
  selected = new Set(),
  cancelLoad = false,
  cancelSend = false,
  busy = false;
function message(text) {
  $('#message').textContent = text;
}
async function session() {
  const cookie = await chrome.cookies.get({ url: 'https://makerworld.com', name: 'token' });
  if (!cookie?.value)
    throw Error(
      'Sign in at makerworld.com in this browser first, then try again. The companion cannot use a session from a different browser.'
    );
  return cookie.value;
}
async function forma(path, options = {}) {
  if (!connection) throw Error('Pair with your Forma server first.');
  const response = await fetch(connection.url + path, {
    ...options,
    credentials: 'omit',
    redirect: 'error',
    headers: {
      Authorization: 'Bearer ' + connection.token,
      ...(options.body instanceof FormData ? {} : { 'Content-Type': 'application/json' }),
      ...options.headers,
    },
  });
  const result = await response.json().catch(() => ({ error: 'Forma returned an unexpected response.' }));
  if (!response.ok) throw Error(result.error || 'Forma returned HTTP ' + response.status + '.');
  return result;
}
function renderCollections() {
  const old = new Set($$('.collection-check:checked').map((i) => i.value));
  $('#collections').innerHTML = collections
    .map(
      (c) =>
        `<label class="collection-card"><input class="collection-check" type="checkbox" value="${c.id}" ${old.has(c.id) ? 'checked' : ''}><span>${E(c.name)}</span><small>${c.count} models</small></label>`
    )
    .join('');
}
function renderModels() {
  const query = $('#model-search').value.toLowerCase();
  $('#models').innerHTML = models
    .filter((m) => (m.title + ' ' + m.creator).toLowerCase().includes(query))
    .map(
      (m) =>
        `<label class="model-row"><input class="model-check" type="checkbox" value="${m.id}" ${selected.has(m.id) ? 'checked' : ''}><div><strong>${E(m.title)}</strong><small>${E(m.creator || 'MakerWorld creator')} · ${E(m.collections.map((c) => c.name).join(' · ') || 'Saved model')}${m.profile_id ? ' · 3MF profile available' : ''}</small></div></label>`
    )
    .join('');
  $('#model-count').textContent = selected.size + ' selected / ' + models.length + ' models';
}
$('#pair').onclick = async () => {
  try {
    message('');
    const parsed = new URL($('#forma-url').value);
    if (
      !['http:', 'https:'].includes(parsed.protocol) ||
      parsed.username ||
      parsed.password ||
      parsed.pathname !== '/' ||
      parsed.search ||
      parsed.hash
    )
      throw Error('Enter only the Forma server address, such as http://your-server:8086.');
    const allowed = await chrome.permissions.request({ origins: [parsed.protocol + '//' + parsed.hostname + '/*'] });
    if (!allowed) throw Error('Server access was not granted.');
    const response = await fetch(parsed.origin + '/api/companion/connect', {
      method: 'POST',
      credentials: 'omit',
      redirect: 'error',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        code: $('#pair-code').value.trim(),
        name: navigator.userAgent.includes('Edg/') ? 'Edge companion' : 'Chrome companion',
      }),
    });
    const result = await response.json();
    if (!response.ok) throw Error(result.error || 'Pairing failed.');
    connection = { url: parsed.origin, token: result.token, device_id: result.device_id };
    await chrome.storage.local.set({ formaConnection: connection });
    $('#pair-code').value = '';
    $('#connection-status').textContent = 'Connected to ' + parsed.host;
    $('#disconnect').hidden = false;
    message('Connected. Your MakerWorld sign-in stays in this browser.');
  } catch (error) {
    message(error.message);
  }
};
$('#disconnect').onclick = async () => {
  await chrome.storage.local.remove('formaConnection');
  connection = null;
  $('#connection-status').textContent = 'Not connected';
  $('#disconnect').hidden = true;
  message('This browser forgot its connection. To revoke access on the server too, remove this companion in Forma.');
};
$('#load-collections').onclick = async () => {
  const button = $('#load-collections');
  button.disabled = true;
  message('');
  try {
    const token = await session();
    collections = await MW.collections(fetch, token);
    renderCollections();
    if (!collections.length) message('MakerWorld returned no collections. You can still paste a collection link.');
  } catch (error) {
    message(error.message);
  } finally {
    button.disabled = false;
  }
};
$('#select-all-collections').onclick = () => $$('.collection-check').forEach((i) => (i.checked = true));
$('#add-collection').onclick = async () => {
  try {
    const url = new URL($('#collection-url').value);
    const id = url.hostname === 'makerworld.com' && url.pathname.match(/\/collections\/(\d+)/)?.[1];
    if (!id) throw Error('Paste a MakerWorld collection URL.');
    const token = await session();
    const detail = await MW.api('/design-service/favorites/' + id, fetch, token);
    if (!collections.some((c) => c.id === id))
      collections.push({ id, name: detail.title || 'Collection ' + id, count: Number(detail.designCnt) || 0 });
    renderCollections();
    $(`.collection-check[value="${id}"]`).checked = true;
    message('Collection added. Choose Review selected models.');
  } catch (error) {
    message(error.message);
  }
};
$('#cancel-load').onclick = () => {
  cancelLoad = true;
};
$('#load-models').onclick = async () => {
  if (busy) return;
  const chosen = new Set($$('.collection-check:checked').map((i) => i.value));
  if (!chosen.size && !$('#include-liked').checked) {
    message('Choose a collection or include liked models first.');
    return;
  }
  busy = true;
  cancelLoad = false;
  $('#load-models').disabled = true;
  message('');
  try {
    const token = await session(),
      groups = [];
    for (const collection of collections.filter((c) => chosen.has(c.id))) {
      $('#load-progress').textContent = 'Reading ' + collection.name + '…';
      groups.push(
        await MW.paged(
          '/design-service/favorites/' + collection.id + '/designs?seed=0&collectionId=' + collection.id,
          collection,
          fetch,
          token,
          (n) => ($('#load-progress').textContent = collection.name + ' · ' + n + ' models'),
          () => cancelLoad
        )
      );
    }
    if ($('#include-liked').checked)
      groups.push(
        await MW.paged(
          '/design-service/my/design/like',
          { id: 'liked', name: 'MakerWorld · Liked models' },
          fetch,
          token,
          (n) => ($('#load-progress').textContent = 'Liked models · ' + n),
          () => cancelLoad
        )
      );
    models = MW.merge(groups);
    selected = new Set(models.map((m) => m.id));
    $('#review-section').hidden = false;
    $('#review-link').hidden = true;
    renderModels();
    $('#load-progress').textContent = models.length + ' distinct models ready.';
    if (!models.length) message('MakerWorld returned no models in the selected lists.');
  } catch (error) {
    message(error.message);
  } finally {
    busy = false;
    $('#load-models').disabled = false;
  }
};
$('#model-search').oninput = renderModels;
$('#select-all-models').onchange = (e) => {
  selected = e.target.checked ? new Set(models.map((m) => m.id)) : new Set();
  renderModels();
};
$('#models').onchange = (e) => {
  if (e.target.matches('.model-check')) {
    e.target.checked ? selected.add(e.target.value) : selected.delete(e.target.value);
    $('#model-count').textContent = selected.size + ' selected / ' + models.length + ' models';
  }
};

async function downloadDefault(model, token) {
  let item = model;
  if (!item.profile_id) {
    const detail = await MW.api('/design-service/design/' + model.id, fetch, token);
    item = { ...model, ...MW.normalize(detail) };
  }
  if (!item.profile_id) throw Error(model.title + ': no downloadable 3MF profile was returned.');
  let response = await fetch(MW.API + '/design-service/instance/' + item.profile_id + '/f3mf?type=download', {
    headers: { Authorization: 'Bearer ' + token },
    credentials: 'omit',
    redirect: 'error',
  });
  if (response.status === 429) throw Error('MakerWorld rate limit reached. Stop and retry this model later.');
  if (!response.ok) throw Error(model.title + ': MakerWorld download returned HTTP ' + response.status + '.');
  if ((response.headers.get('Content-Type') || '').includes('json')) {
    const result = await response.json();
    const value = result.url || result.data?.url || result.link;
    const url = new URL(value);
    const trusted = ['bblmw.com', 'bambulab.com', 'makerworld.com'];
    if (
      url.protocol !== 'https:' ||
      url.username ||
      url.password ||
      !trusted.some((h) => url.hostname === h || url.hostname.endsWith('.' + h))
    )
      throw Error('MakerWorld returned an unexpected download host.');
    const allowed = await chrome.permissions.contains({ origins: [url.protocol + '//' + url.hostname + '/*'] });
    if (!allowed)
      throw Error(
        'This download uses an unapproved host: ' +
          url.hostname +
          '. Download it normally from MakerWorld and upload it in Forma.'
      );
    response = await fetch(url.href, { credentials: 'omit', redirect: 'error' });
    if (!response.ok) throw Error('File download failed with HTTP ' + response.status + '.');
  }
  const max = 256 * 1024 * 1024;
  const length = Number(response.headers.get('Content-Length') || 0);
  if (length > max) throw Error('The file exceeds the companion’s 256 MB limit.');
  const reader = response.body.getReader(),
    chunks = [];
  let size = 0;
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    size += value.length;
    if (size > max) {
      await reader.cancel();
      throw Error('The file exceeds the companion’s 256 MB limit.');
    }
    chunks.push(value);
  }
  const blob = new Blob(chunks, { type: 'application/octet-stream' });
  const prefix = new Uint8Array(await blob.slice(0, 4).arrayBuffer());
  if (prefix[0] !== 80 || prefix[1] !== 75)
    throw Error('The response was not a 3MF archive. Download this file normally from MakerWorld.');
  return blob;
}
$('#cancel-send').onclick = () => {
  cancelSend = true;
};
$('#send').onclick = async () => {
  if (busy) return;
  if (!connection) {
    message('Pair with Forma first.');
    return;
  }
  const items = models.filter((m) => selected.has(m.id));
  if (!items.length) {
    message('Choose models to send first.');
    return;
  }
  busy = true;
  cancelSend = false;
  $('#send').disabled = true;
  $('#review-link').hidden = true;
  message('');
  let batch = null;
  try {
    const warnings = [];
    const wantFiles = $('#download-files').checked;
    const token = wantFiles ? await session() : '';
    // Refresh details only for selected models, at a deliberate one-at-a-time pace.
    for (let i = 0; i < items.length; i++) {
      if (cancelSend) throw Error('Cancelled before sending a batch.');
      $('#send-progress').textContent = 'Reading model details ' + (i + 1) + ' / ' + items.length + '…';
      try {
        const detail = await MW.api('/design-service/design/' + items[i].id, fetch, await session());
        const full = MW.normalize(detail);
        if (full) items[i] = { ...full, collections: items[i].collections, favorite: items[i].favorite };
      } catch (error) {
        if (/authorize|Sign in|slow down|HTTP 429/.test(error.message)) throw error;
        warnings.push(items[i].title + ': detailed metadata unavailable; using collection data.');
      }
      if (i < items.length - 1) await new Promise((resolve) => setTimeout(resolve, 400));
    }
    batch = await forma('/api/companion/batches', {
      method: 'POST',
      body: JSON.stringify({ title: 'MakerWorld · ' + new Date().toLocaleDateString(), items }),
    });
    if (wantFiles) {
      for (let i = 0; i < items.length; i++) {
        if (cancelSend) {
          warnings.push('File downloads stopped by user. Remaining models are catalog-only.');
          break;
        }
        $('#send-progress').textContent = 'Downloading model ' + (i + 1) + ' / ' + items.length + '…';
        try {
          const blob = await downloadDefault(items[i], token);
          const fd = new FormData();
          fd.append('file', blob, 'MakerWorld-' + items[i].id + '.3mf');
          await forma('/api/companion/batches/' + batch.id + '/files/' + items[i].id, { method: 'POST', body: fd });
        } catch (error) {
          warnings.push(items[i].title + ': ' + error.message);
          if (/rate limit|slow down|HTTP 429/.test(error.message)) {
            warnings.push('Remaining file downloads paused after a MakerWorld rate limit.');
            break;
          }
        }
        if (i < items.length - 1) await new Promise((resolve) => setTimeout(resolve, 1000));
      }
    }
    await forma('/api/companion/batches/' + batch.id + '/finish', {
      method: 'POST',
      body: JSON.stringify({ warnings }),
    });
    $('#review-link').href = connection.url + '/?makerworldBatch=' + encodeURIComponent(batch.id);
    $('#review-link').hidden = false;
    $('#send-progress').textContent =
      items.length + ' models sent. Approve the batch in Forma to add them to your library.';
    if (warnings.length) message(warnings.length + ' items need attention. Details are shown in the Forma review.');
  } catch (error) {
    message(error.message + (batch ? ' A partial batch is visible in Forma; dismiss it before starting over.' : ''));
  } finally {
    busy = false;
    $('#send').disabled = false;
  }
};
(async () => {
  await chrome.storage.local.setAccessLevel?.({ accessLevel: 'TRUSTED_CONTEXTS' });
  connection = (await chrome.storage.local.get('formaConnection')).formaConnection || null;
  if (connection) {
    $('#forma-url').value = connection.url;
    $('#connection-status').textContent = 'Connected to ' + new URL(connection.url).host;
    $('#disconnect').hidden = false;
  }
})().catch((error) => message(error.message));
