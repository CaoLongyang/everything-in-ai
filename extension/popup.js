import { SITES } from './lib/sites.js';

const DEFAULTS = { port: 8771, token: '', paused: false, sites: { chatgpt: true, claude: true } };
const $ = (id) => document.getElementById(id);

async function load() {
  const { settings: saved, records = {} } = await chrome.storage.local.get(['settings', 'records']);
  const config = { ...DEFAULTS, ...(saved || {}), sites: { ...DEFAULTS.sites, ...(saved?.sites || {}) } };
  return { config, records };
}

function describe(record) {
  if (!record) return { text: '还没同步过', cls: 'state' };
  const when = record.at ? new Date(record.at).toLocaleString() : '';
  if (record.error) return { text: `${when}：${record.error}`, cls: 'error' };
  const counts = record.result?.counts || {};
  const parts = Object.entries(counts).map(([k, v]) => `${k} ${v}`);
  const text = parts.length ? parts.join('，') : '没有更新过的对话';
  return { text: `${when}：${text}`, cls: 'good' };
}

async function render() {
  const { config, records } = await load();
  $('sites').replaceChildren(
    ...Object.entries(SITES).map(([name, site]) => {
      const box = document.createElement('div');
      box.className = 'site';
      const head = document.createElement('header');
      head.textContent = site.label;
      const state = document.createElement('span');
      state.className = 'state';
      state.textContent = config.sites[name] ? '' : '已关闭';
      head.append(state);
      const line = document.createElement('div');
      const { text, cls } = describe(records[name]);
      line.className = cls;
      line.textContent = text;
      box.append(head, line);
      return box;
    }),
  );
  $('pause').textContent = config.paused ? '继续自动同步' : '暂停自动同步';
  $('port').value = config.port;
  $('token').value = config.token;
  $('site-chatgpt').checked = config.sites.chatgpt;
  $('site-claude').checked = config.sites.claude;
  if (!config.token) $('setup').open = true;
}

$('now').addEventListener('click', async () => {
  $('now').disabled = true;
  $('now').textContent = '同步中…';
  await chrome.runtime.sendMessage({ type: 'sync-now' });
});

$('pause').addEventListener('click', async () => {
  const { config } = await load();
  await chrome.storage.local.set({ settings: { ...config, paused: !config.paused } });
  render();
});

$('save').addEventListener('click', async () => {
  const { config } = await load();
  const port = Number($('port').value) || DEFAULTS.port;
  const next = {
    ...config,
    port,
    token: $('token').value.trim(),
    sites: { chatgpt: $('site-chatgpt').checked, claude: $('site-claude').checked },
  };
  await chrome.storage.local.set({ settings: next });
  $('saved').textContent = '已保存';
  render();
});

chrome.storage.onChanged.addListener(() => render());
render();
