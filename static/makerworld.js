'use strict';
let mwBatchId = null;
const mwDialog = document.querySelector('#makerworld-dialog');
async function loadMakerWorld() {
  const [batches, devices, bookmark, websiteBookmark] = await Promise.all([
    api('/api/makerworld/batches'),
    api('/api/makerworld/devices'),
    api('/api/makerworld/bookmarklet'),
    api('/api/websites/bookmarklet'),
  ]);
  $('#mw-bookmarklet').href = bookmark.url;
  $('#website-bookmarklet').href = websiteBookmark.url;
  $('#mw-devices').innerHTML = devices.devices.length
    ? '<p class="hint">Paired browsers</p>' +
      devices.devices
        .map(
          (d) =>
            `<div class="mw-device"><span>${escapeHTML(d.name)}</span><button class="danger-button" data-mw-revoke="${d.id}">Revoke access</button></div>`
        )
        .join('')
    : '';
  $('#mw-batches').innerHTML =
    batches.batches
      .filter((b) => b.state !== 'dismissed')
      .map(
        (b) =>
          `<div class="mw-device"><div><strong>${escapeHTML(b.title)}</strong><small>${b.count} models · ${escapeHTML(b.state === 'receiving' ? 'Still receiving files' : b.state === 'pending' ? 'Ready for review' : 'Imported')} · ${escapeHTML(new Date(b.created_at).toLocaleDateString())}</small></div><button class="text-button" data-mw-open="${b.id}">View</button></div>`
      )
      .join('') ||
    '<p class="hint">No incoming imports yet. Send models from the Safari bookmark or optional companion.</p>';
  $$('[data-mw-revoke]').forEach(
    (b) =>
      (b.onclick = async () => {
        try {
          await api('/api/makerworld/devices/' + b.dataset.mwRevoke, { method: 'DELETE' });
          await loadMakerWorld();
          notify('Companion access revoked.');
        } catch (e) {
          $('#mw-message').textContent = e.message;
        }
      })
  );
  $$('[data-mw-open]').forEach((b) => (b.onclick = () => openMakerWorldBatch(b.dataset.mwOpen)));
}
async function openMakerWorld(id = null) {
  $('#mw-message').textContent = '';
  $('#mw-server-url').value = location.origin;
  if (!mwDialog.open) mwDialog.showModal();
  try {
    await loadMakerWorld();
    if (id) await openMakerWorldBatch(id);
  } catch (error) {
    $('#mw-message').textContent = error.message;
  }
}
async function openMakerWorldBatch(id) {
  try {
    const b = await api('/api/makerworld/batches/' + id);
    mwBatchId = id;
    $('#mw-batch-detail').innerHTML =
      `<div class="settings-card"><h3>${escapeHTML(b.title)}</h3>${b.warnings.length ? `<details class="mw-warnings"><summary>${b.warnings.length} import notes</summary><ul>${b.warnings.map((w) => `<li>${escapeHTML(w)}</li>`).join('')}</ul></details>` : ''}${b.state === 'receiving' ? '<p class="hint">Your browser is still sending files. Finish or stop that transfer before approving. You can dismiss a partial batch below.</p>' : ''}${b.state === 'pending' ? '<label class="checkbox-label"><input type="checkbox" id="mw-select-all" checked> Select all models in this batch</label>' : ''}<div class="mw-items">${b.items.map((m) => `<label class="mw-import-row">${b.state === 'pending' ? `<input type="checkbox" class="mw-item-check" value="${m.id}" checked>` : ''}<div class="mw-photo">${m.image_url ? `<img src="/api/makerworld/batches/${id}/photo/${m.id}" alt="${escapeHTML(m.title)}" loading="lazy">` : '⬡'}</div><div><strong>${escapeHTML(m.title)}</strong><small>${escapeHTML(m.creator || m.provider || 'MakerWorld creator')} · ${escapeHTML(m.collections.map((c) => c.name).join(' · '))}</small><small>${m.existing_model_id ? 'Already in Forma · adds memberships and new files, keeps your edits' : 'New catalog entry'}${m.has_file ? ' · model file ready' : ' · catalog only'}</small></div></label>`).join('')}</div><div class="dialog-footer">${['pending', 'receiving'].includes(b.state) ? '<button id="mw-dismiss-batch" class="button secondary">Dismiss batch</button>' : ''}${b.state === 'pending' ? '<button id="mw-apply-batch" class="button primary">Add selected to library</button>' : ''}</div></div>`;
    const all = $('#mw-select-all');
    if (all) all.onchange = () => $$('.mw-item-check').forEach((i) => (i.checked = all.checked));
    const dismiss = $('#mw-dismiss-batch');
    if (dismiss)
      dismiss.onclick = async () => {
        try {
          await api('/api/makerworld/batches/' + id, { method: 'DELETE' });
          $('#mw-batch-detail').innerHTML = '';
          await loadMakerWorld();
          notify('Import batch dismissed.');
        } catch (e) {
          $('#mw-message').textContent = e.message;
        }
      };
    const apply = $('#mw-apply-batch');
    if (apply)
      apply.onclick = async () => {
        const ids = $$('.mw-item-check:checked').map((i) => i.value);
        if (!ids.length) {
          $('#mw-message').textContent = 'Choose at least one model.';
          return;
        }
        apply.disabled = true;
        $('#mw-message').textContent = '';
        try {
          const result = await api('/api/makerworld/batches/' + id + '/apply', {
            method: 'POST',
            body: JSON.stringify({ ids }),
          });
          await refresh();
          await loadMakerWorld();
          await openMakerWorldBatch(id);
          notify(
            result.created +
              ' models added · ' +
              result.linked +
              ' existing models linked · ' +
              result.files +
              ' files attached.'
          );
          if (result.photos_queued) {
            setTimeout(refresh, 3000);
            setTimeout(refresh, 12000);
          }
        } catch (e) {
          $('#mw-message').textContent = e.message;
          apply.disabled = false;
        }
      };
    $('#mw-batch-detail').scrollIntoView({ behavior: 'smooth', block: 'start' });
  } catch (error) {
    $('#mw-message').textContent = error.message;
  }
}
$('#makerworld-button').onclick = () => openMakerWorld();
$('#mw-refresh').onclick = async () => {
  try {
    await loadMakerWorld();
    if (mwBatchId) await openMakerWorldBatch(mwBatchId);
  } catch (e) {
    $('#mw-message').textContent = e.message;
  }
};
$('#mw-create-code').onclick = async () => {
  try {
    const result = await api('/api/makerworld/pair-code', { method: 'POST', body: '{}' });
    $('#mw-pair-code').value = result.code;
    $('#mw-pairing').hidden = false;
  } catch (e) {
    $('#mw-message').textContent = e.message;
  }
};
$('#mw-copy-code').onclick = async () => {
  const input = $('#mw-pair-code');
  input.select();
  try {
    await navigator.clipboard.writeText(input.value);
    notify('Pairing code copied.');
  } catch {
    $('#mw-message').textContent = 'The pairing code is selected. Press Ctrl+C or Cmd+C to copy it.';
  }
};
const mwSettings = document.createElement('div');
mwSettings.className = 'settings-card';
mwSettings.innerHTML =
  '<h3>Import from website</h3><p>Bring model catalogs and collections from MakerWorld, Printables, Cults3D, and Thangs into Forma.</p><button class="button secondary" id="mw-settings-open">Open website import</button>';
