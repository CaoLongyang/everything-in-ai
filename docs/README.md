# 文档导览

只想知道项目做到哪了：看 `tasks/todo.md`。想知道以后做什么：看 `ROADMAP.md`。

## 根目录（只留最常用的）

| 文件 | 是什么 |
|---|---|
| `README.md` | 项目介绍、安装和用法 |
| `AGENTS.md` / `CLAUDE.md` | 给 AI 编码助手的规则和架构说明 |
| `LICENSE` / `THIRD_PARTY_NOTICES.md` | 许可证；借用的第三方代码（Horizon）的版权声明 |

## docs/：总体规划

| 文件 | 是什么 |
|---|---|
| `CONSTRAINTS.md` | 质量底线和产品边界（不许放宽） |
| `ROADMAP.md` | 每个版本做什么、不做什么；新想法记在这里的 Backlog |
| `CAPABILITY-MAP.md` | 所有模块一览、依赖关系、设计原则 |
| `TEST-PLAN.md` | 功能编号（F1、F2……）和验收标准；检查脚本按它出报告 |

## docs/specs/：每个模块的规格

`<模块>.md` 是批准过的规格；`<模块>.delta-*.md` 是之后的变更（批准过的规格不直接改）。

| 模块 | 文件 | 做什么 | 状态 |
|---|---|---|---|
| vault-schema | `vault-schema.md`、`vault-schema.delta-v1.md`、`vault-schema.delta-profile.md`（V2：画像条目） | 文件格式、目录布局、`init` / `validate` / `new` | ✅ |
| chat-import | `chat-import.md`、`chat-import.delta-v1.md`（Claude）、`chat-import.delta-gemini.md`（Gemini）、`chat-import.delta-title.md`（标题只留一行） | 导入 ChatGPT / Claude / Gemini 对话 | ✅ |
| llm-adapter | `llm-adapter.md`、`llm-adapter.delta-v1.md` | 接入 AI 服务（DeepSeek、Claude、本机 Ollama） | ✅ |
| chat-reader | `chat-reader.md`、`chat-reader.delta-paste.md`（V2：标出文字来源） | 把对话读回来给其他模块分析 | ✅ |
| headline-radar | `headline-radar.md`、`headline-radar.delta-sources.md`（新闻源扩展） | 新闻雷达：多家媒体都报道的大事 | ✅ |
| scheduler | `scheduler.md` | 每天定时自动运行 | ✅ |
| interest-digest | `interest-digest.md` | 从对话生成兴趣、按兴趣挑新闻、评价 | ✅；`min_score` 校准在 V2 |
| storage | `storage.md` | SQLite 数据库和全文搜索 | ✅ |
| web-clip | `web-clip.md` | 浏览器剪藏插件的模板，剪下的网页直接合规 | ✅ |
| profile | `profile.md`、`profile.delta-format.md`（细分章节）、`profile.delta-layout.md`（排版）、`profile.delta-skip.md`（坏批次跳过）、`profile.delta-v2b.md`（访谈和补充、时间感、想做的清单）、`profile.delta-sampling.md`（观察太多时按时间均匀挑） | 个人画像：AI 读你说过的话，写「数字化的你」 | 🔄 V2 |

## docs/adr/：实现中做的决定

每个模块写代码时偏离规格、或规格没写到的决定，按编号记在这里（0001–0009，与上表模块对应：
0001–0002 是 V0，0003 llm-adapter，0004 headline-radar，0005 scheduler，0006 interest-digest，0007 storage，0008 新闻源扩展，0009 profile）。

## 其他目录

| 目录 | 是什么 |
|---|---|
| `tasks/` | `todo.md` 进度表，`plan.md` 早期计划 |
| `src/eia/` | 代码 |
| `tests/` | 测试 |
| `scripts/` | `check.py` 全套检查、`playground.py` 本机试用网站 |
| `reports/` | 检查脚本生成的报告（自动生成，不用看） |
| `exports/` | 你的真实对话导出（私人数据，不提交到 GitHub） |
