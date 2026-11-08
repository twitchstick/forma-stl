// Sidebar groups, the import badge, and a Library settings dialog without duplicate links.
const fs = require('node:fs'),
  path = require('node:path'),
  assert = require('node:assert/strict'),
  { JSDOM, VirtualConsole } = require('jsdom');
const root = path.resolve(__dirname, '..'),
  errors = [],
  vc = new VirtualConsole();
vc.on('jsdomError', (e) => errors.push(e.message));
const dom = new JSDOM(fs.readFileSync(path.join(root, 'static/index.html'), 'utf8'), {
    url: 'http://forma.test/',
    runScripts: 'outside-only',
    virtualConsole: vc,
  }),
  w = dom.window,
  $ = (s) => w.document.querySelector(s),
  $$ = (s) => [...w.document.querySelectorAll(s)];
w.matchMedia = () => ({ matches: false, addEventListener() {} });
w.scrollTo = () => {}; // jsdom does not implement scrolling
w.HTMLDialogElement.prototype.showModal = function () {
  this.open = true;
};
w.HTMLDialogElement.prototype.close = function () {
  this.open = false;
  this.dispatchEvent(new w.Event('close'));
};
let pending = 0;
const response = (value) => ({ ok: true, status: 200, json: async () => value });
w.fetch = async (url) => {
  if (url === '/api/models') return response([]);
  if (url === '/api/watch') return response({ pending, enabled: true, interval: 900 });
  if (url === '/api/account')
    return response({ username: 'owner', display_name: '', authentication_enabled: true, avatar_url: '' });
  if (url === '/api/makerworld/batches' || url === '/api/makerworld/devices') return response([]);
  if (url === '/api/collection-schedules') return response({ enabled: false, collections: [] });
  return response([]);
};
const settle = () => new Promise((resolve) => setTimeout(resolve, 30));

(async () => {
  // Load every script the page loads, in page order.
  const scripts = [...w.document.querySelectorAll('script[src]')].map((s) => s.getAttribute('src'));
  w.eval(
    scripts
      .filter((src) => src !== '/static/theme.js')
      .map((src) => fs.readFileSync(path.join(root, src.replace(/^\//, '')), 'utf8'))
      .join('\n;\n')
  );
  await settle();

  // Tools are grouped under Import and Settings, closed by default.
  assert.deepEqual(
    $$('.nav-group > summary').map((s) => s.firstChild.textContent),
    ['Import', 'Settings']
  );
  assert.deepEqual(
    $$('.nav-group').map((g) => g.open),
    [false, false]
  );
  assert.deepEqual(
    $$('#import-group .nav-item').map((b) => b.id),
    ['makerworld-button', 'scan-button', 'review-button', 'collection-schedules-button']
  );
  assert.deepEqual(
    $$('#settings-group .nav-item').map((b) => b.id),
    ['printers-button', 'duplicates-button', 'account-button', 'settings-button']
  );

  // With nothing waiting, no badge; waiting imports show on the collapsed Import heading.
  assert.equal($('#import-badge').hidden, true);
  $('#review-count').textContent = '3';
  await settle();
  assert.equal($('#import-badge').hidden, false);
  assert.equal($('#import-badge').textContent, '3');
  $('#review-count').textContent = '0';
  await settle();
  assert.equal($('#import-badge').hidden, true);

  // Open state is remembered.
  $('#settings-group').open = true;
  $('#settings-group').dispatchEvent(new w.Event('toggle'));
  assert.equal(w.localStorage.getItem('forma-nav-settings-group'), 'open');

  // Library settings holds only its own settings, not copies of sidebar links.
  $('#settings-button').click();
  assert.equal($('#settings').open, true);
  assert.deepEqual(
    $$('#settings .settings-card h3').map((h) => h.textContent),
    ['Appearance', 'Backup']
  );

  assert.deepEqual(errors, []);
  console.log('Sidebar client test passed.');
  dom.window.close();
})().catch((error) => {
  console.error(error);
  dom.window.close();
  process.exitCode = 1;
});
