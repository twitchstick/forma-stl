// PDF instructions on the model page, and adding them by dropping.
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
  all = (s) => [...w.document.querySelectorAll(s)];
w.matchMedia = () => ({ matches: false, addEventListener() {} });
w.scrollTo = () => {};
w.HTMLDialogElement.prototype.showModal = function () {
  this.open = true;
};
w.HTMLDialogElement.prototype.close = function () {
  this.open = false;
  this.dispatchEvent(new w.Event('close'));
};
w.Blob.prototype.arrayBuffer = function () {
  return new Promise((resolve) => {
    const reader = new w.FileReader();
    reader.onload = () => resolve(reader.result);
    reader.readAsArrayBuffer(this);
  });
};

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
const models = [
  {
    ...base,
    id: 'crane',
    title: 'Crane',
    files: [
      { id: 'f1', name: 'boom.stl', size: 2048, kind: 'model' },
      { id: 'f2', name: 'Assembly <guide>.pdf', size: 3 * 1048576, kind: 'document' },
      { id: 'f3', name: 'photo.png', size: 10, kind: 'image' },
    ],
  },
  { ...base, id: 'vase', title: 'Vase', files: [{ id: 'f4', name: 'vase.stl', size: 1, kind: 'model' }] },
];
const uploads = [],
  lookups = [];
const response = (value, status = 200) => ({ ok: status < 400, status, json: async () => value });
w.fetch = async (url, options = {}) => {
  if (url === '/api/files/lookup') {
    const files = JSON.parse(options.body).files;
    lookups.push(files);
    return response({ matches: Object.fromEntries(files.map((f) => [f.sha256, []])) });
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
function drop(files) {
  const event = new w.Event('drop', { bubbles: true, cancelable: true });
  Object.defineProperty(event, 'dataTransfer', { value: { types: ['Files'], files } });
  w.document.body.dispatchEvent(event);
}
const headings = () => all('#detail-content .file-heading').map((h) => h.textContent);

(async () => {
  w.eval(['app.js', 'quick-add.js'].map((n) => fs.readFileSync(path.join(root, 'static', n), 'utf8')).join('\n'));
  await settle();

  // The card counts model files only.
  assert.match($('[data-open="crane"]').closest('.model-card').querySelector('.card-footer').textContent, /^1 file/);

  // The model page lists the PDF under Instructions, apart from the model files.
  $('[data-open="crane"]').click();
  await settle();
  assert.deepEqual(headings(), ['Instructions · 1', 'Files · 1']);
  const open = $('[data-document="f2"]');
  assert.equal(open.textContent, 'Open');
  assert.equal(open.getAttribute('href'), '/api/files/f2');
  assert.equal(open.target, '_blank');
  assert.equal(open.rel, 'noopener');
  const row = open.closest('.detail-file');
  assert.equal(row.querySelector('span').textContent, 'Assembly <guide>.pdf'); // shown as text, not markup
  assert.equal(row.querySelector('small').textContent, '3.0 MB');
  assert.equal(row.querySelector('a[download]').getAttribute('href'), '/api/files/f2?download=1');
  // A PDF is not offered to the slicer or the 3D viewer, and is not a photo.
  assert.equal(row.querySelector('[data-slicer-file]'), null);
  assert.equal(all('[data-view3d]').length, 1);
  assert.equal(all('[data-photo]').length, 1);

  // Dropping a PDF on a model page adds it to that model. It is not fingerprinted as a model.
  drop([new w.File(['%PDF-1.4'], 'Wiring.pdf'), new w.File(['x'], 'notes.docx')]);
  await settle(80);
  assert.deepEqual(uploads.at(-1), { url: '/api/models/crane/files', names: ['Wiring.pdf'] });
  assert.equal(lookups.length, 0);
  assert.match($('#toast').textContent, /Skipped notes\.docx: Forma keeps .* and PDF files\./);

  // A model without PDFs has no Instructions heading.
  $('[data-back]').click();
  await settle();
  $('[data-open="vase"]').click();
  await settle();
  assert.deepEqual(headings(), ['Files · 1']);

  // The Add model form accepts PDFs too.
  assert.match($('#upload-files').accept, /\.pdf/);

  assert.deepEqual(errors, []);
  console.log('Documents client test passed.');
  dom.window.close();
})().catch((error) => {
  console.error(error);
  dom.window.close();
  process.exitCode = 1;
});
