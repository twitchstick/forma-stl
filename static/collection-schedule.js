'use strict';
let scheduleTimer = null,
  scheduleData = null,
  scheduleDirty = false;
const scheduleDialog = $('#collection-schedule-dialog');
function scheduleWhen(value, zone) {
  if (!value) return 'Not yet';
  try {
    return new Date(value).toLocaleString(undefined, { timeZone: zone, dateStyle: 'medium', timeStyle: 'short' });
  } catch {
    return value;
  }
}
async function loadCollectionSchedules() {
  const s = await api('/api/collection-schedule');
  scheduleData = s;
  if (!scheduleDirty) {
    $('#schedule-enabled').checked = s.enabled;
    $('#schedule-time').value = s.time;
    $('#schedule-timezone').value = s.timezone;
  }
  $('#schedule-summary').textContent =
    (s.enabled ? 'Daily checks enabled' : 'Daily checks paused') +
    ' · ' +
    s.time +
    ' · ' +
    s.timezone +
    '. ' +
    s.pending +
    ' website import batch' +
    (s.pending === 1 ? '' : 'es') +
    ' awaiting review.';
  $('#collection-schedules-list').innerHTML =
    s.collections
      .map(
        (c) =>
          `<article class="settings-card tracked-collection"><div class="printer-title"><h3>${escapeHTML(c.name)}</h3><label class="checkbox-label"><input type="checkbox" data-track-enabled="${c.id}" ${c.enabled ? 'checked' : ''}> Daily check</label></div><small>${escapeHTML(c.provider)} · ${escapeHTML((c.enabled ? '' : 'Daily paused · ') + ({ waiting: 'Scheduled', queued: 'Queued', checking: 'Checking now', checked: 'Checked', partial: 'Partial public check', needs_link: 'Collection link needed', needs_browser: 'Browser refresh needed', interrupted: 'Interrupted' }[c.state] || c.state))}</small><p>${escapeHTML(c.message || 'The next check will look for newly added models.')}</p><div class="schedule-dates"><span>Last check: ${escapeHTML(scheduleWhen(c.last_checked, s.timezone))}</span><span>Next check: ${s.enabled && c.enabled && c.url ? escapeHTML(scheduleWhen(c.next_check, s.timezone)) : 'Paused or needs a collection link'}</span></div><form class="collection-link-form" data-track-link="${c.id}"><label>Original collection URL<input type="url" name="url" required value="${escapeHTML(c.url)}" placeholder="https://…/collection/…" aria-label="Collection URL for ${escapeHTML(c.name)}"></label><button class="button secondary" type="submit">Save link</button></form>${!c.url ? '<p class="hint">Older browser imports did not retain the collection link. Paste it once to schedule public checks. Signed-in saved/liked lists still need the Safari importer.</p>' : ''}<div class="schedule-actions"><button class="button secondary" data-track-check="${c.id}" ${!c.url || ['queued', 'checking'].includes(c.state) ? 'disabled' : ''}>Check now</button>${c.last_batch_id ? `<button class="button primary" data-track-review="${c.last_batch_id}">Review discovered models</button>` : ''}${c.url ? `<a class="text-button" href="${escapeHTML(c.url)}" target="_blank" rel="noopener noreferrer">Open collection ↗</a>` : ''}</div></article>`
      )
      .join('') ||
    '<p class="hint">Imported website collections will appear here. You can also add a public collection below; its first check will stage the models you have not imported.</p>';
  $$('[data-track-enabled]').forEach(
    (input) =>
      (input.onchange = async () => {
        try {
          await api('/api/collection-schedule/collections/' + input.dataset.trackEnabled, {
            method: 'PATCH',
            body: JSON.stringify({ enabled: input.checked }),
          });
          await loadCollectionSchedules();
        } catch (e) {
          $('#schedule-message').textContent = e.message;
          input.checked = !input.checked;
        }
      })
  );
  $$('[data-track-link]').forEach(
    (form) =>
      (form.onsubmit = async (e) => {
        e.preventDefault();
        const b = e.submitter;
        b.disabled = true;
        try {
          await api('/api/collection-schedule/collections/' + form.dataset.trackLink, {
            method: 'PATCH',
            body: JSON.stringify({ url: form.elements.url.value }),
          });
          await loadCollectionSchedules();
          notify('Collection link saved.');
        } catch (error) {
          $('#schedule-message').textContent = error.message;
        } finally {
          b.disabled = false;
        }
      })
  );
  $$('[data-track-check]').forEach((b) => (b.onclick = () => checkCollections(b.dataset.trackCheck)));
  $$('[data-track-review]').forEach(
    (b) =>
      (b.onclick = () => {
        scheduleDialog.close();
        openMakerWorld(b.dataset.trackReview);
      })
  );
  clearTimeout(scheduleTimer);
  if (scheduleDialog.open)
    scheduleTimer = setTimeout(
      () => loadCollectionSchedules().catch((e) => ($('#schedule-message').textContent = e.message)),
      s.running ? 2000 : 30000
    );
}
async function openCollectionSchedules() {
  scheduleDirty = false;
  if (!scheduleDialog.open) scheduleDialog.showModal();
  $('#schedule-message').textContent = '';
  try {
    await loadCollectionSchedules();
  } catch (e) {
    $('#schedule-message').textContent = e.message;
  }
}
async function checkCollections(id = null) {
  try {
    const result = await api('/api/collection-schedule/check', {
      method: 'POST',
      body: JSON.stringify(id ? { id } : {}),
    });
    await loadCollectionSchedules();
    notify(result.queued + ' collection check' + (result.queued === 1 ? '' : 's') + ' queued.');
  } catch (error) {
    $('#schedule-message').textContent = error.message;
  }
}
$('#collection-schedules-button').onclick = openCollectionSchedules;
$('#schedule-refresh').onclick = () =>
  loadCollectionSchedules().catch((e) => ($('#schedule-message').textContent = e.message));