$('#settings').append(mwSettings);
$('#mw-settings-open').onclick = () => {
  $('#settings').close();
  openMakerWorld();
};
const incomingMw = new URLSearchParams(location.search).get('makerworldBatch');
if (incomingMw && /^[a-f0-9-]{36}$/.test(incomingMw)) openMakerWorld(incomingMw);
$('#mw-bookmarklet').onclick = (e) => {
  e.preventDefault();
  $('#mw-message').textContent =
    'Drag this button to Safari’s Favorites bar. Then open MakerWorld and use the bookmark there.';
};
$('#mw-json-import').onchange = async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  try {
    if (file.size > 5 * 1024 * 1024) throw Error('Choose a catalog JSON export smaller than 5 MB.');
    const payload = JSON.parse(await file.text());
    if (
      !payload ||
      !['forma-makerworld-v1', 'forma-websites-v1'].includes(payload.format) ||
      !Array.isArray(payload.items)
    )
      throw Error('Choose a Forma website JSON export.');
    payload.has_files = false;
    const batch = await api('/api/makerworld/browser-batch', { method: 'POST', body: JSON.stringify(payload) });
    await loadMakerWorld();
    await openMakerWorldBatch(batch.id);
  } catch (error) {
    $('#mw-message').textContent = error.message;
  } finally {
    e.target.value = '';
  }
};
const mwBridgeNonce = new URLSearchParams(location.search).get('makerworldBridge');
if (mwBridgeNonce && /^[a-f0-9-]{36}$/.test(mwBridgeNonce)) {
  openMakerWorld();
  if (!window.opener) {
    $('#mw-message').textContent =
      'Safari isolated the website tab. Use Export selected JSON in its bookmark panel, then Import website JSON here.';
  } else {
    let received = false;
    const allowed = [
      'https://makerworld.com',
      'https://www.makerworld.com',
      'https://printables.com',
      'https://www.printables.com',
      'https://cults3d.com',
      'https://www.cults3d.com',
      'https://thangs.com',
      'https://www.thangs.com',
    ];
    const ready = (origin) =>
      window.opener.postMessage({ type: 'forma-makerworld-ready', nonce: mwBridgeNonce }, origin);
    window.addEventListener('message', async (event) => {
      if (event.source !== window.opener || !allowed.includes(event.origin) || event.data?.nonce !== mwBridgeNonce)
        return;
      if (event.data.type === 'forma-makerworld-ping') {
        ready(event.origin);
        return;
      }
      if (event.data.type !== 'forma-makerworld-import' || received) return;
      received = true;
      try {
        const payload = event.data.payload,
          files = Array.isArray(event.data.files) ? event.data.files : [];
        if (
          files.length > 500 ||
          files.some((f) => !(f.blob instanceof Blob) || f.blob.size > 256 * 1024 * 1024) ||
          files.reduce((n, f) => n + f.blob.size, 0) > 256 * 1024 * 1024
        )
          throw Error('Invalid or oversized model-file transfer.');
        const batch = await api('/api/makerworld/browser-batch', {
          method: 'POST',
          body: JSON.stringify({ ...payload, has_files: files.length > 0 }),
        });
        const warnings = [...(payload.warnings || [])];
        for (const f of files) {
          try {
            const body = new FormData();
            body.append('file', f.blob, f.name || 'MakerWorld.3mf');
            await api('/api/makerworld/batches/' + batch.id + '/files/' + encodeURIComponent(f.id), {
              method: 'POST',
              body,
            });
          } catch (error) {
            warnings.push('Model ' + f.id + ': ' + error.message);
          }
        }
        if (files.length)
          await api('/api/makerworld/batches/' + batch.id + '/finish', {
            method: 'POST',
            body: JSON.stringify({ warnings }),
          });
        await loadMakerWorld();
        await openMakerWorldBatch(batch.id);
        event.source.postMessage({ type: 'forma-makerworld-ack', nonce: mwBridgeNonce, ok: true }, event.origin);
      } catch (error) {
        $('#mw-message').textContent = error.message;
        event.source.postMessage(
          { type: 'forma-makerworld-ack', nonce: mwBridgeNonce, ok: false, error: error.message },
          event.origin
        );
      }
    });
    allowed.forEach(ready);
  }
}

if (new URLSearchParams(location.search).get('makerworld') === '1') openMakerWorld();

$('#website-bookmarklet').onclick = (e) => {
  e.preventDefault();
  $('#mw-message').textContent =
    'Drag this button to Safari’s Favorites bar, then use it on Printables, Cults3D, or Thangs.';
};
