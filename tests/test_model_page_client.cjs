// Model pages: deep links, Back, Escape, new-tab clicks and the 3D preview.
const fs = require('node:fs'),
  path = require('node:path'),
  assert = require('node:assert/strict'),
  { JSDOM, VirtualConsole } = require('jsdom');
const root = path.resolve(__dirname, '..'),
  errors = [],
  vc = new VirtualConsole();
vc.on('jsdomError', (e) => errors.push(e.message));
const dom = new JSDOM(fs.readFileSync(path.join(root, 'static/index.html'), 'utf8'), {
    url: 'http://forma.test/models/b',
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
const base = {
  creator: '',
  source_url: '',
  description: '',
  tags: [],
  collections: [],
  collection: '',
  status: 'Saved',
  favorite: false,
  license: '',
  license_url: '',
  attribution: '',
};
const models = [
  {
    ...base,
    id: 'a',
    title: 'Hook',
    cover_id: '',
    created_at: '2026-11-02',
    files: [{ id: 'a-stl', name: 'hook.stl', kind: 'model', size: 684 }],
  },
  {
    ...base,
    id: 'b',
    title: 'Planter',
    cover_id: 'b-photo',
    created_at: '2026-11-03',
    files: [
      { id: 'b-photo', name: 'photo.jpg', kind: 'image', size: 100 },
      { id: 'b-stl', name: 'planter.stl', kind: 'model', size: 684 },
      { id: 'b-step', name: 'planter.step', kind: 'model', size: 900 },
    ],
  },
];
const response = (value) => ({ ok: true, status: 200, json: async () => value });
w.fetch = async (url) => {
  if (url === '/api/models') return response(models);
  if (url === '/api/watch') return response({ pending: 0 });
  if (/\/details$/.test(url)) return response({ files: [] });
  if (/\/source-import$/.test(url))
    return response({ job: null, photos: [], model: models.find((m) => url.includes('/' + m.id + '/')) });
  return response([]);
};
const settle = () => new Promise((resolve) => setTimeout(resolve, 20));
const click = (element, init = {}) =>
  element.dispatchEvent(new w.MouseEvent('click', { bubbles: true, cancelable: true, button: 0, ...init }));

(async () => {
  w.eval(
    ['app.js', 'catalog.js', 'source.js', 'slicer.js']
      .map((n) => fs.readFileSync(path.join(root, 'static', n), 'utf8'))
      .join('\n')
  );
  // Stand-in for the WebGL bundle: records what it was asked to show.
  w.FormaViewer = {
    mount(el, options) {
      w.mounted = options.url;
      options.onStatus({ state: 'ready', size: [20, 30, 10] });
      return { dispose: () => (w.disposed = (w.disposed || 0) + 1) };
    },
  };
  await settle();

  // A deep link opens the model page directly, showing the cover photo first.
  assert.ok(w.document.body.classList.contains('viewing-model'));
  assert.equal($('#detail').hidden, false);
  assert.equal($('#detail h2').textContent, 'Planter');
  assert.equal(w.document.title, 'Planter · Forma');
  assert.equal($('#detail-cover').hidden, false);
  assert.equal(w.mounted, undefined);
  // STL gets a 3D tile; STEP cannot be previewed in the browser.
  assert.deepEqual(
    $$('#detail [data-view3d]').map((b) => b.dataset.view3d),
    ['b-stl']
  );
  assert.equal($('#detail [data-photo="b-photo"]').classList.contains('selected'), true);

  click($('#detail [data-view3d="b-stl"]'));
  await settle();
  assert.equal(w.mounted, '/api/files/b-stl/mesh');
  assert.equal($('#detail-cover').hidden, true);
  assert.match($('#viewer-meta').textContent, /^20\.0 × 30\.0 × 10\.0 mm · planter\.stl/);

  // Back from a deep link goes to the library rather than leaving the app.
  click($('[data-back]'));
  await settle();
  assert.equal(w.location.pathname, '/');
  assert.equal(w.document.body.classList.contains('viewing-model'), false);
  assert.equal($('#detail').hidden, true);
  assert.equal($('#model-grid').querySelectorAll('.model-card').length, 2);
  assert.equal(w.disposed, 1);

  // Cards are real links: Cmd-click is left to the browser (new tab).
  const card = $('.card-open[data-open="a"]');
  assert.equal(card.getAttribute('href'), '/models/a');
  assert.equal(click(card, { metaKey: true }), true);
  assert.equal(w.location.pathname, '/');

  // A plain click opens the page; with no cover photo the 3D preview starts on its own.
  assert.equal(click(card), false);
  await settle();
  assert.equal(w.location.pathname, '/models/a');
  assert.equal(w.mounted, '/api/files/a-stl/mesh');
  assert.equal($('#detail [data-view3d="a-stl"]').classList.contains('selected'), true);

  // Escape returns to the library through browser history.
  w.document.dispatchEvent(new w.KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
  await settle();
  assert.equal(w.location.pathname, '/');
  assert.equal($('#detail').hidden, true);

  // Escape inside a dialog closes the dialog only. (The library re-rendered, so find the card again.)
  click($('.card-open[data-open="a"]'));
  await settle();
  $('#settings').showModal();
  w.document.dispatchEvent(new w.KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
  await settle();
  assert.equal(w.location.pathname, '/models/a');
  $('#settings').close();

  // Choosing a library tab from a model page leaves the page.
  click($('#navigation [data-view="favorites"]'));
  await settle();
  assert.equal($('#detail').hidden, true);
  assert.equal($('#page-title').textContent, 'Favorites');

  // A link to a model that no longer exists falls back to the library.
  w.history.pushState({}, '', '/models/gone');
  w.dispatchEvent(new w.PopStateEvent('popstate'));
  await settle();
  assert.equal(w.location.pathname, '/');
  assert.equal($('#detail').hidden, true);

  assert.deepEqual(errors, []);
  console.log('Model page client test passed.');
  dom.window.close();
})().catch((error) => {
  console.error(error);
  dom.window.close();
  process.exitCode = 1;
});
