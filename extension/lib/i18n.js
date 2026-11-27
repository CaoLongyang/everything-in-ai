// 界面、提示和存下来的 Markdown 用中文还是英文（docs/specs/chat-sync.delta-standalone.md 第 6 条）。
// 设置里的语言：'auto' 跟随浏览器（中文浏览器用中文，其他用英文）、'zh'、'en'。

const STRINGS = {
  zh: {
    // 存下来的 Markdown
    you: '我',
    untitled: '未命名对话',
    unknownTime: '时间未知',
    image: '[图片]',
    voice: '（语音）',
    attachment: '[附件：{name}]',
    // 同步时的提示
    badToken: '口令不对：在插件里重新填 eia sync token 显示的口令',
    eiaDown: 'eia 没开：运行 eia sync serve',
    eiaReply: 'eia 回复 {status}：{error}',
    eiaStopped: 'eia 中途停了，稍后接着同步',
    formatBroken: '{site} 连续 {count} 个对话格式认不出，网站可能改版了',
    limited: '{site} 今天被限速 {count} 次，明天再同步',
    login: '请先在浏览器里登录 {site}',
    siteError: '{site} 回复 {status}',
    unreachable: '连不上',
    // 结果（eia 回的也是这几个中文词）
    新建: '新建',
    更新: '更新',
    未变: '未变',
    跳过: '跳过',
    格式认不出: '格式认不出',
    // 弹窗
    title: 'eia 对话同步',
    lead: '每天一次，把 ChatGPT、Claude、Gemini 上更新过的对话存到你的电脑。内容不出电脑。',
    never: '还没同步过',
    nothingNew: '没有更新过的对话',
    off: '已关闭',
    syncNow: '现在同步',
    syncing: '同步中…',
    pause: '暂停自动同步',
    resume: '继续自动同步',
    settings: '设置',
    whereTo: '存到哪里',
    modeFiles: '存成文件：放进「下载」文件夹的 ai-chat-sync 里，每个对话一个文件',
    fmtMd: 'Markdown（.md，适合用 Obsidian、记事本看）',
    fmtJson: '网站返回的原始数据（.json，适合给别的程序用）',
    modeEia: '发给 eia：写进 eia 的知识库（要先装 eia）',
    port: '端口',
    token: '口令',
    eiaHint: '先在电脑上运行 eia sync autostart --vault 你的vault，再把 eia sync token --vault 你的vault 显示的口令填在这里。',
    which: '同步哪些',
    geminiHint: '（登录了多个 Google 账号时，先打开要同步的那个账号的 Gemini）',
    language: '语言',
    langAuto: '跟随浏览器',
    save: '保存',
    saved: '已保存',
  },
  en: {
    you: 'You',
    untitled: 'Untitled',
    unknownTime: 'unknown time',
    image: '[image]',
    voice: '(voice) ',
    attachment: '[attachment: {name}]',
    badToken: 'Wrong token: paste the one shown by `eia sync token` into the settings',
    eiaDown: 'eia is not running: start it with `eia sync serve`',
    eiaReply: 'eia replied {status}: {error}',
    eiaStopped: 'eia stopped halfway; will continue later',
    formatBroken: '{site}: {count} conversations in a row had an unreadable format; the site may have changed',
    limited: '{site} rate-limited us {count} times today; will try again tomorrow',
    login: 'Please sign in to {site} in this browser first',
    siteError: '{site} replied {status}',
    unreachable: 'unreachable',
    新建: 'new',
    更新: 'updated',
    未变: 'unchanged',
    跳过: 'skipped',
    格式认不出: 'unreadable format',
    title: 'AI chat sync',
    lead: 'Once a day, saves new and updated ChatGPT, Claude and Gemini conversations to your computer. Nothing leaves your machine.',
    never: 'Not synced yet',
    nothingNew: 'No updated conversations',
    off: 'Off',
    syncNow: 'Sync now',
    syncing: 'Syncing…',
    pause: 'Pause daily sync',
    resume: 'Resume daily sync',
    settings: 'Settings',
    whereTo: 'Save to',
    modeFiles: 'Files: one file per conversation in Downloads/ai-chat-sync',
    fmtMd: 'Markdown (.md, for Obsidian or any text editor)',
    fmtJson: 'Raw data from the site (.json, for other programs)',
    modeEia: 'eia: write into an everything-in-ai vault (install eia first)',
    port: 'Port',
    token: 'Token',
    eiaHint: 'Run `eia sync autostart --vault <your vault>`, then paste the token shown by `eia sync token --vault <your vault>` here.',
    which: 'Sites',
    geminiHint: ' (with several Google accounts, open Gemini in the account you want first)',
    language: 'Language',
    langAuto: 'Same as browser',
    save: 'Save',
    saved: 'Saved',
  },
};

export function resolveLang(setting, uiLanguage) {
  if (setting === 'zh' || setting === 'en') return setting;
  return /^zh/i.test(uiLanguage || '') ? 'zh' : 'en';
}

export function t(lang, key, vars = {}) {
  const table = STRINGS[lang] || STRINGS.zh;
  const text = table[key] ?? STRINGS.zh[key] ?? key;
  return text.replace(/\{(\w+)\}/g, (_, name) => String(vars[name] ?? ''));
}
