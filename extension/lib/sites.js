// 每个网站的接口地址和对话列表的读法（docs/specs/chat-sync.md 第 2 节）。
// 请求接口的写法改写自 pionxzh/chatgpt-exporter 的 src/api.ts 和
// agoramachina/claude-exporter 的 chrome/content.js；Gemini 的改写自作者自己的
// CaoLongyang/gemini-chat-exporter 的 gemini-export.js（都是 MIT，见仓库根目录的 THIRD_PARTY_NOTICES.md）。
//
// call(request) 由 sync.js 提供：在网站自己的网页里发请求，返回解析好的 JSON；
// 限速、没登录、出错都在 sync.js 里处理，这里只管地址和格式。

export const PAGE_SIZE = 100;
export const GEMINI_LIST_PAGE = 20; // Gemini 自己用 13 或 20
export const GEMINI_READ_PAGE = 1000; // 读一个对话时每次要多少轮

export const SITES = {
  chatgpt: {
    label: 'ChatGPT',
    home: 'https://chatgpt.com/',
    match: 'https://chatgpt.com/*',
    useToken: true, // 要先向 /api/auth/session 取临时令牌，只在网页里用
    world: 'ISOLATED',
    page: pageRequest,
    async prepare() {
      return {};
    },
    async list(call) {
      const items = [];
      for (let offset = 0; ; ) {
        const page = await call(
          `/backend-api/conversations?offset=${offset}&limit=${PAGE_SIZE}&order=updated`,
        );
        const rows = Array.isArray(page?.items) ? page.items : [];
        for (const row of rows) items.push({ id: row?.id, updated: row?.update_time });
        offset += rows.length;
        const total = typeof page?.total === 'number' ? page.total : null;
        if (rows.length === 0 || (total !== null && offset >= total)) break;
        if (total === null && rows.length < PAGE_SIZE) break;
      }
      return items;
    },
    conversationPath(id) {
      return `/backend-api/conversation/${encodeURIComponent(id)}`;
    },
    conversation(call, id, ctx) {
      return call(this.conversationPath(id, ctx));
    },
  },

  claude: {
    label: 'Claude',
    home: 'https://claude.ai/',
    match: 'https://claude.ai/*',
    useToken: false, // 网页自己的登录状态就够
    world: 'ISOLATED',
    page: pageRequest,
    async prepare(call) {
      const orgs = await call('/api/organizations');
      if (!Array.isArray(orgs) || orgs.length === 0) throw new Error('找不到 Claude 的组织编号');
      const chat = orgs.find((o) => Array.isArray(o?.capabilities) && o.capabilities.includes('chat'));
      return { org: (chat || orgs[0]).uuid };
    },
    async list(call, ctx) {
      const rows = await call(`/api/organizations/${ctx.org}/chat_conversations`);
      return (Array.isArray(rows) ? rows : []).map((row) => ({
        id: row?.uuid,
        updated: row?.updated_at,
      }));
    },
    conversationPath(id, ctx) {
      return (
        `/api/organizations/${ctx.org}/chat_conversations/${encodeURIComponent(id)}` +
        '?tree=True&rendering_mode=messages&render_all_tools=true'
      );
    },
    conversation(call, id, ctx) {
      return call(this.conversationPath(id, ctx));
    },
  },

  // Gemini 的 batchexecute 接口返回没有字段名的数组；位置是作者在 gemini-chat-exporter 里实测的
  // （DEVELOPMENT.md 第 13、14 节）。插件只把每一轮原样交给 eia，由 eia 拼成对话。
  gemini: {
    label: 'Gemini',
    home: 'https://gemini.google.com/app',
    match: 'https://gemini.google.com/*',
    useToken: false,
    world: 'MAIN', // 令牌（WIZ_global_data）在网页自己的变量里
    page: geminiRequest,
    async prepare() {
      return { meta: new Map(), home: null };
    },
    async list(call, ctx) {
      const items = [];
      // [0,null,1] 是普通列表；[1,null,1] 推测是置顶等其他分组，也取一遍以防漏
      for (const filter of [[0, null, 1], [1, null, 1]]) {
        let cursor = null;
        for (let page = 0; page < 1000; page += 1) {
          const { inner, prefix } = await call({
            rpc: 'MaZiqc',
            payload: [GEMINI_LIST_PAGE, cursor, filter],
            source: '/app',
          });
          ctx.prefix = prefix;
          ctx.home = `https://gemini.google.com${prefix}/app`;
          const rows = Array.isArray(inner?.[2]) ? inner[2] : [];
          let added = 0;
          for (const row of rows) {
            const id = row?.[0];
            if (typeof id !== 'string' || ctx.meta.has(id)) continue;
            ctx.meta.set(id, { title: row[1], updated: row[5] });
            items.push({ id, updated: seconds(row[5]) });
            added += 1;
          }
          const next = typeof inner?.[1] === 'string' ? inner[1] : null;
          if (!next || next === cursor || rows.length === 0 || added === 0) break;
          cursor = next;
        }
      }
      return items;
    },
    async conversation(call, id, ctx) {
      const plain = id.replace(/^c_/, '');
      const turns = new Map();
      let cursor = null;
      let possiblyIncomplete = false;
      for (let page = 0; page < 200; page += 1) {
        const { inner } = await call({
          rpc: 'hNvQHb',
          payload: [id, GEMINI_READ_PAGE, cursor, 1, [1], [4], null, 1],
          source: `/app/${plain}`,
        });
        const list = Array.isArray(inner?.[0]) ? inner[0] : [];
        let added = 0;
        for (const turn of list) {
          const key = turn?.[0]?.[1] || JSON.stringify(turn?.[4]);
          if (!turns.has(key)) {
            turns.set(key, turn);
            added += 1;
          }
        }
        // 对话太长时接口会给翻页标记（位置没完全确认，两处都看）
        const next = inner ? [inner[1], inner[2]].find((x) => typeof x === 'string' && x.length > 8) : null;
        if (!next || next === cursor) break;
        if (added === 0) {
          possiblyIncomplete = true;
          break;
        }
        cursor = next;
      }
      const meta = ctx.meta.get(id) || {};
      return {
        conversationId: id,
        title: meta.title,
        updated: meta.updated,
        url: `https://gemini.google.com${ctx.prefix || ''}/app/${plain}`,
        turns: [...turns.values()],
        possiblyIncomplete,
      };
    },
  },
};

