// 插件的后台：每天一次，把 ChatGPT、Claude 上更新过的对话发给本机的 eia（docs/specs/chat-sync.md）。
import { SITES } from './lib/sites.js';
import { SyncStop, afterRun, localDay, shouldRun, syncSite } from './lib/sync.js';

const DEFAULTS = { port: 27771, token: '', paused: false, sites: { chatgpt: true, claude: true, gemini: true } };
const FIRST_DELAY_MIN = 10; // 打开浏览器 10 分钟后开始
const RETRY_MIN = 60; // 没成功的，每小时再试
let running = false;

chrome.runtime.onInstalled.addListener(() => schedule());
chrome.runtime.onStartup.addListener(() => schedule());
chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === 'tick') runAll({ manual: false });
});
chrome.runtime.onMessage.addListener((message, _sender, reply) => {
  if (message?.type === 'sync-now') {
    runAll({ manual: true });
    reply({ started: !running });
  }
  return false;
});

function schedule() {
  chrome.alarms.create('tick', { delayInMinutes: FIRST_DELAY_MIN, periodInMinutes: RETRY_MIN });
}

export async function settings() {
  const { settings: saved } = await chrome.storage.local.get('settings');
  return { ...DEFAULTS, ...(saved || {}), sites: { ...DEFAULTS.sites, ...(saved?.sites || {}) } };
}

async function runAll({ manual }) {
  if (running) return;
  running = true;
  try {
    const config = await settings();
    if (config.paused && !manual) return;
    const { records = {} } = await chrome.storage.local.get('records');
    for (const [name, site] of Object.entries(SITES)) {
      if (!config.sites[name]) continue;
      const today = localDay(Date.now());
      if (!manual && !shouldRun(records[name], today)) continue;
      records[name] = await runOne(name, site, config, records[name], today);
      await chrome.storage.local.set({ records });
    }
    await badge(records);
  } finally {
    running = false;
  }
}

async function runOne(name, site, config, record, today) {
  const state = { limited: record?.day === today ? record.limited || 0 : 0 };
  let tab = null;
  let opened = false;
  try {
    [tab] = await chrome.tabs.query({ url: site.match });
    if (!tab) {
      // Gemini 登录了多个 Google 账号时，用上次同步成功的那个账号（/u/1 之类）
      const home = record?.home || site.home;
      tab = await chrome.tabs.create({ url: home, active: false });
      opened = true;
      await loaded(tab.id);
    }
    const result = await syncSite(name, site, {
      req: (request, which) => inPage(tab.id, request, which),
      eia: (method, path, body) => eia(config, method, path, body),
      sleep: keepAliveSleep,
      now: () => Date.now(),
      state,
    });
    const done = afterRun(record, today, { at: Date.now(), limited: state.limited, result });
    return { ...done, home: result.home || record?.home };
  } catch (error) {
    const stop = error instanceof SyncStop ? error : new SyncStop('site', String(error?.message || error));
    const failed = afterRun(record, today, { at: Date.now(), limited: state.limited, error: stop });
    return { ...failed, home: record?.home };
  } finally {
    if (opened && tab) chrome.tabs.remove(tab.id).catch(() => {});
  }
}

async function inPage(tabId, request, site) {
  const [frame] = await chrome.scripting.executeScript({
    target: { tabId },
    func: site.page,
    args: [request, site.useToken],
    world: site.world, // Gemini 的令牌在网页自己的变量里，要在网页的主环境里运行
  });
  return frame?.result || { status: 0 };
}

async function eia(config, method, path, body) {
  try {
    const response = await fetch(`http://127.0.0.1:${config.port}${path}`, {
      method,
      headers: { 'Content-Type': 'application/json', 'X-Eia-Token': config.token },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    return { status: response.status, body: await response.json().catch(() => ({})) };
  } catch {
    return { status: 0, body: {} };
  }
}

// 后台的 service worker 闲着 30 秒会被浏览器停掉；长时间等待时每 20 秒碰一下插件接口，让它保持运行。
async function keepAliveSleep(ms) {
  const end = Date.now() + ms;
  while (Date.now() < end) {
    await new Promise((resolve) => setTimeout(resolve, Math.min(20_000, end - Date.now())));
    await chrome.storage.local.get('settings');
  }
}

function loaded(tabId) {
  return new Promise((resolve) => {
    const timer = setTimeout(done, 60_000);
    function done() {
      clearTimeout(timer);
      chrome.tabs.onUpdated.removeListener(listen);
      setTimeout(resolve, 3000); // 等网页自己的脚本跑起来
    }
    function listen(id, info) {
      if (id === tabId && info.status === 'complete') done();
    }
    chrome.tabs.onUpdated.addListener(listen);
  });
}

async function badge(records) {
  const failed = Object.values(records).some((r) => r?.error);
  await chrome.action.setBadgeText({ text: failed ? '!' : '' });
  await chrome.action.setBadgeBackgroundColor({ color: '#c0392b' });
}
