const fs = require('node:fs'),
  path = require('node:path'),
  assert = require('node:assert/strict');
const { JSDOM, VirtualConsole } = require('jsdom');
const root = path.resolve(__dirname, '..');
const errors = [],
  vc = new VirtualConsole();
vc.on('jsdomError', (e) => errors.push(e.message));
const dom = new JSDOM(fs.readFileSync(path.join(root, 'static/index.html'), 'utf8'), {
  url: 'http://forma.test',
  runScripts: 'outside-only',
  virtualConsole: vc,
});
const w = dom.window,
  $ = (s) => w.document.querySelector(s);
w.matchMedia = () => ({ matches: true, addEventListener() {} });
w.confirm = () => true;
w.Element.prototype.scrollIntoView = function () {};
w.HTMLDialogElement.prototype.showModal = function () {
  this.open = true;
};
w.HTMLDialogElement.prototype.close = function () {
  this.open = false;
};
let saved = [],
  printers = [],
  logs = [],
  label = null;
const models = [
  {
    id: 'a',
    title: 'Desk tray',
    creator: 'Maker',
    source_url: 'https://printables.com/model/1',
    description: '',
    tags: ['desk'],
    collections: ['Workshop'],
    collection: 'Workshop',
    status: 'To print',
    favorite: false,
    cover_id: '',
    created_at: '2026-11-03',
    files: [{ id: 'file1', name: 'tray.stl', size: 100, kind: 'model' }],
  },
  {
    id: 'b',
    title: 'Vase',
    creator: 'Maker',
    source_url: '',
    description: '',
    tags: [],
    collections: [],
    collection: '',
    status: 'Printed',
    favorite: false,
    cover_id: '',
    created_at: '2026-11-03',
    files: [],
  },
];
const response = (value) => ({ ok: true, json: async () => value });
w.fetch = async (url, opt = {}) => {
  const p = opt.body && typeof opt.body === 'string' ? JSON.parse(opt.body) : null;
  if (url === '/api/models') return response(models);
  if (url === '/api/watch') return response({ pending: 0, enabled: true, interval: 60 });
  if (url === '/api/saved-views' && opt.method === 'POST') {
    saved.push({ id: 'view1', ...p });
    return response({ id: 'view1' });
  }
  if (url === '/api/saved-views') return response(saved);
  if (url === '/api/saved-views/view1' && opt.method === 'PATCH') {
    saved[0].name = p.name;
    return response({ ok: true });
  }
  if (url === '/api/printers' && opt.method === 'POST') {
    printers.push({ id: 'printer1', ...p });
    return response({ id: 'printer1' });
  }
  if (url === '/api/printers') return response(printers);
  if (url === '/api/models/a/details')
    return response({
      files: [
        {
          id: 'file1',
          name: 'tray.stl',
          size: 100,
          variant_label: '',
          error: '',
          entries: [
            {
              name: 'tray.stl',
              dimensions_mm: [110, 80, 20],
              note: 'Millimeters assumed',
              settings: { layer_height: '0.2' },
              error: '',
            },
          ],
        },
      ],
    });
  if (url === '/api/files/file1/label') {
    label = p.label;
    return response({ ok: true });
  }
  if (url === '/api/models/a/prints' && opt.method === 'POST') {
    logs.push({ id: 'log1', printer_name: printers[0].name, ...p });
    return response({ id: 'log1' });
  }
  if (url === '/api/models/a/prints') return response(logs);
  throw Error('Unexpected request ' + url);
};
const tick = () => new Promise((r) => setImmediate(r));
async function settle() {
  for (let n = 0; n < 4; n++) await tick();
}
function submit(form) {
  const button = form.querySelector('button[type=submit]');
  form.dispatchEvent(new w.SubmitEvent('submit', { bubbles: true, cancelable: true, submitter: button }));
}
(async () => {
  w.eval(
    fs.readFileSync(path.join(root, 'static/app.js'), 'utf8') +
      '\n' +
      fs.readFileSync(path.join(root, 'static/catalog.js'), 'utf8') +
      '\nwindow.testFit=fitText;'
  );
  await settle();
  assert.equal(w.document.querySelectorAll('.model-card').length, 2);
  $('#search').value = 'Desk';
  $('#search').dispatchEvent(new w.Event('input'));
  $('#status-filter').value = 'To print';
  $('#status-filter').dispatchEvent(new w.Event('change'));
  $('#save-view').click();
  $('#saved-view-form').elements.name.value = 'Workshop ideas';
  submit($('#saved-view-form'));
  await settle();
  assert.equal(saved[0].rules.search, 'Desk');
  assert.equal(saved[0].rules.status, 'To print');
  $('#search').value = 'Vase';
  $('#search').dispatchEvent(new w.Event('input'));
  $('#status-filter').value = '';
  $('#status-filter').dispatchEvent(new w.Event('change'));
  $('[data-saved-view]').click();
  await settle();
  assert.equal($('#search').value, 'Desk');
  assert.equal(w.document.querySelectorAll('.model-card').length, 1);
  $('#printers-button').click();
  await settle();
  for (const [k, v] of Object.entries({
    name: 'Small printer',
    width: '100',
    depth: '120',
    height: '200',
    nozzle: '0.4',
  }))
    $('#printer-form').elements[k].value = v;
  submit($('#printer-form'));
  await settle();
  assert.equal(printers.length, 1);
  assert.match($('#printers-list').textContent, /Small printer/);
  $('#printers-dialog').close();
  $('[data-open="a"]').click();
  await settle();
  assert.match($('#catalog-model-details').textContent, /110 × 80 × 20 mm/);
  $('#fit-printer').value = 'printer1';
  $('#fit-printer').dispatchEvent(new w.Event('change'));
  assert.match($('.fit-result').textContent, /90°/);
  const variant = $('[data-variant-file]');
  variant.elements.label.value = 'Tray base';
  submit(variant);
  await settle();
  assert.equal(label, 'Tray base');
  $('#record-print').click();
  await settle();
  $('#log-printer').value = 'printer1';
  $('#print-log-form').elements.filament.value = 'PLA';
  $('#print-log-form').elements.notes.value = '<b>Finished</b>';
  submit($('#print-log-form'));
  await settle();
  assert.equal(logs.length, 1);
  assert.match($('#catalog-print-history').textContent, /<b>Finished<\/b>/);
  assert.equal($('#catalog-print-history b'), null);
  assert.match(w.testFit([120, 130, 250], { width: 100, depth: 120, height: 200 }), /Exceeds/);
  assert.equal(w.testFit(null, {}), '');
  assert.deepEqual(errors, []);
  console.log(
    'PASS: saved filter persistence/reapplication, printer creation, measurements, variant labels, XY fit check, print logs and escaped notes.'
  );
  dom.window.close();
})().catch((e) => {
  console.error(e);
  dom.window.close();
  process.exitCode = 1;
});
