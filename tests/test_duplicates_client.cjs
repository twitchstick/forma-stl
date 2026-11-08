// Duplicate warnings when dropping files, and the browser's SHA-256.
const fs = require('node:fs'),
  path = require('node:path'),
  crypto = require('node:crypto'),
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
w.scrollTo = () => {}; // jsdom does not implement scrolling
w.HTMLDialogElement.prototype.showModal = function () {
  this.open = true;
};
w.HTMLDialogElement.prototype.close = function () {
  this.open = false;
  this.dispatchEvent(new w.Event('close'));
};
// jsdom's Blob has no arrayBuffer(); every real browser does.
w.Blob.prototype.arrayBuffer = function () {
  return new Promise((resolve) => {
    const reader = new w.FileReader();
    reader.onload = () => resolve(reader.result);
    reader.readAsArrayBuffer(this);
  });
};

const sha = (text) => crypto.createHash('sha256').update(text).digest('hex');
const base = {
  creator: '',
  source_url: '',
  description: '',
  tags: [],
  collections: [],
  collection: '',
  status: 'Saved',
  favorite: false,
  cover_id: '',
  created_at: '2026-11-08',
};
let models = [{ ...base, id: 'benchy', title: 'Benchy', files: [] }],
  existing = {},
  lookups = [],
  created = [],
  uploads = [],
  lookupDelay = 0;
const response = (value, status = 200) => ({ ok: status < 400, status, json: async () => value });
w.fetch = async (url, options = {}) => {
  if (url === '/api/files/lookup') {
    const files = JSON.parse(options.body).files;
    lookups.push(files);
    if (lookupDelay) await new Promise((resolve) => setTimeout(resolve, lookupDelay));
    return response({ matches: Object.fromEntries(files.map((f) => [f.sha256, existing[f.sha256] || []])) });
  }
  if (url === '/api/models' && options.method === 'POST') {
    const model = { ...base, id: 'new' + (created.length + 1), ...JSON.parse(options.body), files: [] };
    created.push(model);
    models = [model, ...models];
    return response(model, 201);
  }
  if (url === '/api/models') return response(models);
  if (url === '/api/watch') return response({ pending: 0 });
  return response([]);
};
w.XMLHttpRequest = class {
  constructor() {
    this.upload = {};
  }
  open(method, url) {
    this.url = url;
  }
  setRequestHeader() {}
  send(body) {
    uploads.push({ url: this.url, names: body.getAll('files').map((f) => f.name) });
    setTimeout(() => {
      this.status = 200;
      this.response = {};
      this.onload();
    }, 1);
  }
};
const settle = (ms = 40) => new Promise((resolve) => setTimeout(resolve, ms));
const file = (name, content = name) => new w.File([content], name);
function drop(files) {
  const event = new w.Event('drop', { bubbles: true, cancelable: true });
  Object.defineProperty(event, 'dataTransfer', { value: { types: ['Files'], files } });
  w.document.body.dispatchEvent(event);
}
const click = (element) =>
  element.dispatchEvent(new w.MouseEvent('click', { bubbles: true, cancelable: true, button: 0 }));