$('#schedule-check-all').onclick = () => checkCollections();
$('#collection-schedule-form').oninput = () => {
  scheduleDirty = true;
};
$('#collection-schedule-form').onsubmit = async (e) => {
  e.preventDefault();
  const b = e.submitter;
  b.disabled = true;
  try {
    await api('/api/collection-schedule', {
      method: 'PATCH',
      body: JSON.stringify({
        enabled: $('#schedule-enabled').checked,
        time: $('#schedule-time').value,
        timezone: $('#schedule-timezone').value,
      }),
    });
    scheduleDirty = false;
    await loadCollectionSchedules();
    notify('Daily collection schedule saved.');
  } catch (error) {
    $('#schedule-message').textContent = error.message;
  } finally {
    b.disabled = false;
  }
};
$('#track-collection-form').onsubmit = async (e) => {
  e.preventDefault();
  const b = e.submitter,
    form = e.currentTarget;
  b.disabled = true;
  try {
    await api('/api/collection-schedule/collections', {
      method: 'POST',
      body: JSON.stringify(Object.fromEntries(new FormData(form))),
    });
    form.reset();
    await loadCollectionSchedules();
    notify('Collection tracked. Check now or wait for the daily schedule.');
  } catch (error) {
    $('#schedule-message').textContent = error.message;
  } finally {
    b.disabled = false;
  }
};
scheduleDialog.addEventListener('close', () => clearTimeout(scheduleTimer));
const scheduleSettings = document.createElement('div');
scheduleSettings.className = 'settings-card';
scheduleSettings.innerHTML =
  '<h3>Daily collection checks</h3><p>Track website collections and review newly discovered models. Public collections can be checked from Unraid; signed-in collections may need a Safari refresh.</p><button class="button secondary" id="schedule-settings-open">Collection schedules</button>';
$('#settings').append(scheduleSettings);
$('#schedule-settings-open').onclick = () => {
  $('#settings').close();
  openCollectionSchedules();
};
$('#schedule-import-open').onclick = () => {
  $('#makerworld-dialog').close();
  openCollectionSchedules();
};
