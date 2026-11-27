// 同步一个网站：取对话列表 → 问 eia 哪些要取 → 逐个取内容发给 eia（docs/specs/chat-sync.md 第 4.2 节）。
// 不碰 chrome.* ，浏览器相关的都由调用方传进来，方便用 node --test 测。
import { t } from './i18n.js';

export const GAP_MS = 2000; // 两次请求网站之间至少隔多久
export const DEFAULT_WAIT_MS = 60_000; // 被限速、网站没说等多久时
export const MAX_LIMITED = 3; // 一天被限速几次就停到明天
export const MAX_FORMAT_ERRORS = 3; // 连续几个对话格式认不出就停（网站可能改版）

export class SyncStop extends Error {
  // kind：login 没登录；limited 被限速太多；format 格式认不出；eia eia 没开；site 网站出错
  constructor(kind, message) {
    super(message);
    this.kind = kind;
  }
}

// deps:
//   req(request, site)   在网页里请求网站（request 是地址，Gemini 是接口编号和参数），返回 { status, json?, retryAfter? }
//   eia(method, path, body)  请求本机的 eia，返回 { status, body }；连不上时 status 为 0
//   sleep(ms)            等待
//   now()                现在的毫秒数
//   state                { limited: 今天被限速的次数 }，会被修改，调用方负责存下来
//   lang                 提示用中文（'zh'，默认）还是英文（'en'）
export async function syncSite(name, site, deps) {
  const lang = deps.lang || 'zh';
  const counts = {};
  const status = await deps.eia('GET', '/status');
  if (status.status !== 200) {
    throw new SyncStop('eia', t(lang, status.status === 401 ? 'badToken' : 'eiaDown'));
  }
  const call = rateLimited(site, deps);
  const ctx = await site.prepare(call);
  const items = await site.list(call, ctx);
  const plan = await deps.eia('POST', '/plan', { platform: name, items });
  if (plan.status !== 200) {
    throw new SyncStop('eia', t(lang, 'eiaReply', { status: plan.status, error: plan.body?.error || '' }));
  }
  const need = Array.isArray(plan.body?.need) ? plan.body.need : [];

  let formatErrors = 0;
  for (const id of need) {
    const data = await site.conversation(call, id, ctx);
    const reply = await deps.eia('POST', '/conversation', { platform: name, data });
    if (reply.status === 0) throw new SyncStop('eia', t(lang, 'eiaStopped'));
    if (reply.status === 422) {
      bump(counts, '格式认不出');
      formatErrors += 1;
      if (formatErrors >= MAX_FORMAT_ERRORS) {
        throw new SyncStop('format', t(lang, 'formatBroken', { site: site.label, count: formatErrors }));
      }
      continue;
    }
    formatErrors = 0;
    bump(counts, reply.status === 200 ? reply.body?.result || '?' : `HTTP ${reply.status}`);
  }
  return { listed: items.length, needed: need.length, counts, home: ctx.home };
}

// 包一层：请求之间至少隔 GAP_MS；429 按 Retry-After 等，一天 MAX_LIMITED 次就停；没登录、出错就停。
export function rateLimited(site, deps) {
  const lang = deps.lang || 'zh';
  let last = -Infinity;
  return async function call(request) {
    for (;;) {
      const wait = last + GAP_MS - deps.now();
      if (wait > 0) await deps.sleep(wait);
      last = deps.now();
      const response = await deps.req(request, site);
      last = deps.now();
      if (response.status === 200) return response.json;
      if (response.status === 429) {
        deps.state.limited = (deps.state.limited || 0) + 1;
        if (deps.state.limited >= MAX_LIMITED) {
          throw new SyncStop('limited', t(lang, 'limited', { site: site.label, count: deps.state.limited }));
        }
        await deps.sleep(retryAfterMs(response.retryAfter));
        continue;
      }
      if (response.status === 401 || response.status === 403) {
        throw new SyncStop('login', t(lang, 'login', { site: site.label }));
      }
      throw new SyncStop('site', t(lang, 'siteError', { site: site.label, status: response.status || t(lang, 'unreachable') }));
    }
  };
}

export function retryAfterMs(value) {
  const seconds = Number(value);
  if (value !== null && value !== undefined && value !== '' && Number.isFinite(seconds) && seconds >= 0) {
    return Math.max(seconds * 1000, GAP_MS);
  }
  return DEFAULT_WAIT_MS;
}

// 今天要不要同步这个网站：开着、今天还没成功、今天没有因为限速或改版停掉。
export function shouldRun(record, today) {
  if (!record || record.day !== today) return true;
  return !record.done && !record.stopped;
}

// 一次同步结束后，这个网站今天的记录。eia 没开、中途停了的，下个小时再试。
export function afterRun(record, today, outcome) {
  const base = record && record.day === today ? record : { day: today, limited: 0 };
  const next = { ...base, at: outcome.at, limited: outcome.limited ?? base.limited };
  if (outcome.error) {
    next.done = false;
    next.error = outcome.error.message;
    next.stopped = ['limited', 'format'].includes(outcome.error.kind);
  } else {
    next.done = true;
    next.error = '';
    next.result = outcome.result;
  }
  return next;
}

export function localDay(ms) {
  const d = new Date(ms);
  const pad = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

function bump(counts, key) {
  counts[key] = (counts[key] || 0) + 1;
}