(async () => {
  w.eval(
    ['app.js', 'quick-add.js'].map((n) => fs.readFileSync(path.join(root, 'static', n), 'utf8')).join('\n') +
      '\nwindow.sha256File = sha256File;'
  );
  await settle();

  // SHA-256 must match the standard at every padding and chunk boundary.
  const chunk = 4 * 1024 * 1024;
  for (const size of [0, 1, 55, 56, 57, 63, 64, 65, 119, 120, 1000, chunk - 1, chunk, chunk + 1, chunk * 2 + 70]) {
    const data = crypto.createHash('sha512').update(String(size)).digest();
    const body = Buffer.alloc(size);
    for (let i = 0; i < size; i++) body[i] = data[i % data.length] ^ (i & 255);
    const fake = {
      size,
      slice: (a, b) => ({
        arrayBuffer: async () => body.buffer.slice(body.byteOffset + a, body.byteOffset + Math.min(b, size)),
      }),
    };
    assert.equal(await w.sha256File(fake), crypto.createHash('sha256').update(body).digest('hex'), `size ${size}`);
  }

  // No duplicates: nothing extra is shown.
  drop([file('fresh.stl'), file('photo.png')]);
  await settle();
  assert.equal($('#quick-duplicates').hidden, true);
  assert.equal($('#quick-save').textContent, 'Add model');
  // Only model files are fingerprinted, and the size is sent with the fingerprint.
  assert.deepEqual(lookups[0], [{ sha256: sha('fresh.stl'), size: 9 }]);
  $('#quick-add').close();

  // Some files are duplicates: marked, and skipped by default.
  existing[sha('hull.stl')] = [{ model_id: 'benchy', title: 'Benchy', name: 'hull.stl' }];
  drop([file('hull.stl'), file('mast.stl'), file('photo.png')]);
  await settle();
  assert.equal($('#quick-duplicates').hidden, false);
  assert.match($('#quick-files').children[0].textContent, /Already in Benchy/);
  assert.doesNotMatch($('#quick-files').children[1].textContent, /Already in/);
  assert.equal($('#quick-skip-duplicates').checked, true);
  assert.equal($('#quick-add-form').elements.title.value, 'mast'); // named after what will be added
  $('#quick-add-form').requestSubmit();
  await settle();
  assert.deepEqual(uploads.at(-1).names, ['mast.stl', 'photo.png']);
  assert.equal(created.length, 1);
  click($('[data-back]'));
  await settle();

  // Unticking the option uploads everything.
  drop([file('hull.stl'), file('mast2.stl')]);
  await settle();
  $('#quick-skip-duplicates').checked = false;
  $('#quick-add-form').requestSubmit();
  await settle();
  assert.deepEqual(uploads.at(-1).names, ['hull.stl', 'mast2.stl']);
  click($('[data-back]'));
  await settle();

  // Everything is a duplicate: offer the existing model, and Enter opens it.
  const uploadsBefore = uploads.length;
  drop([file('hull.stl')]);
  await settle();
  assert.match($('#quick-duplicates').textContent, /This file is already in your library/);
  assert.equal($('#quick-open-existing').textContent, 'Open Benchy');
  assert.equal(w.document.activeElement, $('#quick-open-existing'));
  assert.equal($('#quick-save').textContent, 'Add a copy anyway');
  assert.equal($('#quick-save').classList.contains('primary'), false);
  click($('#quick-open-existing'));
  await settle();
  assert.equal($('#quick-add').open, false);
  assert.equal(w.location.pathname, '/models/benchy');
  assert.equal(uploads.length, uploadsBefore);

  // On a model page: files it already has are skipped and reported honestly.
  drop([file('hull.stl')]);
  await settle();
  assert.equal(uploads.length, uploadsBefore);
  assert.match($('#toast').textContent, /hull\.stl is already on this model/);
  drop([file('hull.stl'), file('rudder.stl')]);
  await settle();
  assert.deepEqual(uploads.at(-1), { url: '/api/models/benchy/files', names: ['rudder.stl'] });
  assert.match($('#toast').textContent, /Added rudder\.stl to Benchy\. 1 already on this model\./);
  // A file that lives on a different model can still be attached here.
  existing[sha('shared.stl')] = [{ model_id: 'other', title: 'Other', name: 'shared.stl' }];
  drop([file('shared.stl')]);
  await settle();
  assert.deepEqual(uploads.at(-1).names, ['shared.stl']);
  click($('[data-back]'));
  await settle();

  // Pressing Enter before the check finishes: the warning wins, nothing is added yet.
  const createdBefore = created.length;
  lookupDelay = 60;
  drop([file('hull.stl')]);
  $('#quick-add-form').requestSubmit();
  await settle(120);
  lookupDelay = 0;
  assert.equal(created.length, createdBefore);
  assert.equal($('#quick-add').open, true);
  assert.equal($('#quick-open-existing').textContent, 'Open Benchy');
  // Choosing to add a copy still works, and only because it was asked for.
  $('#quick-add-form').requestSubmit();
  await settle();
  assert.equal(created.length, createdBefore + 1);
  assert.deepEqual(uploads.at(-1).names, ['hull.stl']);

  // If the check fails, adding still works.
  click($('[data-back]'));
  await settle();
  const realFetch = w.fetch;
  w.fetch = async (url, options) => {
    if (url === '/api/files/lookup') throw new Error('offline');
    return realFetch(url, options);
  };
  drop([file('unchecked.stl')]);
  await settle();
  assert.equal($('#quick-duplicates').hidden, true);
  $('#quick-add-form').requestSubmit();
  await settle();
  assert.deepEqual(uploads.at(-1).names, ['unchecked.stl']);

  assert.deepEqual(errors, []);
  console.log('Duplicates client test passed.');
  dom.window.close();
})().catch((error) => {
  console.error(error);
  dom.window.close();
  process.exitCode = 1;
});