function seconds(stamp) {
  return Array.isArray(stamp) && typeof stamp[0] === 'number' ? stamp[0] + (stamp[1] || 0) / 1e9 : null;
}

// 在网站的网页里运行（chrome.scripting.executeScript 注入，必须自成一体，不能引用外面的变量）。
// 只用取数据的 GET 请求；ChatGPT 的临时令牌只留在这个网页的内存里，几分钟后作废，不存、不外传。
export async function pageRequest(path, useToken) {
  try {
    const headers = {};
    if (useToken) {
      const cache = globalThis.__eiaSession;
      let token = cache && Date.now() - cache.at < 5 * 60 * 1000 ? cache.token : null;
      if (!token) {
        const session = await fetch('/api/auth/session', { credentials: 'include' });
        if (!session.ok) return { status: session.status };
        const body = await session.json();
        if (!body || !body.accessToken) return { status: 401 };
        token = body.accessToken;
        globalThis.__eiaSession = { token, at: Date.now() };
      }
      headers.Authorization = `Bearer ${token}`;
    }
    const response = await fetch(path, { headers, credentials: 'include' });
    if (response.status === 401 && useToken) globalThis.__eiaSession = null;
    if (!response.ok) {
      return { status: response.status, retryAfter: response.headers.get('Retry-After') };
    }
    return { status: response.status, json: await response.json() };
  } catch (error) {
    return { status: 0, error: String(error) };
  }
}

// Gemini：在网页的主环境里运行（要读网页自己的 WIZ_global_data），必须自成一体。
// 改写自 gemini-chat-exporter 的 rpc() 和 parseBatch()。只用取数据的接口（MaZiqc、hNvQHb）。
export async function geminiRequest(request) {
  try {
    const wiz = window.WIZ_global_data || {};
    if (!wiz.SNlM0e) return { status: 401 }; // 没登录
    const prefix = (location.pathname.match(/^\/u\/\d+/) || [''])[0];
    const hl = document.documentElement.lang || 'zh-CN';
    globalThis.__eiaReq = (globalThis.__eiaReq || Math.floor(Math.random() * 90000) + 10000) + 100000;
    const url =
      `${prefix}/_/BardChatUi/data/batchexecute?rpcids=${request.rpc}` +
      `&source-path=${encodeURIComponent(prefix + request.source)}&bl=${encodeURIComponent(wiz.cfb2h || '')}` +
      `&f.sid=${encodeURIComponent(wiz.FdrFJe || '')}&hl=${encodeURIComponent(hl)}&_reqid=${globalThis.__eiaReq}&rt=c`;
    const body = new URLSearchParams();
    body.set('f.req', JSON.stringify([[[request.rpc, JSON.stringify(request.payload), null, 'generic']]]));
    body.set('at', wiz.SNlM0e);
    const abort = new AbortController();
    const timer = setTimeout(() => abort.abort(), 45_000);
    let response;
    try {
      response = await fetch(url, {
        method: 'POST',
        credentials: 'include',
        headers: { 'content-type': 'application/x-www-form-urlencoded;charset=UTF-8', 'x-same-domain': '1' },
        body: body.toString(),
        signal: abort.signal,
      });
    } finally {
      clearTimeout(timer);
    }
    if (!response.ok) return { status: response.status, retryAfter: response.headers.get('Retry-After') };
    const text = await response.text();
    for (const line of text.split('\n')) {
      const trimmed = line.trim();
      if (!trimmed.startsWith('[')) continue;
      let rows;
      try {
        rows = JSON.parse(trimmed);
      } catch {
        continue; // 不完整的行
      }
      for (const row of rows) {
        if (Array.isArray(row) && row[0] === 'wrb.fr' && row[1] === request.rpc) {
          if (row[5]) return { status: 502, error: `接口返回错误 ${JSON.stringify(row[5])}` };
          return { status: 200, json: { inner: row[2] ? JSON.parse(row[2]) : null, prefix } };
        }
      }
    }
    return { status: 502, error: '返回格式认不出' };
  } catch (error) {
    return { status: 0, error: String(error) };
  }
}
