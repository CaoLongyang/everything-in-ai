// 不装 eia 时的「接收端」：和 eia sync serve 的三个接口（/status、/plan、/conversation）一样的回复，
// 但不发到任何地方，而是把对话转成文件存进浏览器的「下载」文件夹。
// sync.js 不用区分这两种情况。
import { FormatError, fileName, toMarkdown, toRecord } from './convert.js';

export const FOLDER = 'ai-chat-sync';

// known：{ [platform]: { [id]: { updated, name } } }，哪些对话存过、存成什么名字；由调用方存下来
// save(path, text, mime)：写一个文件（相对「下载」文件夹）
// formats：['md'] / ['json'] / ['md', 'json']
export function localSink({ known, save, formats = ['md'] }) {
  const listed = {}; // 这次列表里每个对话的更新时间，存完后记下来，下次和它比
  return async function eia(method, path, body) {
    if (path === '/status') return { status: 200, body: { ok: true } };
    const platform = body?.platform;
    const mine = (known[platform] ||= {});
    if (path === '/plan') {
      const need = [];
      for (const item of body.items || []) {
        if (typeof item?.id !== 'string') continue;
        const updated = toSeconds(item.updated);
        listed[`${platform}:${item.id}`] = updated;
        const seen = mine[item.id];
        if (!seen || updated === null || seen.updated === null || updated > seen.updated) need.push(item.id);
      }
      return { status: 200, body: { need: [...new Set(need)] } };
    }
    if (path === '/conversation') {
      let rec;
      try {
        rec = toRecord(platform, body.data);
      } catch (error) {
        if (error instanceof FormatError) return { status: 422, body: { error: error.message } };
        throw error;
      }
      if (rec.messages.length === 0) return { status: 200, body: { result: '跳过', reason: '没有文字消息' } };
      const before = mine[rec.id];
      const name = before?.name || fileName(rec);
      if (formats.includes('md')) await save(`${FOLDER}/${platform}/${name}.md`, toMarkdown(rec), 'text/markdown');
      if (formats.includes('json')) {
        await save(`${FOLDER}/${platform}/${name}.json`, JSON.stringify(body.data, null, 1), 'application/json');
      }
      const updated = listed[`${platform}:${rec.id}`] ?? toSeconds(rec.updated);
      mine[rec.id] = { updated, name };
      return { status: 200, body: { result: before ? '更新' : '新建' } };
    }
    return { status: 404, body: { error: '没有这个地址' } };
  };
}

function toSeconds(value) {
  if (typeof value === 'number' && Number.isFinite(value)) return Math.floor(value);
  if (typeof value === 'string') {
    const ms = Date.parse(value);
    return Number.isFinite(ms) ? Math.floor(ms / 1000) : null;
  }
  return null;
}
