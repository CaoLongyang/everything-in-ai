// 不装 eia 时用：把三家网站接口返回的原始对话转成统一的结构，再写成 Markdown / JSON。
// 规则和 eia 的导入一致（docs/specs/chat-sync.delta-standalone.md）：
//   ChatGPT：从 current_node 沿 parent 走回开头；只要文字（text / multimodal_text），图片留占位，隐藏的消息不要
//   Claude：走到当前分支（current_leaf_message_uuid，没有就取最晚的叶子）；只要 text 块，附件留占位
//   Gemini：按时间排每一轮；只取选中的回答；同一轮的提问和回答共用时间
// 不认识的格式抛 FormatError，调用方记成「格式认不出」，不写文件。

export class FormatError extends Error {}

const NAMES = { chatgpt: 'ChatGPT', claude: 'Claude', gemini: 'Gemini' };

export function toRecord(platform, data) {
  if (!data || typeof data !== 'object') throw new FormatError('对话应为一个对象');
  if (platform === 'chatgpt') return chatgpt(data);
  if (platform === 'claude') return claude(data);
  if (platform === 'gemini') return gemini(data);
  throw new FormatError(`不认识的平台：${platform}`);
}

function chatgpt(data) {
  const id = data.conversation_id || data.id;
  const mapping = data.mapping;
  if (typeof id !== 'string' || !mapping || typeof mapping !== 'object') {
    throw new FormatError('ChatGPT：缺少 conversation_id 或 mapping');
  }
  if (!(data.current_node in mapping)) throw new FormatError('ChatGPT：current_node 指向不存在的消息');
  const created = seconds(data.create_time);
  const path = [];
  const seen = new Set();
  for (let node = data.current_node; typeof node === 'string'; node = mapping[node]?.parent) {
    if (seen.has(node) || !mapping[node]) throw new FormatError('ChatGPT：消息树坏了');
    seen.add(node);
    path.push(mapping[node]);
  }
  path.reverse();
  const messages = [];
  for (const node of path) {
    const message = node.message;
    const role = message?.author?.role;
    if (role !== 'user' && role !== 'assistant') continue;
    if (message.metadata?.is_visually_hidden_from_conversation) continue;
    const content = message.content || {};
    if (!['text', 'multimodal_text'].includes(content.content_type)) continue;
    const text = (Array.isArray(content.parts) ? content.parts : [])
      .map((part) => {
        if (typeof part === 'string') return part.trim();
        if (part?.content_type === 'image_asset_pointer') return '[图片]';
        if (part?.content_type === 'audio_transcription' && part.text) return `（语音）${part.text.trim()}`;
        return '';
      })
      .filter(Boolean)
      .join('\n\n');
    if (!text) continue;
    messages.push({ role, time: iso(seconds(message.create_time) ?? created), text });
  }
  return record('chatgpt', id, data.title, `https://chatgpt.com/c/${id}`, created, seconds(data.update_time), messages);
}

function claude(data) {
  const id = data.uuid;
  if (typeof id !== 'string' || !Array.isArray(data.chat_messages)) {
    throw new FormatError('Claude：缺少 uuid 或 chat_messages');
  }
  const byId = new Map(data.chat_messages.filter((m) => m && typeof m.uuid === 'string').map((m) => [m.uuid, m]));
  const parents = new Set(data.chat_messages.map((m) => m?.parent_message_uuid));
  let leaf = byId.get(data.current_leaf_message_uuid);
  if (!leaf) {
    const leaves = [...byId.values()].filter((m) => !parents.has(m.uuid));
    leaf = leaves.sort((a, b) => String(a.created_at).localeCompare(String(b.created_at))).at(-1);
  }
  const path = [];
  const seen = new Set();
  for (let m = leaf; m; m = byId.get(m.parent_message_uuid)) {
    if (seen.has(m.uuid)) throw new FormatError('Claude：消息树坏了');
    seen.add(m.uuid);
    path.push(m);
  }
  path.reverse();
  const messages = [];
  for (const m of path) {
    const role = m.sender === 'human' ? 'user' : m.sender === 'assistant' ? 'assistant' : null;
    if (!role) continue;
    const blocks = Array.isArray(m.content) ? m.content : [];
    const text = blocks
      .filter((b) => b?.type === 'text' && typeof b.text === 'string')
      .map((b) => b.text.trim())
      .filter(Boolean)
      .join('\n\n');
    const files = [...(m.files || []), ...(m.attachments || [])]
      .map((f) => f?.file_name)
      .filter(Boolean)
      .map((name) => `[附件：${name}]`)
      .join('\n');
    const body = [role === 'user' ? files : '', text, role === 'user' ? '' : files].filter(Boolean).join('\n\n');
    if (!body) continue;
    messages.push({ role, time: isoString(m.created_at), text: body });
  }
  return record(
    'claude',
    id,
    data.name,
    `https://claude.ai/chat/${id}`,
    stringSeconds(data.created_at),
    stringSeconds(data.updated_at),
    messages,
  );
}

