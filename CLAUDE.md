# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

先读 `AGENTS.md`（必读文件清单和硬性规则），本文件只补充命令和架构。

## 项目是什么

`eia`（everything-in-ai）：把 AI 对话导入成纯 Markdown 的个人知识库（vault），用 Obsidian 浏览。
V0 已完成（init / validate / new / import chatgpt）；V1 已于 2026-11-25 完成，包括 `import claude`、`import gemini`、`llm`（AI 服务接入）、`chats`（按说话方读回对话）、`headlines`（新闻雷达）、`interests` / `digest`（兴趣简报）、`run` / `schedule`（定时运行）、`db` / `search`（数据库和全文搜索）、`clip`（剪藏模板）；下一步是 V2，进度见 `tasks/todo.md`，范围见 `docs/ROADMAP.md`。
所有文档、命令输出、报错信息都是中文，新代码保持一致。

## 命令

必须用项目自带的 `.venv`（系统 Python 缺 click / PyYAML，测试收集阶段就会失败）：

```powershell
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"           # 首次
python scripts\check.py           # 全套检查，最后一行必须是 ALL PASSED（交付前必跑并贴输出）
```

`check.py` 依次运行：`ruff check .`、`ruff format --check .`、`mypy`（strict，只查 `src/`）、
`pytest -q --junitxml reports/junit.xml --cov=eia`，最后 `scripts/acceptance_report.py` 生成 `reports/acceptance.md`。

单独运行：

```powershell
pytest tests/chat/test_chatgpt_import.py -q
pytest -k F6_03 -q                 # 按验收编号跑
ruff format .                      # 自动格式化
```

## 测试编号机制（容易踩坑）

`acceptance_report.py` 解析 `docs/TEST-PLAN.md` 里形如 `| F4-02 | ... |` 的表格行，再用正则 `test_F(\d+)_(\d+)_`
匹配 junit 里的测试名。后果：

- 测试名里出现 `docs/TEST-PLAN.md` 没登记的编号 → 检查失败（"没有登记的测试编号"）。新功能先在 docs/TEST-PLAN.md 加行。
- 任一已登记编号的测试失败或被 skip → 检查失败。
- 测试数据用合成样例（如 `tests/chat/conftest.py` 构造的 ChatGPT zip），绝不使用 `exports/` 里的真实对话。

## 架构

四个包，`src/eia/schema/`（数据契约）、`src/eia/chat/`（导入）、`src/eia/llm/`（AI 服务接入）和 `src/eia/headlines/`（新闻雷达），由 `src/eia/cli.py`（click）串起来。

