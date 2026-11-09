// Opening the Import inbox looks for new files without being asked.
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
  $ = (s) => w.document.querySelector(s);
w.matchMedia = () => ({ matches: false, addEventListener() {} });
w.scrollTo = () => {};
w.HTMLDialogElement.prototype.showModal = function () {
  this.open = true;
};
w.HTMLDialogElement.prototype.close = function () {
  this.open = false;
};

const item = (id, title) => ({
  id,
  kind: 'new',
  folder_key: title,
  payload: { title, files: [{}], model_id: null },
});
let items = [item(1, 'Old folder')],
  scans = 0,
  reviewedAt = null,
  scanFails = false,
  release = null;
const response = (value, status = 200) => ({ ok: status < 400, status, json: async () => value });
w.fetch = async (url, options = {}) => {
  if (url === '/api/watch/scan') {
    scans++;
    if (release) await new Promise((resolve) => (release = resolve));
    if (scanFails) return response({ error: 'The model share is unavailable.' }, 400);
    items = [item(2, 'New folder'), ...items.filter((i) => i.id !== 2)];
    return response({});
  }
  if (url === '/api/watch/review' && options.method === 'POST') {
    reviewedAt = scans;
    return response({ reviewed: JSON.parse(options.body).ids.length });
  }
  if (url === '/api/watch/review') return response({ items });
  if (url === '/api/watch')
    return response({ enabled: true, interval: 900, last_scan: '', error: '', pending: items.length });
  if (url === '/api/models') return response([]);
  return response([]);
};
const settle = (ms = 30) => new Promise((resolve) => setTimeout(resolve, ms));
const titles = () => [...w.document.querySelectorAll('#review-items strong')].map((e) => e.textContent);
const hold = () => (release = () => {});
const letGo = async () => {
  const go = release;
  release = null;
  go();
  await settle();
};

(async () => {
  w.eval(fs.readFileSync(path.join(root, 'static/app.js'), 'utf8'));
  await settle();

  // What is already known appears at once, while the scan is still running.
  hold();
  $('#review-button').click();
  await settle();
  assert.equal($('#review-dialog').open, true);
  assert.deepEqual(titles(), ['Old folder']);
  assert.equal(scans, 1);
  assert.equal($('#watch-scan-now').disabled, true);
  assert.equal($('#watch-scan-now').textContent, 'Scanning…');
  assert.match($('#watch-last-scan').textContent, /Checking your folders/);

  // Ticks made during the scan survive the refresh.
  w.document.querySelector('.review-check').checked = true;

  // Scan now does not start a second scan on top.
  $('#watch-scan-now').click();
  await settle();
  assert.equal(scans, 1);

  await letGo();
  assert.deepEqual(titles(), ['New folder', 'Old folder']);
  assert.equal($('.review-check[value="1"]').checked, true);
  assert.equal($('.review-check[value="2"]').checked, false);
  assert.equal($('#review-select-all').checked, false);
  assert.equal($('#watch-scan-now').disabled, false);
  assert.equal($('#watch-scan-now').textContent, 'Scan now');
  assert.match($('#watch-last-scan').textContent, /Last checked/);

  // Approving during a scan waits for it, because the server would refuse.
  hold();
  $('#watch-scan-now').click();
  await settle();
  const scansBefore = scans;
  reviewedAt = null;
  $('#approve-review').click();
  await settle();
  assert.equal(reviewedAt, null);
  await letGo();
  assert.equal(reviewedAt, scansBefore);

  // A failed scan says why and still shows the list.
  $('#review-dialog').close();
  scanFails = true;
  $('#review-button').click();
  await settle();
  assert.match($('#review-message').textContent, /share is unavailable/);
  assert.equal(titles().length, 2);
  assert.equal($('#watch-scan-now').disabled, false);

  assert.deepEqual(errors, []);
  console.log('Inbox client test passed.');
  dom.window.close();
})().catch((error) => {
  console.error(error);
  dom.window.close();
  process.exitCode = 1;
});
