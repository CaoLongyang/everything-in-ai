# everything-in-ai

把你和 AI 的对话、你关注的新闻和网页，沉淀成一个纯 Markdown 的个人知识库。
数据是一个普通文件夹（vault），用 [Obsidian](https://obsidian.md) 就能打开，不绑定任何软件。

能做的事：

- **导入 AI 对话**：ChatGPT、Claude 的官方导出 → 一个对话一个 Markdown 文件；重复导入只更新，不重复，你加的标签和笔记都保留。
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
```

重复导入是安全的：只更新有新消息的对话，你加的标签、自己加的字段、写在 `eia:conversation:end` 标记下面的笔记都会保留。
想保持最新，就隔一段时间重新导出、重新导入一次。

读回对话（只读）：

```bash
eia chats stats                        # 每个平台多少对话、你说了多少话
eia chats dump --only-me --since 2026-01-01   # 只看你自己说的话
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

## 6. 网页剪藏

1. 浏览器安装 [Obsidian Web Clipper](https://obsidian.md/clipper)。
2. 运行 `eia clip template`，会在 vault 里生成 `eia-clipper.json`。
3. 在插件设置里导入这个文件（导入模板），并选好你的 vault。

以后剪下的网页会落进 `inbox/clips/`，带 `clip` 标签，直接通过 `eia validate`。
模板按 vault 的时区生成：改了时区要重新生成、重新导入。

## 7. 搜索

```bash
eia search 量子计算
eia search 量子计算 --type chat     # 只搜对话
```

搜索用的索引在 `.eia/eia.db`，可以随时删掉，`eia db rebuild` 会从 Markdown 重建（你的评价也会恢复）。

## 8. 每天自动运行

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
| `eia llm test` | 一个极短的测试请求 | 你配置的文本模型 |

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
