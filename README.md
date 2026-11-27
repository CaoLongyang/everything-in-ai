# everything-in-ai

把你和 AI 的对话、你关注的新闻和网页，沉淀成一个纯 Markdown 的个人知识库。
数据是一个普通文件夹（vault），用 [Obsidian](https://obsidian.md) 就能打开，不绑定任何软件。

能做的事：

- **导入 AI 对话**：ChatGPT、Claude 的官方导出和 Gemini（浏览器插件导出） → 一个对话一个 Markdown 文件；重复导入只更新，不重复，你加的标签和笔记都保留。
- **新闻雷达**：每天抓一批新闻源，多家独立媒体都在报的事才写进来，大事不漏、不被刷屏。
- **兴趣简报**：从你自己说过的话里归纳兴趣，按兴趣挑新闻，你的评价会让它越来越准。
- **网页剪藏**：Obsidian Web Clipper 的模板，剪下的网页直接进知识库。
- **搜索**：所有条目全文搜索，中文可用。
- **定时运行**：每天自动跑新闻雷达和简报。

所有文档一览见 `docs/README.md`；进度见 `tasks/todo.md`，各版本的范围见 `docs/ROADMAP.md`。

## 安装

需要 Python 3.11 或更高版本（[python.org](https://www.python.org/downloads/) 下载；Windows 安装时勾上「Add python.exe to PATH」）。
项目还没有发布到 PyPI，从 GitHub 下载代码后安装：

**Windows（PowerShell）**

```powershell
git clone https://github.com/CaoLongyang/everything-in-ai.git
cd everything-in-ai
py -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[llm,news]"
```

如果 `Activate.ps1` 被系统拦住，先运行一次：`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`

**macOS / Linux（终端）**

```bash
git clone https://github.com/CaoLongyang/everything-in-ai.git
cd everything-in-ai
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[llm,news]"
```

方括号里是可选功能，只导入对话可以只装 `pip install -e .`：

| 选项 | 给什么用 |
|---|---|
| `llm` | 接入 AI 服务（兴趣草稿起名、根据评价改进兴趣） |
| `news` | 新闻雷达、兴趣简报 |
| `dev` | 改代码的人用：测试和检查工具（包含上面两项） |

装好后运行 `eia --help` 能看到命令列表就成功了。以后每次打开新终端，先进入代码文件夹激活环境
（Windows `.venv\Scripts\Activate.ps1`，macOS / Linux `source .venv/bin/activate`）再用 `eia`。

下面的例子用 macOS / Linux 的路径写法（`~/Documents/...`）；Windows 上把 `~` 换成 `$HOME`、`/` 换成 `\` 即可。

## 1. 创建 vault

vault 放在代码文件夹**外面**，比如文档文件夹：

```bash
eia init ~/Documents/my-vault      # 创建目录结构和 config.yaml
cd ~/Documents/my-vault
eia new note --title "第一篇笔记"   # 新建条目
eia validate                        # 检查格式，只报告不修改
```

然后用 Obsidian 的「打开本地仓库」打开 `my-vault` 文件夹。
`config.yaml` 里第一项是时区（`timezone: Asia/Singapore` 这样写），留空就用电脑的时区。

条目类型：`note` 笔记、`topic` 主题、`clip` 剪藏、`chat` 对话、`digest` 简报、`headline` 大事。格式规范见 `docs/specs/vault-schema.md`。

## 2. 导入 AI 对话

先在平台上申请导出，收到邮件后下载 zip，**不用解压**。建议先加 `--dry-run` 预览，确认无误再真正导入。

```bash
# ChatGPT：设置 → 数据控制 → 导出数据
eia import chatgpt ~/Downloads/xxxx.zip --dry-run
eia import chatgpt ~/Downloads/xxxx.zip

# Claude：设置 → 隐私 → 导出数据；邮件里有好几个链接，只需要 conversations-000.zip
eia import claude ~/Downloads/conversations-000.zip
eia import claude ~/Downloads/conversations-000.zip --include-thinking   # 连思考过程一起导入（折叠显示）

# Gemini：没有官方导出，用浏览器插件导出的 gemini_chats_YYYY-MM-DD.zip（要带时间的新版）
eia import gemini ~/Downloads/gemini_chats_2026-11-25.zip
```

Gemini 的插件导出可能某次没加载完：如果这次导出的消息比 vault 里已有的少，那个对话会被跳过、不覆盖；
插件报告有对话没导出成功时，结束时会提示，补导出后再导入一次即可。

重复导入是安全的：只更新有新消息的对话，你加的标签、自己加的字段、写在 `eia:conversation:end` 标记下面的笔记都会保留。
想保持最新，就隔一段时间重新导出、重新导入一次。

### 每天自动同步 ChatGPT、Claude、Gemini（浏览器插件）

不想每次都去导出？装上仓库里的 `extension/` 插件，它每天把 ChatGPT、Claude、Gemini 上**更新过的**对话发给你电脑上的 eia。在网页、电脑 App、手机 App 上聊的都算（它们和网页是同一个账号）。消息时间和官方导出一样准。登录了多个 Google 账号时，先打开要同步的那个账号的 Gemini 页面，插件会记住。

1. 让 eia 在后台接收（登录 Windows 时自动启动，不弹窗口）：

   ```powershell
   eia sync autostart --vault <你的vault>     # 不想自动启动就改用前台运行：eia sync serve --vault <你的vault>
   eia sync token --vault <你的vault>         # 显示口令，下一步要填
   ```

2. 装插件：Chrome 或 Edge 打开 `chrome://extensions`，打开右上角「开发者模式」，点「加载已解压的扩展程序」，选仓库里的 `extension` 文件夹。
3. 点浏览器工具栏上的插件图标 → 「设置」，填口令（端口默认 27771，不用改）→ 保存 → 「现在同步」。第一次会把 vault 里没有的、更新过的对话都取一遍；你之前用官方导出包导入过的，没变就不再取。

之后每天第一次打开浏览器 10 分钟后自动同步一次，没成功的每小时再试。`eia sync status` 能看最近同步了哪些。

要知道的：

- 插件在你**已经登录**的 ChatGPT、Claude、Gemini 网页里请求网站自己的接口（和网页刷新时取的数据一样），登录状态不存、不外传；内容只发到 `127.0.0.1`。这些接口没有公开，网站改版可能失效，失效时不会写坏文件，插件图标会显示红色的「!」。
- 请求之间隔 2 秒、每天一次；平台条款不鼓励自动取数据，风险不是零（见 `docs/adr/0010-chat-sync-site-api.md`）。
- 和官方导出包不重复：同一个对话是同一个文件，你的标签和笔记照样保留。
- `eia db rebuild` 之后口令会换，要在插件里重新填。不再需要时：`eia sync autostart --remove`，再在 `chrome://extensions` 里删掉插件。

读回对话（只读）：

```bash
eia chats stats                        # 每个平台多少对话、你说了多少话
eia chats dump --only-me --since 2026-01-01   # 只看你自己说的话
eia chats dump --own-only     # 再去掉你贴进去的 AI 回答、代码、报错、附件
```

## 3. 接入 AI 服务（可选）

不配置就**不会调用任何 AI**。兴趣简报要用到：一个文本模型（给兴趣起名，花很少的钱），一个向量模型（推荐本机 Ollama，不花钱、数据不出电脑）。

1. 打开 vault 里的 `config.yaml`，把 `llm:` 那一段前面的 `#` 去掉，按注释改成你用的服务（DeepSeek、Claude、其他 OpenAI 兼容服务都可以）。
2. key **不要**写进 `config.yaml`，写进 vault 里的 `.env` 文件（已被 `.gitignore` 排除），例如：`DEEPSEEK_API_KEY=sk-...`
3. 向量模型：安装 [Ollama](https://ollama.com) 后运行 `ollama pull bge-m3`。
4. 检查：

```bash
eia llm show    # 看解析出来的配置；key 只显示有没有设置
eia llm test    # 发一个极短的请求，看通不通（会产生一次很小的费用）
```

配置格式详见 `docs/specs/llm-adapter.delta-v1.md` 第 2 节。

## 4. 新闻雷达

在 `config.yaml` 里打开 `headlines:` 那一段（模板里已经有一组实测可用的源）。

```bash
eia headlines catalog         # 自带的推荐源（科技、AI、中文热榜……），标出已在用的
eia headlines check           # 逐个源测试能不能访问
eia headlines run --dry-run   # 预览
eia headlines run             # 写进 inbox/headlines/
```

默认按「意思相近」归组，需要上一步的向量模型；不想装 Ollama，可以把 `cluster` 改成 `lexical`（按词语重合归组，效果差一些）。

## 5. 兴趣简报

```bash
eia interests draft           # 第 1 步：在本机把你说过的话归成几组，告诉你接下来要发什么给 AI
eia interests draft --yes     # 第 2 步：把每组的几条摘录发给文本模型起名，写到 interests/draft.md
# 在 Obsidian 里打开 interests/draft.md，勾上你认可的兴趣，可以改名、加关键词
eia interests accept          # 写进 config.yaml（旧配置会备份）
eia digest run                # 按兴趣给最近的报道打分，写进 inbox/digest/（本机计算，不花钱）
eia digest rate <报道链接> --irrelevant --why "只关心芯片，不关心手机"   # 评价
eia interests learn --yes     # 根据你的评价，让 AI 提修改建议（写进草稿，你确认后才生效）
```

## 6. 个人画像

```bash
eia profile run               # 在本机整理最近 3 个月你说过的话，告诉你要发多少字、发给谁（什么都不发）
eia profile run --yes         # 发给文本模型，生成 profile/<日期> 画像.md（每次一个新文件，不覆盖旧的）
eia profile run --since 2025-01-01 --yes   # 换时间范围；分析过、没变的对话不会重复发
```

画像按章节写：核心档案、心理、学业与职业、兴趣、决策记录、时间线，最后是一段可以直接贴给任何 AI 的「系统提示词」。每个小节有一个标签概括，每条要点后面有一个小数字，对应文末的证据（你的原话和对话链接）；AI 给的原话程序会去你的对话里核对，对不上的丢掉。画像会写进学号、住址这类个人信息，分享之前先看一遍。
觉得哪条不像你，在 Obsidian 里把那条的 `[ ]` 改成 `[x]`，后面可以写原因，下次生成时会避开。
如果那条当时没错、只是现在变了（比如「在找室友」，后来找到了），原因写「过时：」开头，例如 `过时：11 月已经找到了`。下次生成时它会挪进时间线，你写的当作现在的情况。

**补上聊天里没有的**：画像只看你跟 AI 的聊天，你的经历、信仰是怎么开始的、为什么做某个选择，往往是空的。

```bash
eia profile ask --yes         # 根据最新画像里空着、不确定的地方出几个问题，写到 profile/访谈 <日期>.md
```

在 Obsidian 里打开访谈文件，把回答写在「你的回答：」后面，不想答的空着。已经变了的事、想让画像知道的事，写进 `profile/我的补充.md`（第一次生成画像时自动建好），每段最好以日期开头。下次生成画像时，这些都会当作你最新、最可信的说法。出题时只发画像的结论，不发你的对话。

### 用哪个模型

画像支持 `config.yaml` 里能配置的任何文本模型：DeepSeek、Claude、其他 OpenAI 兼容服务，也可以是本机 Ollama 上的模型。换模型只改 `llm:` 那一段，不用改别的。

**不管用哪个模型，下面这些都一样**，因为是程序做的，不是模型做的：

- 发什么、不发什么（只发你写的话、笔记、贴回的 AI 回答；不发代码、报错、附件）；发之前先告诉你字数。
- 画像的骨架：章节、小节、标签的位置、证据脚注、「不像我」的勾选。
- 原话核对：模型给的原话在你的对话里查不到，就整条丢掉。
- 每条结论来自几个对话、几天，是程序按证据数的。
- 某一批回复格式不对时跳过、下次重试；分析过、没变的对话不重复发。

所以换了模型，打开画像**第一眼看起来差不多**。**差别在骨架里填的内容**：标签是不是只像你、要点是具体的事还是套话、事实漏没漏。

**实测对比**（2026-11-26，同一个 vault 最近 3 个月的 272 个对话，同样的流程）：

| | DeepSeek（`deepseek-v4-flash`，联网） | Qwen3（`qwen3:8b`，本机 Ollama，8GB 显存的笔记本） |
|---|---|---|
| 用时 | 几分钟 | 约 113 分钟（第 2 步每批 1.5–4 分钟） |
| 读出的观察 | 393 条 | 252 条 |
| 原话对不上、被程序丢掉的 | 0 条 | 22 条 |
| 回复格式不对、被跳过的对话 | 0 个 | 1 个 |
| 画像要点 | 104 条 | 70 条 |
| 标签 | 具体到只像这个人（某个习惯、某种处境） | 多是谁都适用的词，如「理性思考」「注重责任」「细节导向」 |
| 要点 | 写具体的事和本人的原话 | 多是「你在追求解决实际问题的方案」这类概括，像在列聊过的话题 |
| 事实 | 年级、课程、信仰、生活状况都写到了 | 漏了不少；「教育」一节是空的；决策记录、时间线各只有一条 |
| 数据 | 你写的话会发给 DeepSeek | 不出电脑 |

**花了多少钱**（2026-11-27，同一个 vault 的全部对话，`deepseek-v4-flash`）：

| | 数量 |
|---|---|
| 对话 | 1,417 个（2024-12 ~ 2026-11），其中 271 个之前跑最近 3 个月时已分析过，这次没再发 |
| 第 2 步发出去的 | 1,146 个对话、约 203 万字，分 249 批 |
| 读出的观察 | 2,853 条，原话对不上的 0 条 |
| 第 3 步 | 4 次汇总请求；观察太多，按月均匀挑了约 940 条 |
| 费用 | **约 3.78 元人民币**，在 DeepSeek 的高峰时段跑的 |

- 分析过的对话有缓存，以后再生成画像只发新增和改过的对话，加上 4 次汇总，费用会少得多。
- DeepSeek 如果有错峰优惠，避开高峰时段跑会更便宜，以官网的价格说明为准。
- 跑全部对话时，汇总的回复比较长，要在 `config.yaml` 的 `llm:` 里把 `max_tokens` 调大（例如 `16384`），`timeout_s` 调长（例如 `300`）；否则会报「回复超过了 4096 token 被截断」。第 2 步的结果已经存下，调大后重跑只发汇总请求。
- 跑之前看一下账户余额，余额不足时会报 HTTP 402，同样重跑就能接着来。

建议：

- **正式的画像用联网的大模型**（DeepSeek 就够，便宜）。画像要的是「读懂一个人」，8B 级别的本机模型做不好。
- **本机模型适合不需要深度理解的活**：向量模型（bge-m3，新闻雷达归组、兴趣打分）本来就在本机跑，效果很好。
- 很在意数据不出电脑、显存又够（24GB 以上，能跑 30B 级别的模型），可以自己再比一次。用本机模型时：
  - 关掉思考模式：`llm.extra_body` 里写 `reasoning_effort: none`。
  - 把上下文调大：建一个 `num_ctx 32768` 的模型。
  - 把 `timeout_s` 调长：本机模型慢，超时会让这次运行停下。

## 7. 网页剪藏

1. 浏览器安装 [Obsidian Web Clipper](https://obsidian.md/clipper)。
2. 运行 `eia clip template`，会在 vault 里生成 `eia-clipper.json`。
3. 在插件设置里导入这个文件（导入模板），并选好你的 vault。

以后剪下的网页会落进 `inbox/clips/`，带 `clip` 标签，直接通过 `eia validate`。
模板按 vault 的时区生成：改了时区要重新生成、重新导入。

## 8. 搜索

```bash
eia search 量子计算
eia search 量子计算 --type chat     # 只搜对话
```

搜索用的索引在 `.eia/eia.db`，可以随时删掉，`eia db rebuild` 会从 Markdown 重建（你的评价也会恢复）。

## 9. 每天自动运行

**Windows**：登记到任务计划程序，后台运行，不弹窗；错过的时间开机后补跑。

```powershell
eia schedule install headlines --at 08:00   # 可以写多次 --at
eia schedule status                          # 是否登记、下次运行时间、最近几次结果
eia schedule remove headlines
```

**macOS / Linux**：`eia schedule` 暂时只支持 Windows。可以自己用 cron 调用同一个命令（`crontab -e` 加一行，路径换成你的）：

```
0 8 * * * /path/to/everything-in-ai/.venv/bin/eia run headlines --vault /path/to/my-vault
```

`headlines` 任务跑完新闻雷达后，如果已经有兴趣清单，会接着跑兴趣简报。

## 隐私：什么数据会离开你的电脑

| 情况 | 发出去的内容 | 发给谁 |
|---|---|---|
| 导入、校验、新建、搜索、读回对话、剪藏模板 | 什么都不发 | — |
| 新闻雷达 | 只是去下载新闻源（不发你的数据）；开了 `interest_search` 时，兴趣关键词会作为搜索词发给 Google News | 新闻网站、Google |
| 向量模型（雷达归组、兴趣草稿第 1 步、简报打分） | 新闻标题；兴趣草稿会发**你说过的话** | 你配置的向量服务。兴趣草稿要求它在本机（如 Ollama），否则拒绝运行 |
| `interests draft --yes`、`interests learn --yes` | 每组兴趣的几条摘录 / 兴趣清单和你的评价 | 你配置的文本模型。不加 `--yes` 只告诉你会发什么，不发 |
| `profile run --yes` | 范围内**你写的话**、笔记、你贴回的 AI 回答（每段最多 500 字），以及对话标题和日期；你的访谈回答和「我的补充」；不发代码、报错、附件和 AI 的回答 | 你配置的文本模型。不加 `--yes` 只告诉你会发多少，不发 |
| `profile ask --yes` | 最新一份画像的结论（不含原话） | 你配置的文本模型。不加 `--yes` 只告诉你会发多少，不发 |
| `eia llm test` | 一个极短的测试请求 | 你配置的文本模型 |
| 对话同步插件 | ChatGPT、Claude、Gemini 网页接口返回的对话 | 只发到你电脑上的 eia（`127.0.0.1`），不出电脑 |

另外：

- key 只从环境变量或 vault 的 `.env` 读取，不会写进日志或命令输出。
- 工具不会替你执行 git push，不会删除你的文件。
- 如果你把这个代码仓库拉到本地改，`exports/` 文件夹可以用来放各平台的原始导出，它已在 `.gitignore` 里，不会被提交。

## 改代码

```bash
pip install -e ".[dev]"
python scripts/check.py     # ruff、mypy、pytest、验收报告；最后一行是 ALL PASSED 就是全部通过
```

每条验收标准的结果在 `reports/acceptance.md`。给 AI 编码助手的规则见 `AGENTS.md`。
