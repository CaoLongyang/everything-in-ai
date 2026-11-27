// 不装 eia 时：插件自己转换、存文件（docs/TEST-PLAN.md F19-13、F19-14）。内容全部是虚构的。
import assert from 'node:assert/strict';
import { test } from 'node:test';

import { FormatError, toMarkdown, toRecord } from '../lib/convert.js';
import { localSink } from '../lib/local.js';

const T = 1741766482; // 2025-03-12T08:01:22Z

function chatgpt(id = 'g1', extra = 0) {
  const mapping = { root: { id: 'root', parent: null, message: null } };
  const turns = [
    ['user', '虚构的问题'],
    ['assistant', '虚构的回答'],
    ...Array.from({ length: extra }, (_, i) => ['user', `追问 ${i}`]),
  ];
  let parent = 'root';
  turns.forEach(([role, text], i) => {
    const node = `${id}-m${i}`;
    mapping[node] = {
      id: node,
      parent,
      message: { author: { role }, content: { content_type: 'text', parts: [text] }, create_time: T + i * 60, metadata: {} },
    };
    parent = node;
  });
  mapping.hidden = {
    id: 'hidden',
    parent: 'root',
    message: { author: { role: 'assistant' }, content: { content_type: 'text', parts: ['别的分支'] }, create_time: T },
  };
  return { conversation_id: id, title: '虚构对话\n第二行', create_time: T, update_time: T + 600, current_node: parent, mapping };
}

function claude() {
  const msg = (uuid, sender, text, parent, t, files = []) => ({
    uuid,
    sender,
    parent_message_uuid: parent,
    content: [{ type: 'text', text }, { type: 'tool_use', name: 'x' }],
    files,
    created_at: new Date((T + t) * 1000).toISOString(),
  });
  return {
    uuid: 'c1',
    name: 'Claude 虚构',
    created_at: new Date(T * 1000).toISOString(),
    updated_at: new Date((T + 300) * 1000).toISOString(),
    current_leaf_message_uuid: 'a-old',
    chat_messages: [
      msg('q', 'human', '问题', '00000000-0000-4000-8000-000000000000', 0, [{ file_name: '虚构.pdf' }]),
      msg('a-old', 'assistant', '当前分支', 'q', 60),
      msg('a-new', 'assistant', '更晚但没选的分支', 'q', 120),
    ],
  };
}

function gemini() {
  const turn = (rid, q, answers, chosen, t) => [['c_x', rid], null, [[q]], [answers, null, null, chosen], [T + t, 5e8]];
  return {
    conversationId: 'c_x',
    title: 'Gemini 虚构',
    updated: [T + 900, 0],
    url: 'https://gemini.google.com/u/1/app/x',
    turns: [turn('r2', '后问', [['b', ['后答']]], 'b', 300), turn('r1', '先问', [['a1', ['草稿']], ['a2', ['选中', '第二段']]], 'a2', 0)],
  };
}

test('F19_13 三家的原始对话转成统一结构：分支、时间、附件占位、选中的回答', () => {
  const g = toRecord('chatgpt', chatgpt());
  assert.equal(g.title, '虚构对话');
  assert.deepEqual(g.messages.map((m) => m.text), ['虚构的问题', '虚构的回答']);
  assert.equal(g.messages[1].time, new Date((T + 60) * 1000).toISOString());
  assert.equal(g.url, 'https://chatgpt.com/c/g1');

  const c = toRecord('claude', claude());
  assert.deepEqual(c.messages.map((m) => m.text), ['[附件：虚构.pdf]\n\n问题', '当前分支']);

  const m = toRecord('gemini', gemini());
  assert.deepEqual(m.messages.map((x) => x.text), ['先问', '选中\n\n第二段', '后问', '后答']);
  assert.equal(m.messages[0].time, m.messages[1].time);

  const md = toMarkdown(g);
  assert.match(md, /^---\ntitle: "虚构对话"\nplatform: chatgpt\nconversation_id: "g1"/);
  assert.match(md, /## 我 · \d{4}-\d\d-\d\d \d\d:\d\d\n\n虚构的问题/);
  assert.match(md, /## ChatGPT · /);

  assert.throws(() => toRecord('chatgpt', { title: '没有 mapping' }), FormatError);
  assert.throws(() => toRecord('gemini', { conversationId: 'c_y', turns: [[['c_y', 'r'], null, [['问']], [], null]] }), FormatError);
});

test('F19_14 存成文件：第一次全存，之后只存更新过的；文件名不变；可以同时存 JSON', async () => {
  const files = new Map();
  const known = {};
  const save = async (path, text) => files.set(path, text);
  let sink = localSink({ known, save, formats: ['md', 'json'] });

  const plan = await sink('POST', '/plan', { platform: 'chatgpt', items: [{ id: 'g1', updated: T + 600.7 }, { id: 'g2', updated: T }] });
  assert.deepEqual(plan.body.need, ['g1', 'g2']);
  const first = await sink('POST', '/conversation', { platform: 'chatgpt', data: chatgpt('g1') });
  assert.equal(first.body.result, '新建');
  const name = [...files.keys()].find((k) => k.endsWith('.md'));
  assert.match(name, /^ai-chat-sync\/chatgpt\/虚构对话 g1\.md$/);
  assert.ok(files.has(name.replace(/\.md$/, '.json')));

  // 下一次（插件重启后 known 从存储里读回来）：没变的不取，变了的取，标题改了也写回同一个文件
  sink = localSink({ known: JSON.parse(JSON.stringify(known)), save, formats: ['md'] });
  const again = await sink('POST', '/plan', { platform: 'chatgpt', items: [{ id: 'g1', updated: T + 600 }] });
  assert.deepEqual(again.body.need, []);
  const later = await sink('POST', '/plan', { platform: 'chatgpt', items: [{ id: 'g1', updated: T + 9999 }] });
  assert.deepEqual(later.body.need, ['g1']);
  const renamed = { ...chatgpt('g1', 2), title: '改过的标题' };
  const update = await sink('POST', '/conversation', { platform: 'chatgpt', data: renamed });
  assert.equal(update.body.result, '更新');
  assert.match(files.get(name), /追问 1/);

  const bad = await sink('POST', '/conversation', { platform: 'chatgpt', data: { title: '坏的' } });
  assert.equal(bad.status, 422);
});
