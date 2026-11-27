import { SITES } from './lib/sites.js';

const DEFAULTS = {
  mode: '',
  formats: ['md'],
  port: 27771,
  token: '',
  paused: false,
  sites: { chatgpt: true, claude: true, gemini: true },
};
const $ = (id) => document.getElementById(id);

async function load() {
  const { settings: saved, records = {} } = await chrome.storage.local.get(['settings', 'records']);
  const config = { ...DEFAULTS, ...(saved || {}), sites: { ...DEFAULTS.sites, ...(saved?.sites || {}) } };
  config.mode = config.mode || (config.token ? 'eia' : 'files');
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
  for (const radio of document.querySelectorAll('input[name=mode]')) radio.checked = radio.value === config.mode;
  $('fmt-md').checked = config.formats.includes('md');
  $('fmt-json').checked = config.formats.includes('json');
  showMode(config.mode);
  $('port').value = config.port;
  $('token').value = config.token;
  $('site-chatgpt').checked = config.sites.chatgpt;
  $('site-claude').checked = config.sites.claude;
  $('site-gemini').checked = config.sites.gemini;
  if (config.mode === 'eia' && !config.token) $('setup').open = true;
}

$('now').addEventListener('click', async () => {
  await save(); // 填了口令没点「保存」就点同步，也照样用新填的
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
  await save();
  $('saved').textContent = '已保存';
  render();
});

async function save() {
  const { config } = await load();
  const port = Number($('port').value) || DEFAULTS.port;
  const mode = document.querySelector('input[name=mode]:checked')?.value || 'files';
  const formats = [$('fmt-md').checked && 'md', $('fmt-json').checked && 'json'].filter(Boolean);
  const next = {
    ...config,
    mode,
    formats: formats.length ? formats : ['md'],
    port,
    token: $('token').value.replace(/\s+/g, ''), // 复制时容易带上空格、换行
    sites: {
      chatgpt: $('site-chatgpt').checked,
      claude: $('site-claude').checked,
      gemini: $('site-gemini').checked,
    },
  };
  await chrome.storage.local.set({ settings: next });
}

function showMode(mode) {
  $('files-box').hidden = mode !== 'files';
  $('eia-box').hidden = mode !== 'eia';
}

for (const radio of document.querySelectorAll('input[name=mode]')) {
  radio.addEventListener('change', () => showMode(radio.value));
}

chrome.storage.onChanged.addListener(() => render());
render();
