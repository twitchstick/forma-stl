// Quick adding: drop files anywhere, paste files or links, upload progress.
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
w.scrollTo = () => {}; // jsdom does not implement scrolling
w.HTMLDialogElement.prototype.showModal = function () {
  this.open = true;
};
w.HTMLDialogElement.prototype.close = function () {
  this.open = false;
  this.dispatchEvent(new w.Event('close'));
};

let models = [],
  created = [],
  metadataRequests = [],
  uploads = [],
  failUpload = false;
const response = (value, status = 200) => ({ ok: status < 400, status, json: async () => value });
w.fetch = async (url, options = {}) => {
  if (url === '/api/models' && options.method === 'POST') {
    const body = JSON.parse(options.body);
    const model = {
      id: 'new' + (created.length + 1),
      ...body,
      creator: '',
      source_url: '',
      description: '',
      collections: [],
      favorite: false,
      cover_id: '',
      created_at: '2026-11-08',
      files: [],
    };
    created.push(body);
    models = [model, ...models];
    return response(model, 201);
  }
  if (url === '/api/models') return response(models);
  if (url === '/api/metadata') {
    metadataRequests.push(JSON.parse(options.body).url);
    return response({ title: 'Imported model', tags: [] });
  }
  if (url === '/api/watch') return response({ pending: 0 });
  return response([]);
};
// Stand-in for XMLHttpRequest: records each upload and reports progress.
w.XMLHttpRequest = class {
  constructor() {
    this.upload = {};
    this.headers = {};
  }
  open(method, url) {
    Object.assign(this, { method, url });
  }
  setRequestHeader(name, value) {
    this.headers[name] = value;
  }
  send(body) {
    const names = body.getAll('files').map((f) => f.name);
    uploads.push({ url: this.url, names, headers: this.headers });
    setTimeout(() => {
      this.upload.onprogress?.({ lengthComputable: true, loaded: 50, total: 100 });
      if (failUpload) {
        this.status = 413;
        this.response = { error: 'File too large.' };
      } else {
        this.status = 200;
        const id = this.url.split('/')[3];
        models = models.map((m) =>
          m.id === id
            ? {
                ...m,
                files: [...m.files, ...names.map((name, i) => ({ id: id + '-' + i, name, kind: 'model', size: 5 }))],
              }
            : m
        );
        this.response = models.find((m) => m.id === id);
      }
      this.onload();
    }, 1);
  }
};
const settle = () => new Promise((resolve) => setTimeout(resolve, 30));
const file = (name, size = 5) => new w.File(['x'.repeat(size)], name);
function fileEvent(type, files = [], target = w.document.body) {
  const event = new w.Event(type, { bubbles: true, cancelable: true });
  Object.defineProperty(event, 'dataTransfer', { value: { types: ['Files'], files } });
  target.dispatchEvent(event);
  return event;
}
function pasteEvent(target, { files = [], text = '' }) {
  const event = new w.Event('paste', { bubbles: true, cancelable: true });
  Object.defineProperty(event, 'clipboardData', { value: { files, getData: () => text } });
  target.dispatchEvent(event);
  return event;
}

