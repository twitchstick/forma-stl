const fs = require('node:fs'),
  assert = require('node:assert/strict'),
  { JSDOM } = require('jsdom');
const script = fs
  .readFileSync(require('node:path').join(__dirname, '../static/website-bookmarklet.js'), 'utf8')
  .replace('__FORMA_ORIGIN__', JSON.stringify('http://forma.test'));
const cases = [
  ['printables', 'https://www.printables.com/collection/12', '/model/123-tray', '/model/456-box'],
  ['cults3d', 'https://cults3d.com/en/collections/desk', '/en/3d-model/home/tray', '/en/3d-model/home/box'],
  [
    'thangs',
    'https://thangs.com/mythangs/collections/12',
    '/designer/maker/3d-model/Tray-123',
    '/designer/maker/3d-model/Box-456',
  ],
];
(async () => {
  for (const [provider, url, a, b] of cases) {
    const dom = new JSDOM(
        `<h1>Workshop</h1><article><a href="${a}"><img src="https://cdn.example.com/a.jpg" alt="Tray"><h3>Tray</h3></a></article><article><a href="${b}"><img src="https://cdn.example.com/b.jpg" alt="Box"><h3>Box</h3></a></article>`,
        { url, runScripts: 'outside-only' }
      ),
      w = dom.window;
    let exported;
    w.URL.createObjectURL = (blob) => {
      exported = blob;
      return 'blob:test';
    };
    w.URL.revokeObjectURL = () => {};
    w.HTMLAnchorElement.prototype.click = function () {};
    w.eval(script);
    const panel = w.document.getElementById('forma-website-panel'),
      root = panel.shadowRoot,
      $ = (s) => root.querySelector(s);
    assert.equal(root.querySelectorAll('[data-index]').length, 2);
    $('#collection').value = 'Desk';
    $('#capture').click();
    assert.equal(root.querySelectorAll('[data-index]').length, 2);
    $('#all').checked = false;
    $('#all').dispatchEvent(new w.Event('change'));
    $('#export').click();
    assert.match($('#status').textContent, /select at least one/i);
    const check = root.querySelector('[data-index]');
    check.checked = true;
    check.dispatchEvent(new w.Event('change'));
    $('#export').click();
    const read = new w.FileReader();
    const payload = await new Promise((resolve, reject) => {
      read.onload = () => resolve(JSON.parse(read.result));
      read.onerror = reject;
      read.readAsText(exported);
    });
    assert.equal(payload.items.length, 1);
    assert.equal(payload.items[0].provider, provider);
    assert.ok(payload.items[0].collections.some((c) => c.name === 'Desk'));
    assert.equal(payload.items[0].image_url, 'https://cdn.example.com/a.jpg');
    assert.equal(payload.format, 'forma-websites-v1');
    // Reopen preserves selection and collection memberships in this tab.
    $('#close').click();
    w.eval(script);
    assert.equal(
      w.document.getElementById('forma-website-panel').shadowRoot.querySelectorAll('[data-index]:checked').length,
      1
    );
    assert.ok(!script.includes('document.cookie'));
    assert.ok(!script.includes('fetch('));
    dom.window.close();
  }
  const dom = new JSDOM(
    '<h1>Full model</h1><script type="application/ld+json">{"@type":"Product","name":"Full model","license":{"name":"Creator terms","url":"https://example.com/license"},"creditText":"Credit Maker","image":["https://cdn.example.com/second.jpg"]}</script><meta property="og:image" content="https://cdn.example.com/cover.jpg"><meta property="og:description" content="A real description"><meta name="author" content="Maker"><a href="/model/999-other"><img alt="Recommended"></a>',
    { url: 'https://www.printables.com/model/123-tray', runScripts: 'outside-only' }
  );
  dom.window.eval(script);
  const root = dom.window.document.getElementById('forma-website-panel').shadowRoot;
  assert.equal(root.querySelectorAll('[data-index]').length, 1);
  assert.match(root.textContent, /Full model/);
  assert.ok(!root.querySelector('#list').textContent.includes('Recommended'));
  const captured = JSON.parse(dom.window.sessionStorage.getItem('forma-capture-v1'))[0];
  assert.equal(captured.license, 'Creator terms');
  assert.equal(captured.attribution, 'Credit Maker');
  assert.equal(captured.image_urls.length, 2);
  dom.window.close();
  console.log(
    'PASS: Printables, Cults3D and Thangs card capture, collection merging, selection, JSON export, tab persistence and model-page recommendation filtering.'
  );
})().catch((e) => {
  console.error(e);
  process.exitCode = 1;
});
