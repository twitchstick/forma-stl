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
Object.defineProperty(w.navigator, 'platform', { value: 'MacIntel' });
w.HTMLDialogElement.prototype.showModal = function () {
  this.open = true;
};
w.HTMLDialogElement.prototype.close = function () {
  this.open = false;
  this.dispatchEvent(new w.Event('close'));
};
const models = [
  {
    id: 'a',
    title: 'Workshop tray',
    creator: 'Maker',
    source_url: '',
    description: '',
    tags: [],
    collections: [],
    collection: '',
    status: 'Saved',
    favorite: false,
    cover_id: '',
    created_at: '2026-11-04',
    files: [
      { id: 'file1', name: 'Tray.stl', kind: 'model', size: 1024 },
      { id: 'zip1', name: 'Parts.zip', kind: 'model', size: 1024 },
    ],
  },
];
const printers = [
  { id: 'bambu1', name: 'Bambu Lab H2D', width: 300, depth: 320, height: 320, slicer: 'bambu' },
  { id: 'u1', name: 'Snapmaker U1', width: 270, depth: 270, height: 270, slicer: 'snapmaker' },
];
let mint = [];
const response = (value) => ({ ok: true, json: async () => value });
w.fetch = async (url, opt = {}) => {
  if (url === '/api/models') return response(models);
  if (url === '/api/watch') return response({ pending: 0 });
  if (url === '/api/saved-views' || url === '/api/models/a/prints') return response([]);
  if (url === '/api/printers') return response(printers);
  if (url === '/api/printer-presets')
    return response({
      presets: [
        {
          id: 'h2s',
          name: 'Bambu Lab H2S',
          width: 340,
          depth: 320,
          height: 340,
          nozzle: 0.4,
          slicer: 'bambu',
          notes: 'Check slicer clearance.',
        },
      ],
    });
  if (url === '/api/models/a/details') return response({ files: [] });
  if (url === '/api/files/file1/slicer-link') {
    const p = JSON.parse(opt.body);
    mint.push(p);
    return response({
      launch_url: p.slicer === 'snapmaker' ? 'snapmaker-orca://open?file=ENCODED' : 'bambustudioopen://ENCODED',
      slicer_name: p.slicer === 'snapmaker' ? 'Snapmaker Orca' : 'Bambu Studio',
      converted: p.slicer === 'bambu',
    });
  }
  throw Error('Unexpected request ' + url);
};
const settle = async () => {
  for (let n = 0; n < 5; n++) await new Promise((r) => setImmediate(r));
};
function change(el, value) {
  el.value = value;
  el.dispatchEvent(new w.Event('change'));
}
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
    ['app.js', 'catalog.js', 'slicer.js'].map((n) => fs.readFileSync(path.join(root, 'static', n), 'utf8')).join('\n')
  );
  await settle();
  $('#printers-button').click();
  await settle();
  change($('#printer-preset-picker'), 'h2s');
  assert.equal($('#printer-form').elements.width.value, '340');
  assert.equal($('#printer-form').elements.slicer.value, 'bambu');
  $('#printers-dialog').close();
  $('[data-open="a"]').click();
  await settle();
  assert.equal(w.document.querySelectorAll('[data-slicer-file]').length, 1);
  $('[data-slicer-file="file1"]').click();
  await settle();
  assert.equal($('#slicer-dialog').open, true);
  assert.equal($('#slicer-platform').value, 'mac');
  assert.equal(mint.length, 0);
  assert.equal($('#slicer-ready').hidden, true);
  change($('#slicer-printer'), 'u1');
  assert.equal($('#slicer-target').value, 'snapmaker');
  submit($('#slicer-form'));
  await settle();
  assert.deepEqual(mint[0], { slicer: 'snapmaker', platform: 'mac' });
  assert.match($('#slicer-launch').href, /^snapmaker-orca:/);
  assert.match($('#slicer-download').href, /\/api\/files\/file1\?download=1$/);
  assert.equal(w.location.href, 'http://forma.test/');
  change($('#slicer-target'), 'bambu');
  assert.equal($('#slicer-ready').hidden, true);
  assert.equal($('#slicer-launch').hasAttribute('href'), false);
  submit($('#slicer-form'));
  await settle();
  assert.match($('#slicer-launch').href, /^bambustudioopen:/);
  assert.match($('#slicer-message').textContent, /Geometry-only 3MF/);
  assert.deepEqual(errors, []);
  console.log(
    'PASS: printer presets, preferred slicer selection, macOS handoff, explicit launch links, original download fallback and 3MF conversion notice.'
  );
  dom.window.close();
})().catch((e) => {
  console.error(e);
  dom.window.close();
  process.exitCode = 1;
});
