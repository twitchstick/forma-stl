const fs = require('node:fs'),
  path = require('node:path'),
  assert = require('node:assert/strict'),
  { JSDOM, VirtualConsole } = require('jsdom');
const root = path.resolve(__dirname, '..'),
  errors = [],
  vc = new VirtualConsole();
vc.on('jsdomError', (e) => errors.push(e.message));
const dom = new JSDOM(fs.readFileSync(path.join(root, 'static/index.html'), 'utf8'), {
    url: 'http://forma.test',
    runScripts: 'outside-only',
    virtualConsole: vc,
  }),
  w = dom.window,
  $ = (s) => w.document.querySelector(s);
w.matchMedia = () => ({ matches: true, addEventListener() {} });
w.HTMLDialogElement.prototype.showModal = function () {
  this.open = true;
};
w.HTMLDialogElement.prototype.close = function () {
  this.open = false;
  this.dispatchEvent(new w.Event('close'));
};
const state = {
  enabled: true,
  time: '04:00',
  timezone: 'America/New_York',
  pending: 0,
  running: false,
  collections: [
    {
      id: 'one',
      name: 'Workshop',
      provider: 'makerworld',
      url: 'https://makerworld.com/en/collections/10',
      enabled: true,
      state: 'waiting',
      message: '',
      next_check: '2026-11-04T09:00:00+00:00',
      last_checked: '',
      last_batch_id: '',
    },
  ],
};
let checks = 0;
const response = (value) => ({ ok: true, json: async () => value });
w.fetch = async (url, opt = {}) => {
  const p = typeof opt.body === 'string' ? JSON.parse(opt.body) : {};
  if (url === '/api/models') return response([]);
  if (url === '/api/watch') return response({ pending: 0 });
  if (url === '/api/collection-schedule' && opt.method === 'PATCH') {
    Object.assign(state, p);
    return response(state);
  }
  if (url === '/api/collection-schedule') return response(state);
  if (url === '/api/collection-schedule/collections' && opt.method === 'POST') {
    state.collections.push({
      id: 'two',
      provider: 'printables',
      ...p,
      enabled: true,
      state: 'waiting',
      next_check: '2026-11-04T09:00:00+00:00',
    });
    return response({ id: 'two' });
  }
  if (url === '/api/collection-schedule/collections/one') {
    Object.assign(state.collections[0], p);
    return response({ ok: true });
  }
  if (url === '/api/collection-schedule/check') {
    checks++;
    state.collections[0].state = 'partial';
    state.collections[0].message = 'Public page only. Refresh in Safari.';
    state.collections[0].last_batch_id = 'batch1';
    return response({ queued: 1 });
  }
  throw Error('Unexpected request ' + url);
};
const settle = async () => {
  for (let i = 0; i < 4; i++) await new Promise((r) => setImmediate(r));
};
function submit(form) {
  form.dispatchEvent(
    new w.SubmitEvent('submit', {
      bubbles: true,
      cancelable: true,
      submitter: form.querySelector('button[type=submit]'),
    })
  );
}
(async () => {
  w.eval(
    fs.readFileSync(path.join(root, 'static/app.js'), 'utf8') +
      '\nfunction openMakerWorld(id){window.reviewed=id;}\n' +
      fs.readFileSync(path.join(root, 'static/collection-schedule.js'), 'utf8')
  );
  await settle();
  $('#collection-schedules-button').click();
  await settle();
  assert.equal($('#schedule-time').value, '04:00');
  assert.match($('#collection-schedules-list').textContent, /Workshop/);
  $('#schedule-time').value = '05:30';
  $('#schedule-timezone').value = 'America/Chicago';
  submit($('#collection-schedule-form'));
  await settle();
  assert.equal(state.time, '05:30');
  assert.equal(state.timezone, 'America/Chicago');
  const form = $('#track-collection-form');
  form.elements.name.value = 'Tools';
  form.elements.url.value = 'https://printables.com/collection/12';
  submit(form);
  await settle();
  assert.equal(state.collections.length, 2);
  assert.match($('#collection-schedules-list').textContent, /Tools/);
  assert.equal(form.elements.name.value, '');
  const toggle = $('[data-track-enabled="one"]');
  toggle.checked = false;
  toggle.dispatchEvent(new w.Event('change'));
  await settle();
  assert.equal(state.collections[0].enabled, false);
  $('[data-track-check="one"]').click();
  await settle();
  assert.equal(checks, 1);
  assert.match($('#collection-schedules-list').textContent, /Partial public check/);
  assert.match($('#collection-schedules-list').textContent, /Refresh in Safari/);
  $('[data-track-review="batch1"]').click();
  assert.equal(w.reviewed, 'batch1');
  assert.equal($('#collection-schedule-dialog').open, false);
  assert.deepEqual(errors, []);
  console.log(
    'PASS: daily/time-zone preferences, adding a tracked collection, per-collection pause, partial-check status and review navigation.'
  );
  dom.window.close();
})().catch((e) => {
  console.error(e);
  dom.window.close();
  process.exitCode = 1;
});
