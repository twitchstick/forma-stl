// Big libraries: cards are drawn as you scroll, and new thumbnails load a few at a time.
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
const observers = [];
w.IntersectionObserver = class {
  constructor(callback) {
    this.callback = callback;
    this.targets = new Set();
    observers.push(this);
  }
  observe(el) {
    this.targets.add(el);
  }
  unobserve(el) {
    this.targets.delete(el);
  }
  disconnect() {
    this.targets.clear();
  }
  fire(el, isIntersecting = true) {
    this.callback([{ target: el, isIntersecting }]);
  }
};

const models = Array.from({ length: 150 }, (_, i) => ({
  id: 'm' + i,
  title: i === 140 ? 'Hidden treasure' : 'Model ' + i,
  creator: '',
  source_url: '',
  description: '',
  tags: [],
  collections: i < 3 ? ['Shelf'] : [],
  collection: '',
  status: 'Saved',
  favorite: false,
  cover_id: i === 1 ? 'photo' : '',
  created_at: new Date(2026, 0, 150 - i).toISOString(),
  files: [{ id: 'f' + i, name: 'part.stl', size: 1, kind: 'model' }],
}));
let listLoads = 0;
const response = (value) => ({ ok: true, status: 200, json: async () => value });
w.fetch = async (url, options = {}) => {
  if (url === '/api/models') {
    listLoads++;
    return response(models);
  }
  if (url.startsWith('/api/models/') && options.method === 'PATCH') {
    const m = models.find((x) => url.endsWith('/' + x.id));
    Object.assign(m, JSON.parse(options.body));
    return response({ ...m });
  }
  if (url === '/api/watch') return response({ pending: 0 });
  return response([]);
};
const settle = (ms = 20) => new Promise((resolve) => setTimeout(resolve, ms));
const cards = () => all('#model-grid .model-card').length;
const search = async (text) => {
  $('#search').value = text;
  $('#search').dispatchEvent(new w.Event('input'));
  await settle();
};

(async () => {
  w.eval(fs.readFileSync(path.join(root, 'static/app.js'), 'utf8'));
  await settle();
  const [thumbs, more] = observers;
  const started = () => all('#model-grid img[src*="/thumbnail"]');
  const waiting = () => all('#model-grid img[data-thumbnail]');

  // Only the first batch is drawn, and the count and sidebar still cover everything.
  assert.equal(cards(), 60);
  assert.equal($('#result-count').textContent, '150 models');
  assert.equal($('#total-count').textContent, '150');
  assert.match($('#collections').textContent, /Shelf3/);
  assert.equal($('#show-more').hidden, false);

  // Reaching the end of the list adds more, and watches again in case it is still in view.
  more.fire($('#show-more'));
  assert.equal(cards(), 120);
  assert.ok(more.targets.has($('#show-more')));
  // The Show more button works too; at the end it goes away.
  $('#show-more').click();
  assert.equal(cards(), 150);
  assert.equal($('#show-more').hidden, true);
  assert.equal(more.targets.has($('#show-more')), false);

  // Earlier cards are not duplicated or wiped when more arrive.
  assert.equal(new Set(all('[data-open]').map((a) => a.dataset.open)).size, 150);

  // Search covers models that have not been drawn.
  await search('treasure');
  assert.equal(cards(), 1);
  assert.equal($('#show-more').hidden, true);
  await search('');
  assert.equal(cards(), 60); // A new list starts from the top again.

  // Marking a favorite keeps your place and does not reload the whole library.
  $('#show-more').click();
  assert.equal(cards(), 120);
  const loads = listLoads;
  $('#model-grid [data-favorite="m100"]').click();
  await settle();
  assert.equal(listLoads, loads);
  assert.equal(cards(), 120);
  assert.equal($('#favorite-count').textContent, '1');
  assert.equal($('[data-favorite="m100"]').classList.contains('is-favorite'), true);

  // Opening a model and coming back keeps your place too.
  $('[data-open="m100"]').click();
  await settle();
  assert.equal(w.location.pathname, '/models/m100');
  $('[data-back]').click();
  await settle();
  w.dispatchEvent(new w.PopStateEvent('popstate'));
  await settle();
  assert.equal(cards(), 120);

  // Thumbnails: nothing starts until a card is near the screen, then three at a time.
  await search('Model 1');
  assert.equal(started().length, 0);
  const imgs = waiting().slice(0, 5);
  assert.equal(imgs.length, 5);
  imgs.forEach((img) => thumbs.fire(img));
  assert.equal(started().length, 3);
  assert.equal(waiting().length > 0, true);

  // A card scrolled away before its turn is dropped from the line.
  thumbs.fire(imgs[3], false);
  imgs[0].dispatchEvent(new w.Event('load'));
  assert.equal(imgs[3].getAttribute('src'), null);
  assert.ok(imgs[4].getAttribute('src').endsWith('/thumbnail'));

  // A failed thumbnail frees its place and shows the placeholder.
  thumbs.fire(imgs[3]);
  imgs[1].dispatchEvent(new w.Event('error'));
  assert.ok(imgs[3].getAttribute('src').endsWith('/thumbnail'));
  assert.equal(imgs[1].isConnected, false);

  // Photos are not queued: they need no drawing.
  await search('');
  assert.ok($('#model-grid img[data-model-image="m1"]').getAttribute('src').startsWith('/api/files/photo'));

  // A thumbnail that already loaded appears at once when the grid is redrawn.
  await search('Model 1');
  const loaded = $('#model-grid img[data-model-image="' + imgs[0].dataset.modelImage + '"]');
  assert.ok(loaded.getAttribute('src').endsWith('/thumbnail'));

  // Coming back to the app reloads the library only if it has been a while.
  Object.defineProperty(w.document, 'visibilityState', { value: 'visible', configurable: true });
  const loadsBefore = listLoads;
  const comeBack = async () => {
    w.document.dispatchEvent(new w.Event('visibilitychange'));
    await settle();
  };
  await comeBack();
  assert.equal(listLoads, loadsBefore);
  const now = w.Date.now;
  w.Date.now = () => now() + 6 * 60_000;
  await comeBack();
  assert.equal(listLoads, loadsBefore + 1);
  w.Date.now = now;

  assert.deepEqual(errors, []);
  console.log('Library scale client test passed.');
  dom.window.close();
})().catch((error) => {
  console.error(error);
  dom.window.close();
  process.exitCode = 1;
});