**schema/**
- `model.py`：pydantic 模型。`Entry` 基类 + `ChatEntry` / `HeadlineEntry` / `DigestEntry`；`model_for(type)` 按 `type` 字段选模型。未知字段只警告。
- `io.py`：唯一的文件 IO 入口。关键设计（ADR 0001）：修改已有文件**不重新序列化 YAML**，`update_frontmatter` 只替换被改字段所在的行、新字段追加到末尾，写后重新解析校验；`atomic_write` 保证写失败不损坏原文件；`create_new` 用于新建（目标存在则 `FileExistsError`）。
- `vault.py`：目录布局（`TYPE_DIRS`，chat 进 `chats/<platform>/`）、`config.yaml`（时区）、`init_vault`。
- `ids.py` / `naming.py`：id 和文件名。chat 的 id 由 平台 + conversation_id 哈希决定（12 位后缀），其他条目随机 6 位；文件名日期按配置时区。
- `validate.py`：`validate_vault` 只读，检查 frontmatter、重复 id、重复对话、`.gitignore`。

**chat/**
- `chatgpt.py`：读官方导出 zip（不解压，逐个分片读取），只取 `current_node` 路径上的消息；格式不认识抛 `ExportFormatError`，不产生半成品。
- `claude.py`：读 Claude 导出的 `conversations-NNN.zip`（可多个）。只取 `content[]` 里的 `text` 块（顶层 `text` 字段带英文占位，不用）；工具调用 / 结果不导入；文件和附件只留占位；沿 `parent_message_uuid` 走到最新叶子；`updated` 取对话与所有消息里最晚的时间。规格见 `docs/specs/chat-import.delta-v1.md`。
- `model.py`：平台无关的 `Conversation` / `Message`。新平台应写一个产出这两个类型的解析器，复用 importer。`Message.thinking` / `Conversation.thinking` 支撑 `--include-thinking`：`thinking=None` 的平台不写 frontmatter 的 `thinking` 字段；开关与文件里记录的不同，即使没有新消息也会重写正文。
- `gemini.py`（`docs/specs/chat-import.delta-gemini.md`）：读浏览器插件导出的 zip，根目录每个对话一个 JSON + 一个汇总报告。只认带 `time` 的新版；旧版（`blocks[]`、没有时间）单独报错。同一轮的我和 Gemini 共用一个时间。`Conversation.url` 写进 frontmatter（只补缺的）。CLI 用 `refuse_fewer=True`：消息比文件里少时跳过，防插件没加载完冲掉内容；其他平台不开（ChatGPT 换分支时消息变少是正常的）。
- `render.py`：正文契约（docs/specs/chat-import.md 第 5 节）。程序只拥有 `<!-- eia:conversation:start ... -->` 与 `<!-- eia:conversation:end -->` 之间的内容；消息标题为 `### 我 · 时间` / `### ChatGPT · 时间`；正文里像标题或标记的行会被转义。
- `reader.py`（`docs/specs/chat-reader.md`）：`render.py` 的反向，只读。`iter_chats` / `read_chat` 把 vault 文件读回 `ReadChat`（消息 + 标记之外的用户笔记），还原转义，时间到分钟、可能为空。`eia chats dump --only-me` 给后续分析模块用；改正文格式时两边一起改，F10-01 往返测试会兜住。
- `source.py`（`docs/specs/chat-reader.delta-paste.md`）：把「我」的消息切段、标来源（`own` / `ai` / `code` / `error` / `attachment`），只认有证据的规则，不判断态度、不删内容。`label_chat` 与 `chat.messages` 一一对应。分析模块判断「我认为什么」只能引用 `own`；兴趣草稿用 `fair_share` 让每条消息分量一样。
- `importer.py`：按 (platform, conversation_id) 在 vault 中索引已有文件（不依赖文件名，用户可改名）。新对话 → `create_new`；`updated` 更新了 → 只替换标记块 + 更新 frontmatter 的少数字段，保留用户的 tags、自加字段和标记下方的笔记；标记损坏、重复文件、YAML 坏掉 → 报"跳过"，不碰文件。

**llm/**（规格 `docs/specs/llm-adapter.md` + `docs/specs/llm-adapter.delta-v1.md`，实现决定见 ADR 0003）
- 其他模块只写 `from eia import llm`，用 `llm.complete` / `complete_json` / `embed` 或 `llm.get_client(vault)`。
- `config.py`：解析 `config.yaml` 的 `llm` 段，key 从环境变量或 vault 的 `.env` 读。`client.py`：重试、JSON 校验、只记长度的日志。
- `_anthropic.py` / `_openai.py` 是仅有的两个能 `import anthropic` / `import openai` 的文件（F9-06 测试强制），只负责发一次请求并翻译错误。
- 测试全部模拟；`pytest -m live` 才会真的调用（默认不跑，需设置 `EIA_LIVE_VAULT`）。

**headlines/**（`docs/specs/headline-radar.md`，实现决定见 ADR 0004）
- 源的类型（`docs/specs/headline-radar.delta-sources.md`）：rss、google_news、hackernews、hotlist（NewsNow / B站），都在 `config.py` 解析成 `Feed`（实际请求的地址 + 计数用的媒体名），`feeds.py` 按 `kind` 选解析函数；`run.all_feeds` 再加上按兴趣自动生成的 Google News 搜索。推荐源目录在 `catalog.yaml`，加新源先实测。
- 不冒充浏览器、不破解签名（所以没有 Reddit、小红书等，见 ADR 0008）。
- 流程在 `run.py`：`feeds.py` 抓取解析（全仓库唯一 `import feedparser` 的地方）→ `store.py` 暂存到 `.eia/headlines/`（报道 + 向量缓存，向量按模型名作废）→ `cluster.py` 归组（`embed` 用 `llm.embed` + numpy，`lexical` 按词重合）→ `writer.py` 达到 `min_sources` 家的组写成 `headline` 条目。
- numpy、feedparser 只在函数里导入（可选依赖 `[news]`），`import eia.cli` 不能拉进它们，有测试守着。
- 测试用 `tests/headlines/conftest.py` 的虚构 RSS 和按关键词给向量的假模型；网络通过替换 `urllib.request.urlopen` 或传入 `fetcher` 隔离。

**schedule/**（`docs/specs/scheduler.md`，实现决定见 ADR 0005）
- `runs.py`：`eia run <job>`，加锁（`.eia/locks/`）、写运行记录（`.eia/runs/<job>.jsonl`，最近 200 条）。新任务加到 `JOBS`。
- `windows.py`：用 XML 调 `schtasks` 登记到 `\eia\`，`pythonw -m eia run ...` 不弹窗。测试里 `schtasks` 用假的 runner，绝不真的登记。

**interests/**（`docs/specs/interest-digest.md`，实现决定见 ADR 0006；部分代码改写自 Horizon，见 `THIRD_PARTY_NOTICES.md`）
- `draft.py`：`interests draft` 两步。第 1 步按对话在本机归组（向量模型必须在本机），第 2 步要 `--yes` 才把摘录发给文本模型起名；`accept` 把草稿里勾上的写进 `config.yaml`。
- `digest.py`：`digest run` 本机打分（复用雷达的报道向量），按兴趣限量（`select.py`），写当天的 `inbox/digest/` 条目；评价（`feedback.py`）和 `exclude` 影响打分；`learn.py` 根据评价提出修改建议（也要 `--yes`）。
- 定时任务 `headlines` 跑完雷达后，有兴趣清单就接着跑 `digest`。
- 改 `config.yaml` 的某一段统一用 `schema/configedit.py`（保留注释、先备份）。

**db/**（`docs/specs/storage.md`，实现决定见 ADR 0007）
- 原则：Markdown 是内容的唯一来源；机器用的数据在 `.eia/eia.db`，随时可删掉用 `eia db rebuild` 重建。评价例外地同时导出到 `interests/feedback.jsonl`。
- 一律用 `with database(root) as conn:`；预览传 `readonly=True`（不写任何文件）。改表结构：`schema.py` 的 `SCHEMA_VERSION` + `MIGRATIONS`。
- `index.py`：`sync` 增量索引 Markdown（比较修改时间和大小）；`search` 全文搜索（3 字以上 FTS5 trigram，更短的 LIKE）。
- `legacy.py`：旧版本散文件的一次性导入（导入后移到 `.eia/old/`）。不要再新增 `.eia/` 下的散文件，加表。

**clip/**（`docs/specs/web-clip.md`）
- `template.py`：`eia clip template` 按 vault 时区生成 Obsidian Web Clipper 的模板 JSON。插件没有随机数、不能转 UTC，
  所以 id 后缀用 Unix 秒数换字母，UTC 时间靠 `date_modify`（只返回日期）+ 两轮字面替换拼出来；只支持整小时时差。
- 测试用 `tests/clip/clipper_sim.py` 按插件源码的规则渲染模板，不运行真插件；改模板写法要先看规格第 3.1 节的插件行为。

## 改规格的方式

已批准的 spec 不直接改，在旁边写 `docs/specs/<name>.delta-<主题>.md`；实现中做出的偏离 spec 的决定记到 `docs/adr/`。
版本内不扩范围，新想法进 `docs/ROADMAP.md` 的 Backlog。
