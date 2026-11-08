const assert = require('node:assert/strict');
const MW = require('../companion/makerworld.js');
const response = (body, status = 200) => ({ ok: status === 200, status, json: async () => body });
(async () => {
  const calls = [];
  const fake = async (url, opt) => {
    calls.push({ url, opt });
    return response({ hits: [{ id: 10, title: 'My private saves', designCnt: 2 }] });
  };
  const groups = await MW.collections(fake, 'browser-only-session');
  assert.equal(groups[0].name, 'My private saves');
  assert.equal(calls[0].url, 'https://api.bambulab.com/v1/design-service/my/favorites/listlite?query=');
  assert.equal(calls[0].opt.headers.Authorization, 'Bearer browser-only-session');
  const item = MW.normalize(
    {
      id: 123,
      title: 'Desk tray',
      designCreator: { name: 'Maker' },
      coverUrl: 'https://makerworld.bblmw.com/cover.png',
      defaultInstanceId: 9,
      instances: [{ id: 9 }],
      tags: [{ name: 'desk' }],
    },
    { id: '10', name: 'Desk' }
  );
  assert.equal(item.creator, 'Maker');
  assert.equal(item.profile_id, '9');
  assert.deepEqual(item.tags, ['desk']);
  const merged = MW.merge([[item], [{ ...item, collections: [{ id: '20', name: 'Gifts' }], favorite: true }]]);
  assert.equal(merged.length, 1);
  assert.equal(merged[0].collections.length, 2);
  assert.equal(merged[0].favorite, true);
  let pageCalls = 0;
  const paging = async () => {
    pageCalls++;
    return response({ hits: [{ id: 456, title: 'Another tray' }], total: 1 });
  };
  assert.equal(
    (await MW.paged('/design-service/favorites/10/designs', { id: '10', name: 'Desk' }, paging, 'session')).length,
    1
  );
  assert.equal(pageCalls, 1);
  await assert.rejects(
    () => MW.api('/design-service/my/favorites/listlite', async () => response({}, 403), 'session'),
    /Sign in/
  );
  await assert.rejects(
    () => MW.api('/design-service/design/123', async () => response({}, 429), 'session'),
    /slow down/
  );
  await assert.rejects(() => MW.api('https://evil.test/steal', fake, 'session'), /Invalid/);
  assert.equal(MW.normalize({ id: 'bad', title: 'Unknown' }), null);
  console.log(
    'PASS: MakerWorld API normalization, scoped authentication, collection merging, pagination and error handling.'
  );
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
