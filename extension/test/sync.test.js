// 插件的同步顺序和限速（docs/TEST-PLAN.md F19-08、F19-09）。在 extension/ 目录运行：node --test
import assert from 'node:assert/strict';
import { test } from 'node:test';

import { SITES } from '../lib/sites.js';
import {
  DEFAULT_WAIT_MS,
  GAP_MS,
  SyncStop,
  afterRun,
  retryAfterMs,
  shouldRun,
  syncSite,
} from '../lib/sync.js';

// 假的网站和 eia：时间是虚拟的，sleep 只是把钟往前拨
function harness({ pages, conversations = {}, need, eiaDown = false, replies = {} }) {
  let clock = 0;
  const requests = [];
  const sent = [];
  const deps = {
    now: () => clock,
    sleep: async (ms) => {
      clock += ms;
    },
    state: { limited: 0 },
    req: async (path) => {
      requests.push({ path, at: clock });
      const scripted = pages[path];
      if (Array.isArray(scripted)) return scripted.length > 1 ? scripted.shift() : scripted[0];
      if (scripted) return scripted;
      const id = path.split('/conversation/')[1];
      if (id && conversations[id]) return { status: 200, json: conversations[id] };
      return { status: 404 };
    },
    eia: async (method, path, body) => {
      if (eiaDown) return { status: 0, body: {} };
      if (path === '/status') return { status: 200, body: { ok: true } };
      if (path === '/plan') {
        sent.push({ path, body });
        return { status: 200, body: { need: need ?? body.items.map((i) => i.id) } };
      }
      sent.push({ path, body });
      const id = body.data.conversation_id;
      return replies[id] || { status: 200, body: { result: '新建' } };
    },
  };
  return { deps, requests, sent };
}

const LIST = '/backend-api/conversations?offset=0&limit=100&order=updated';
const conv = (id) => ({ conversation_id: id, mapping: {} });

test('F19_08 只取 eia 说要取的，按顺序逐个发；列表按页取完', async () => {
  const page2 = '/backend-api/conversations?offset=100&limit=100&order=updated';
  const first = Array.from({ length: 100 }, (_, i) => ({ id: `a${i}`, update_time: i }));
  const { deps, sent } = harness({
    pages: {
      [LIST]: { status: 200, json: { items: first, total: 101 } },
      [page2]: { status: 200, json: { items: [{ id: 'z', update_time: 5 }], total: 101 } },
    },
    conversations: { a3: conv('a3'), z: conv('z') },
    need: ['z', 'a3'],
  });
  const result = await syncSite('chatgpt', SITES.chatgpt, deps);
  const plan = sent.find((s) => s.path === '/plan');
  assert.equal(plan.body.items.length, 101);
  assert.deepEqual(plan.body.items[100], { id: 'z', updated: 5 });
  const posted = sent.filter((s) => s.path === '/conversation').map((s) => s.body.data.conversation_id);
  assert.deepEqual(posted, ['z', 'a3']);
  assert.deepEqual(result.counts, { 新建: 2 });
});

test('F19_08 eia 没开时停下，不去取网站', async () => {
  const { deps, requests } = harness({ pages: {}, eiaDown: true });
  await assert.rejects(syncSite('chatgpt', SITES.chatgpt, deps), (e) => e instanceof SyncStop && e.kind === 'eia');
  assert.equal(requests.length, 0);
});

test('F19_08 没登录时停下并提示', async () => {
  const { deps } = harness({ pages: { [LIST]: { status: 401 } } });
  await assert.rejects(syncSite('chatgpt', SITES.chatgpt, deps), (e) => e.kind === 'login');
});

test('F19_08 连续 3 个对话格式认不出就停；中间有成功的就重新数', async () => {
  const items = ['a', 'b', 'c', 'd', 'e'].map((id) => ({ id, update_time: 1 }));
  const bad = { status: 422, body: { error: '格式' } };
  const conversations = Object.fromEntries(items.map(({ id }) => [id, conv(id)]));
  const ok = harness({
    pages: { [LIST]: { status: 200, json: { items, total: 5 } } },
    conversations,
    replies: { a: bad, b: bad, d: bad, e: bad },
  });
  const result = await syncSite('chatgpt', SITES.chatgpt, ok.deps);
  assert.deepEqual(result.counts, { 格式认不出: 4, 新建: 1 });

  const stop = harness({
    pages: { [LIST]: { status: 200, json: { items, total: 5 } } },
    conversations,
    replies: { a: bad, b: bad, c: bad },
  });
  await assert.rejects(syncSite('chatgpt', SITES.chatgpt, stop.deps), (e) => e.kind === 'format');
});

test('F19_08 当天成功后不再跑；没成功的再试；限速和改版停到明天', () => {
  const today = '2026-11-27';
  assert.equal(shouldRun(undefined, today), true);
  const done = afterRun(undefined, today, { at: 1, result: { counts: {} } });
  assert.equal(shouldRun(done, today), false);
  assert.equal(shouldRun(done, '2026-11-28'), true);
  const eiaDown = afterRun(undefined, today, { at: 1, error: new SyncStop('eia', 'eia 没开') });
  assert.equal(shouldRun(eiaDown, today), true);
  const limited = afterRun(undefined, today, { at: 1, limited: 3, error: new SyncStop('limited', '限速') });
  assert.equal(shouldRun(limited, today), false);
  assert.equal(limited.limited, 3);
  assert.equal(shouldRun(limited, '2026-11-28'), true);
});

