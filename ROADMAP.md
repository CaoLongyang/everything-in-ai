# Roadmap（迭代计划）

原则：每个版本都是"能用的整体"。新想法一律先进 Backlog，版本进行中不扩范围；重新排期只在版本之间做。已批准的规格要改，写 delta spec（`SPEC-DELTA-<name>.md`），不直接改旧 spec。

## V0 — 雏形：我的 AI 对话变成一个能浏览的知识库

使用者：作者 本人，Windows。不调用任何 AI，不联网抓取，不做定时任务。

| 功能 | 范围 |
|---|---|
| 创建 vault | `eia init`：目录结构 + 配置模板 + `.gitignore` |
| 格式检查 | `eia validate`：指出不合规的文件和字段 |
| 新建条目 | `eia new`：生成带合法 frontmatter 的空文件 |
| 导入 ChatGPT 对话 | 官方导出 zip → Markdown，一个对话一个文件；流式解析大文件；格式未知时明确报错 |
| 重复导入 | 按确定性 id 更新同一文件，不产生重复；**保留用户手动加的 tags 和未知字段** |
| 标签 | 用户在 Obsidian 里手动打，写进 frontmatter 的 `tags` |
| 浏览与搜索 | 直接用 Obsidian 打开 vault（不做网站） |
| 保持最新 | 用户定期手动官方导出并重新导入；重复导入只更新不重复，等效增量同步 |

完成标准：在 作者 的 Windows 机器上，按 README 完成"初始化 → 导入 ChatGPT 对话 → 在 Obsidian 里浏览、搜索、打标签 → 再次导入后标签仍在"。

状态：✅ 2026-11-20 完成。Claude 导入因官方导出迟迟未到，挪到 V1。

## V1 — 信息输入：大事不漏，兴趣有过滤

| 功能 | 范围 |
|---|---|
| `llm-adapter` | 单模型配置、`complete` / `complete_json` / `embed`、`eia llm test`（见 SPEC-llm-adapter.md） |
| `headline-radar` | 抓取 → 聚类 → 独立来源计数 → 每日 `inbox/headlines/`；向量优先走 `llm-adapter.embed`，避免强制安装 torch |
| `feed-digest` | 先核实 Horizon 的输出格式与许可证；写转换适配器输出合规 `digest` 条目 |
| `config-sync` | `eia sync-config` 生成 Horizon 配置；key 只经环境变量注入 |
| `web-clip` | 剪藏插件模板对齐 schema |
| 调度 | `eia run <job>` + 本机定时（Windows 任务计划 / cron / launchd）；GitHub Actions 模板，runner 唯一负责 commit/push，同一 vault 只启用一种调度 |
| `chat-import` | 增加 Claude（官方导出，原 V0 项）和 Gemini（浏览器插件导出的 JSON，样例已有；Takeout 需换到第二个 Google 账号再试）；`--include-thinking` 选项，把模型思考过程以折叠块导入。**进度：Claude ✅ 2026-11-20；Gemini 暂缓（插件导出没有时间，等 Takeout）** |
| `chat-reader` | 按说话方读取对话：可以只取「我」说的话，供 `llm-adapter` 之后的分析、画像、兴趣模块使用，避免把 AI 的观点当成用户的想法 |
| `vault-site` | `eia site` 启动本机 Quartz，显式绑定 `127.0.0.1`，启动前检查 Node.js |
| 可分发 | macOS / Linux / Windows 三系统 CI；面向其他用户的安装文档 |

## V2 — 分析与闭环

| 功能 | 范围 |
|---|---|
| `knowledge-index` | 部署 Khoj 索引 vault；`config-sync` 扩展到 Khoj；定时研究简报 |
| `chat-sync` | 浏览器插件在聊天时实时把对话写成 vault 条目（ChatGPT / Claude / Gemini 网页版）；先评估现有开源插件能否复用；非官方方式，网站改版可能失效，独立模块、坏了不影响其他部分；不做模拟登录调用内部接口 |
| AI 自动打标签 / 摘要 | 需用户明确开启（会把对话内容发给所配置的 LLM） |
| 闭环 | 👍/👎 反馈；知识库内容反向调节兴趣过滤 |
| `takeout-import` | 通用 Google Takeout 导入（Gemini 之外：YouTube 观看/搜索/订阅、Chrome、搜索记录），按类别勾选；schema 新增 `activity` 类型（需单独 spec） |
| `activity-import` | TikTok 官方导出（JSON，选 All data）；B 站经浏览器插件导出的 JSON |
| `interest-bootstrap` | 从搜索、订阅、收藏、AI 对话等生成兴趣草稿（按信号强弱加权，观看记录权重最低），用户确认后才生效；需用户主动开启并能看到推断依据 |
| `profile` | 个人画像：AI 读取 vault 原文，生成"关于你"的结论，放在独立的 `profile/` 目录，与原始材料分开。从 3–4 个大类起步（参考结构：身份、价值观、认知、行为、关系、兴趣等），每条结论带来源条目 id（证据索引）；不确定的单列"未解问题"；画像按版本保留，可随时整份重新生成；AI 推断与用户本人的话分开标记；需用户明确开启（会把对话发给所配置的 LLM）。依赖 V0 对话导入与 V1 `llm-adapter` |
| 导入便利 | `eia import <folder>` 自动识别平台；监视 `exports\` 与 OneDrive 中的 Takeout 定时导出，新包自动导入 |

## 明确不做

| 项目 | 原因 |
|---|---|
| 微信聊天记录 | 无官方可读导出；第三方工具需破解本地数据库，腾讯 2026-01 已投诉下架 30+ 个此类开源项目；且包含他人隐私。重要内容由用户手动整理成笔记 |
| 抖音（凭登录凭据读取） | 无官方批量导出；现有工具需导入登录凭据，有封号与凭证泄露风险。待出现官方或更安全途径再评估 |
| 模拟登录调用平台内部接口 | 封号与凭证泄露风险，任何平台都不做 |

## Backlog（未排期）

- `llm-adapter`：多模型分组（profiles）、每日预算上限、结果缓存、多 provider 自动切换
- `vault-schema`：`validate --fix`、schema 迁移工具、长对话拆分
- Logseq 兼容
- 私密网站远程访问（带登录保护）
- 选择性公开发布 / 接入个人书卷网站
- 移动端推送（Telegram / 邮件）
- 自有 UI / 每日简报网页
- `chat-import`：Claude 导出中的 `projects`、`memories`、`artifact_data`（2026-11-20 定为以后再做）
