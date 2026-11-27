// 英文的输出（docs/TEST-PLAN.md F19-15）。内容全部是虚构的。
import assert from 'node:assert/strict';
import { test } from 'node:test';

import { toMarkdown, toRecord } from '../lib/convert.js';
import { resolveLang, t } from '../lib/i18n.js';
import { localSink } from '../lib/local.js';
import { SITES } from '../lib/sites.js';
import { syncSite } from '../lib/sync.js';

const T = 1741766482;

function chatgpt(title) {
  const node = (id, parent, role, parts) => ({
    id,
    parent,
    message: { author: { role }, content: { content_type: 'multimodal_text', parts }, create_time: T, metadata: {} },
  });
  return {
    conversation_id: 'g1',
    title,
    create_time: T,
    update_time: T,
    current_node: 'a',
    mapping: {
      root: { id: 'root', parent: null, message: null },
      q: node('q', 'root', 'user', [{ content_type: 'image_asset_pointer' }, 'What is this?']),
      a: node('a', 'q', 'assistant', ['A cat.']),
    },
  };
}

test('F19_15 语言：跟随浏览器，中文浏览器用中文，其他用英文；可以手动选', () => {
  assert.equal(resolveLang('auto', 'zh-CN'), 'zh');
  assert.equal(resolveLang('auto', 'zh-TW'), 'zh');
  assert.equal(resolveLang('auto', 'en-US'), 'en');
  assert.equal(resolveLang('auto', 'ms'), 'en');
  assert.equal(resolveLang('zh', 'en-US'), 'zh');
  assert.equal(resolveLang('en', 'zh-CN'), 'en');
  assert.equal(t('en', 'login', { site: 'Claude' }), 'Please sign in to Claude in this browser first');
  assert.equal(t('en', '新建'), 'new');
});

test('F19_15 英文的 Markdown：说话人、占位、未命名都是英文', async () => {
  const rec = toRecord('chatgpt', chatgpt(''), 'en');
  assert.equal(rec.title, 'Untitled');
  assert.equal(rec.messages[0].text, '[image]\n\nWhat is this?');
  const md = toMarkdown(rec, 'en');
  assert.match(md, /## You · /);
  assert.match(md, /## ChatGPT · /);
  assert.doesNotMatch(md, /[我图片未命名]/);

  const files = new Map();
  const sink = localSink({ known: {}, lang: 'en', save: async (path, text) => files.set(path, text) });
  await sink('POST', '/plan', { platform: 'chatgpt', items: [{ id: 'g1', updated: T }] });
  await sink('POST', '/conversation', { platform: 'chatgpt', data: chatgpt('') });
  const [path] = files.keys();
  assert.equal(path, 'ai-chat-sync/chatgpt/Untitled g1.md');
  assert.match(files.get(path), /## You · /);
});

test('F19_15 英文的提示：没登录时', async () => {
  const deps = {
    lang: 'en',
    now: () => 0,
    sleep: async () => {},
    state: { limited: 0 },
    req: async () => ({ status: 401 }),
    eia: async () => ({ status: 200, body: {} }),
  };
  await assert.rejects(syncSite('claude', SITES.claude, deps), /Please sign in to Claude/);
});