test('F19_08 Claude：先取组织编号，再列对话、取对话', async () => {
  const org = '/api/organizations';
  const list = '/api/organizations/o1/chat_conversations';
  const one = '/api/organizations/o1/chat_conversations/c1?tree=True&rendering_mode=messages&render_all_tools=true';
  const { deps, sent, requests } = harness({
    pages: {
      [org]: { status: 200, json: [{ uuid: 'o0', capabilities: ['api'] }, { uuid: 'o1', capabilities: ['chat'] }] },
      [list]: { status: 200, json: [{ uuid: 'c1', updated_at: '2026-11-27T01:00:00Z' }] },
      [one]: { status: 200, json: { uuid: 'c1', conversation_id: 'c1' } },
    },
  });
  await syncSite('claude', SITES.claude, deps);
  assert.deepEqual(requests.map((r) => r.path), [org, list, one]);
  assert.deepEqual(sent[0].body.items, [{ id: 'c1', updated: '2026-11-27T01:00:00Z' }]);
});

test('F19_09 两次请求网站之间至少隔 2 秒', async () => {
  const items = ['a', 'b', 'c'].map((id) => ({ id, update_time: 1 }));
  const { deps, requests } = harness({
    pages: { [LIST]: { status: 200, json: { items, total: 3 } } },
    conversations: Object.fromEntries(items.map(({ id }) => [id, conv(id)])),
  });
  await syncSite('chatgpt', SITES.chatgpt, deps);
  assert.equal(requests.length, 4);
  for (let i = 1; i < requests.length; i += 1) {
    assert.ok(requests[i].at - requests[i - 1].at >= GAP_MS, `第 ${i} 次只隔了 ${requests[i].at - requests[i - 1].at}`);
  }
});

test('F19_09 429 按 Retry-After 等，没有就等 60 秒；一天 3 次就停', async () => {
  assert.equal(retryAfterMs('30'), 30_000);
  assert.equal(retryAfterMs(null), DEFAULT_WAIT_MS);
  assert.equal(retryAfterMs('不是数字'), DEFAULT_WAIT_MS);

  const items = [{ id: 'a', update_time: 1 }];
  const path = '/backend-api/conversation/a';
  const once = harness({
    pages: {
      [LIST]: { status: 200, json: { items, total: 1 } },
      [path]: [{ status: 429, retryAfter: '30' }, { status: 200, json: conv('a') }],
    },
  });
  await syncSite('chatgpt', SITES.chatgpt, once.deps);
  const tries = once.requests.filter((r) => r.path === path);
  assert.equal(tries.length, 2);
  assert.ok(tries[1].at - tries[0].at >= 30_000);
  assert.equal(once.deps.state.limited, 1);

  const often = harness({
    pages: { [LIST]: { status: 200, json: { items, total: 1 } }, [path]: [{ status: 429 }] },
  });
  await assert.rejects(syncSite('chatgpt', SITES.chatgpt, often.deps), (e) => e.kind === 'limited');
  assert.equal(often.deps.state.limited, 3);
  const waits = often.requests.filter((r) => r.path === path);
  assert.ok(waits[1].at - waits[0].at >= DEFAULT_WAIT_MS);
});

test('F19_12 Gemini：列表按翻页标记取完、两组都取；对话按轮去重、翻页；网址带账号', async () => {
  const requests = [];
  const pages = {
    'MaZiqc:0:': { inner: [null, 'next-page-token', [['c_a', '甲', 0, 0, 0, [100, 5e8]], ['c_b', '乙', 0, 0, 0, [90, 0]]]] },
    'MaZiqc:0:next-page-token': { inner: [null, null, [['c_c', '丙', 0, 0, 0, [80, 0]]]] },
    'MaZiqc:1:': { inner: [null, null, [['c_a', '甲', 0, 0, 0, [100, 5e8]]]] },
    'hNvQHb:c_b:': { inner: [[[['c_b', 'r1'], 0, [['问']], [[['rc1', ['答']]]], [90, 0]]], 'more-turns-token'] },
    'hNvQHb:c_b:more-turns-token': { inner: [[[['c_b', 'r1'], 0, [['问']], [[['rc1', ['答']]]], [90, 0]], [['c_b', 'r0'], 0, [['早']], [[['rc0', ['早答']]]], [10, 0]]]] },
  };
  let clock = 0;
  const deps = {
    now: () => clock,
    sleep: async (ms) => {
      clock += ms;
    },
    state: { limited: 0 },
    req: async (request, site) => {
      requests.push(request);
      assert.equal(site.world, 'MAIN');
      const key =
        request.rpc === 'MaZiqc'
          ? `MaZiqc:${request.payload[2][0]}:${request.payload[1] || ''}`
          : `hNvQHb:${request.payload[0]}:${request.payload[2] || ''}`;
      const json = pages[key] || { inner: [] };
      return { status: 200, json: { ...json, prefix: '/u/1' } };
    },
    eia: async (method, path, body) => {
      if (path === '/status') return { status: 200, body: {} };
      if (path === '/plan') {
        deps.plan = body.items;
        return { status: 200, body: { need: ['c_b'] } };
      }
      deps.sent = body.data;
      return { status: 200, body: { result: '新建' } };
    },
  };
  const result = await syncSite('gemini', SITES.gemini, deps);
  assert.deepEqual(
    deps.plan.map((i) => i.id),
    ['c_a', 'c_b', 'c_c'],
  );
  assert.equal(deps.plan[0].updated, 100.5);
  assert.equal(deps.sent.conversationId, 'c_b');
  assert.equal(deps.sent.title, '乙');
  assert.equal(deps.sent.url, 'https://gemini.google.com/u/1/app/b');
  assert.equal(deps.sent.turns.length, 2); // 第二页重复的那一轮去掉了
  assert.equal(deps.sent.possiblyIncomplete, false);
  assert.equal(result.home, 'https://gemini.google.com/u/1/app');
  assert.equal(requests.filter((r) => r.rpc === 'hNvQHb').length, 2);
});
