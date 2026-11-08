'use strict';
let sourcePoll = null,
  sourceGeneration = 0;
function renderSourcePanel(m, status, mount) {
  const job = status.job,
    working = job && ['queued', 'running'].includes(job.state),
    photos = status.photos || [];
  mount.innerHTML = `<div class="printer-title"><h3>Source details & photos</h3><button class="button secondary" id="retry-source-import" ${!m.source_url || working ? 'disabled' : ''}>${working ? 'Import in progress…' : 'Retry source import'}</button></div><dl class="print-settings"><dt>License / terms</dt><dd>${escapeHTML(m.license || 'Not supplied')}${m.license_url ? ` <a href="${escapeHTML(m.license_url)}" target="_blank" rel="noopener noreferrer">View license ↗</a>` : ''}</dd><dt>Attribution</dt><dd>${escapeHTML(m.attribution || 'Not supplied')}</dd></dl><p class="hint">Retry fills in missing details and photos without changing your edits.</p>${!m.source_url ? '<p class="hint">Add the model’s page link in Edit model to fetch its details.</p>' : ''}${job ? `<div class="source-progress" role="status"><strong>${escapeHTML({ queued: 'Queued', running: 'Importing', complete: 'Complete', partial: 'Finished with notes', interrupted: 'Interrupted', failed: 'Needs attention' }[job.state] || job.state)}</strong><p>${escapeHTML(job.phase)}${job.total ? ' · ' + job.done + ' / ' + job.total + ' photos checked' : ''}</p>${working ? `<progress aria-label="Source import progress" ${job.total ? `value="${job.done}" max="${job.total}"` : ''}></progress>` : ''}${job.filled?.length ? `<p>Filled: ${escapeHTML(job.filled.map((f) => f.replaceAll('_', ' ')).join(', '))}</p>` : ''}${job.notes?.length ? `<ul>${job.notes.map((n) => `<li>${escapeHTML(n)}</li>`).join('')}</ul>` : ''}</div>` : ''}${photos.length ? `<details><summary>Source photos · ${photos.filter((p) => p.state === 'imported').length} / ${photos.length} imported</summary><div class="source-photo-status">${photos.map((p, i) => `<div>${p.file_id ? `<img src="/api/files/${p.file_id}" alt="Source photo ${i + 1}" loading="lazy">` : ''}<span>Photo ${i + 1} · ${escapeHTML(p.state)}${p.error ? `<small>${escapeHTML(p.error)}</small>` : ''}</span></div>`).join('')}</div></details>` : ''}`;
  $('#retry-source-import').onclick = async () => {
    const button = $('#retry-source-import');
    button.disabled = true;
    try {
      await api('/api/models/' + m.id + '/source-import', { method: 'POST', body: '{}' });
      loadSourceImport(m.id);
    } catch (error) {
      notify(error.message);
      button.disabled = false;
    }
  };
}
async function loadSourceImport(id) {
  clearTimeout(sourcePoll);
  const generation = ++sourceGeneration,
    m = models.find((m) => m.id === id);
  if (!m) return;
  let mount = $('#source-import-panel');
  if (!mount) {
    mount = document.createElement('section');
    mount.id = 'source-import-panel';
    const body = $('#detail-content .detail-body');
    body.insertBefore(mount, body.querySelector('.file-heading'));
  }
  mount.innerHTML = '<h3>Source details & photos</h3><p class="hint">Loading source import status…</p>';

  async function poll() {
    try {
      const status = await api('/api/models/' + id + '/source-import');
      if (generation !== sourceGeneration || !mount.isConnected || $('#detail').hidden) return;
      const working = status.job && ['queued', 'running'].includes(status.job.state);
      const fresh = status.model,
        old = models.find((m) => m.id === id);
      if (!working && fresh && JSON.stringify(fresh) !== JSON.stringify(old)) {
        models = models.map((m) => (m.id === id ? fresh : m));
        render();
        openDetail(id, { push: false });
        return;
      }
      renderSourcePanel(models.find((m) => m.id === id) || m, status, mount);
      if (working) sourcePoll = setTimeout(poll, 1500);
    } catch (error) {
      if (generation === sourceGeneration && mount.isConnected)
        mount.innerHTML = '<h3>Source details & photos</h3><p class="hint">' + escapeHTML(error.message) + '</p>';
    }
  }
  await poll();
}
$('#detail').addEventListener('close', () => {
  clearTimeout(sourcePoll);
  sourceGeneration++;
});