(async () => {
  w.eval(['app.js', 'quick-add.js'].map((n) => fs.readFileSync(path.join(root, 'static', n), 'utf8')).join('\n'));
  await settle();

  // Dragging files over the library shows the drop target.
  fileEvent('dragenter');
  assert.equal($('#drop-overlay').hidden, false);
  assert.equal($('#drop-overlay strong').textContent, 'Drop to add a model');
  const dragover = fileEvent('dragover');
  assert.equal(dragover.defaultPrevented, true);

  // Dropping opens the quick-add sheet with a title taken from the model file.
  const drop = fileEvent('drop', [file('Benchy_v2-final.stl'), file('photo.png'), file('notes.txt')]);
  assert.equal(drop.defaultPrevented, true);
  assert.equal($('#drop-overlay').hidden, true);
  assert.equal($('#quick-add').open, true);
  assert.equal($('#quick-add-form').elements.title.value, 'Benchy v2 final');
  assert.equal(w.document.activeElement, $('#quick-add-form').elements.title);
  assert.equal($('#quick-files').children.length, 2);
  assert.match($('#quick-skipped').textContent, /Skipped notes\.txt/);

  // Adding creates the model, uploads both files, then opens the new model page.
  $('#quick-add-form').elements.status.value = 'To print';
  $('#quick-add-form').requestSubmit();
  await settle();
  assert.deepEqual(created[0], { title: 'Benchy v2 final', collection: '', status: 'To print', tags: [] });
  assert.deepEqual(uploads[0].names, ['Benchy_v2-final.stl', 'photo.png']);
  assert.equal(uploads[0].url, '/api/models/new1/files');
  assert.equal(uploads[0].headers['X-Forma-Request'], '1');
  assert.equal($('#quick-add').open, false);
  assert.equal(w.location.pathname, '/models/new1');
  assert.match($('#toast').textContent, /Added Benchy v2 final/);

  // On a model page, dropped files go straight onto that model.
  fileEvent('dragenter');
  assert.equal($('#drop-overlay strong').textContent, 'Add files to Benchy v2 final');
  fileEvent('drop', [file('lid.3mf')]);
  await settle();
  assert.equal($('#quick-add').open, false);
  assert.deepEqual(uploads[1], {
    url: '/api/models/new1/files',
    names: ['lid.3mf'],
    headers: { 'X-Forma-Request': '1' },
  });
  assert.match($('#toast').textContent, /Added lid\.3mf to Benchy v2 final/);
  assert.match($('#detail').textContent, /lid\.3mf/);

  // Pasting a screenshot on a model page adds it too.
  pasteEvent(w.document.body, { files: [file('image.png')] });
  await settle();
  assert.deepEqual(uploads[2].names, ['image.png']);

  // Only unsupported files: nothing is created and the reason is shown.
  click($('[data-back]'));
  await settle();
  fileEvent('drop', [file('readme.pdf')]);
  assert.equal($('#quick-add').open, false);
  assert.match($('#toast').textContent, /Skipped readme\.pdf/);

  // If the upload fails, the new model is kept and the reason is shown.
  failUpload = true;
  fileEvent('drop', [file('broken.stl')]);
  $('#quick-add-form').requestSubmit();
  await settle();
  assert.equal(w.location.pathname, '/models/new2');
  assert.match($('#toast').textContent, /broken was added, but its files were not: File too large\./);
  failUpload = false;
  click($('[data-back]'));
  await settle();

  // Pasting a link anywhere starts an import.
  pasteEvent(w.document.body, { text: '  https://www.printables.com/model/12345-planter  ' });
  await settle();
  assert.equal($('#editor').open, true);
  assert.equal($('#import-section').hidden, false);
  assert.equal($('#import-url').value, 'https://www.printables.com/model/12345-planter');
  assert.deepEqual(metadataRequests, ['https://www.printables.com/model/12345-planter']);
  $('#editor').close();

  // Pasting into a text field, or with a dialog open, is left alone.
  const typed = pasteEvent($('#search'), { text: 'https://example.com/model' });
  assert.equal(typed.defaultPrevented, false);
  $('#settings').showModal();
  fileEvent('drop', [file('ignored.stl')]);
  pasteEvent(w.document.body, { text: 'https://example.com/model' });
  assert.equal($('#quick-add').open, false);
  assert.equal(metadataRequests.length, 1);
  $('#settings').close();

  assert.deepEqual(errors, []);
  console.log('Quick add client test passed.');
  dom.window.close();
})().catch((error) => {
  console.error(error);
  dom.window.close();
  process.exitCode = 1;
});

function click(element) {
  element.dispatchEvent(new w.MouseEvent('click', { bubbles: true, cancelable: true, button: 0 }));
}