// 每一轮：[0]=[c_id, r_id]  [2][0][0]=提问  [3][0]=回答候选  [3][3]=选中的候选  [4]=[秒, 纳秒]
function gemini(data) {
  const id = data.conversationId;
  if (typeof id !== 'string' || !Array.isArray(data.turns)) throw new FormatError('Gemini：缺少 conversationId 或 turns');
  const turns = data.turns.map((turn) => ({ turn, at: stamp(turn?.[4]) }));
  if (turns.some((t) => t.at === null)) throw new FormatError('Gemini：有一轮没有时间');
  turns.sort((a, b) => a.at - b.at);
  const messages = [];
  for (const { turn, at } of turns) {
    const question = turn?.[2]?.[0]?.[0];
    if (typeof question === 'string' && question) messages.push({ role: 'user', time: iso(at), text: question });
    const response = turn?.[3];
    const candidates = Array.isArray(response?.[0]) ? response[0] : [];
    const chosen = candidates.find((c) => response?.[3] != null && c?.[0] === response[3]) || candidates[0];
    const answer = Array.isArray(chosen?.[1]) ? chosen[1].filter((x) => typeof x === 'string').join('\n\n') : '';
    if (answer) messages.push({ role: 'assistant', time: iso(at), text: answer });
  }
  const first = turns.length ? turns[0].at : null;
  return record('gemini', id, data.title, data.url, first, stamp(data.updated), messages);
}

function record(platform, id, title, url, created, updated, messages) {
  const times = messages.map((m) => Date.parse(m.time) / 1000).filter(Number.isFinite);
  const newest = Math.max(created ?? -Infinity, updated ?? -Infinity, ...times);
  return {
    platform,
    id,
    title: typeof title === 'string' && title.trim() ? title.trim().split('\n')[0] : '未命名对话',
    url: url || null,
    created: iso(created ?? (times.length ? Math.min(...times) : null)),
    updated: iso(Number.isFinite(newest) ? newest : null),
    messages,
  };
}

export function toMarkdown(rec) {
  const yaml = (value) => JSON.stringify(value ?? null);
  const lines = [
    '---',
    `title: ${yaml(rec.title)}`,
    `platform: ${rec.platform}`,
    `conversation_id: ${yaml(rec.id)}`,
    `url: ${yaml(rec.url)}`,
    `created: ${yaml(rec.created)}`,
    `updated: ${yaml(rec.updated)}`,
    `message_count: ${rec.messages.length}`,
    'source: ai-chat-sync',
    '---',
    '',
    `# ${rec.title}`,
    '',
  ];
  for (const m of rec.messages) {
    const who = m.role === 'user' ? '我' : NAMES[rec.platform];
    lines.push(`## ${who} · ${localTime(m.time)}`, '', m.text, '');
  }
  return lines.join('\n');
}

export function fileName(rec) {
  const safe = rec.title.replace(/[\\/:*?"<>|\u0000-\u001f]/g, ' ').replace(/\s+/g, ' ').trim().slice(0, 60) || '未命名对话';
  const short = rec.id.replace(/^c_/, '').replace(/-/g, '').slice(0, 8);
  return `${safe.replace(/[. ]+$/, '')} ${short}`;
}

function localTime(value) {
  if (!value) return '时间未知';
  const d = new Date(value);
  const pad = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function seconds(value) {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function stringSeconds(value) {
  const ms = typeof value === 'string' ? Date.parse(value) : NaN;
  return Number.isFinite(ms) ? ms / 1000 : null;
}

function isoString(value) {
  const s = stringSeconds(value);
  if (s === null) throw new FormatError('Claude：消息没有时间');
  return iso(s);
}

function stamp(value) {
  if (Array.isArray(value) && typeof value[0] === 'number') return value[0] + (value[1] || 0) / 1e9;
  return seconds(value);
}

function iso(secondsValue) {
  return secondsValue === null || secondsValue === undefined ? null : new Date(secondsValue * 1000).toISOString();
}
