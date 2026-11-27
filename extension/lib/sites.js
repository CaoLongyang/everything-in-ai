// 每个网站的接口地址和对话列表的读法（docs/specs/chat-sync.md 第 2 节）。
// 请求接口的写法改写自 pionxzh/chatgpt-exporter 的 src/api.ts 和
// agoramachina/claude-exporter 的 chrome/content.js（都是 MIT，见仓库根目录的 THIRD_PARTY_NOTICES.md）。
//
// call(path) 由 sync.js 提供：在网站自己的网页里发请求，返回解析好的 JSON；
// 限速、没登录、出错都在 sync.js 里处理，这里只管地址和格式。

export const PAGE_SIZE = 100;

export const SITES = {
  chatgpt: {
    label: 'ChatGPT',
    home: 'https://chatgpt.com/',
    match: 'https://chatgpt.com/*',
    useToken: true, // 要先向 /api/auth/session 取临时令牌，只在网页里用
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
  },

  claude: {
    label: 'Claude',
    home: 'https://claude.ai/',
    match: 'https://claude.ai/*',
    useToken: false, // 网页自己的登录状态就够
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
  },
};

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
