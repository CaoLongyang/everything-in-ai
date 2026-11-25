# V0 任务清单

**V0 已于 2026-11-20 完成。** Claude 导入挪到 V1。

状态：✅ 完成　🔄 进行中　⏳ 等待　⬜ 未开始

| # | 任务 | 验收标准 | 状态 |
|---|---|---|---|
| T1 | 检查脚本 `scripts/check.py`、`check.ps1`，验收报告生成器 | 运行后生成 `reports/acceptance.md`，按编号列出结果 | ✅ |
| T2 | 数据模型、frontmatter 读写、id 和文件名规则 | F7-01 至 F7-04 | ✅ |
| T3 | `eia init` | F1-01 至 F1-03 | ✅ |
| T4 | `eia validate` | F2-01 至 F2-11 | ✅ |
| T5 | `eia new` | F3-01 至 F3-05 | ✅ |
| T6 | 写 `docs/specs/chat-import.md` | 作者 批准 | ✅ ChatGPT 部分 2026-11-18 批准 |
| T7 | 导入 Claude 对话 | — | ➡️ 挪到 V1 |
| T8 | 导入 ChatGPT 对话 | F4-01 至 F4-10 | ✅ |
| T9 | 重复导入，保留手动标签 | F6-01 至 F6-06 | ✅ |
| T10 | Windows 实机跑 `check.py`；手动验收 | `ALL PASSED`；M-01 至 M-04 | ✅ 2026-11-18 |
| T11 | 文档与实现对齐；补错误分支测试 | 文档无矛盾；覆盖率 ≥ 95% | ✅ 2026-11-20（覆盖率 98%） |

# V1 任务清单

**V1 已于 2026-11-25 完成。**

| # | 任务 | 验收标准 | 状态 |
|---|---|---|---|
| V1-1 | 导入 Claude 对话（含 `--include-thinking`） | F5-01 至 F5-09；真实导出试导 | ✅ 2026-11-20 |
| V1-2 | 导入 Gemini 对话 | F8-01 至 F8-08 | ✅ 2026-11-25（新版插件导出带真实时间；真实导出试导：439 个对话 0 跳过、validate 0 错误、读回逐条一致、重复导入全部未变） |
| V1-3 | GitHub 仓库：LICENSE、三系统 CI | CI 绿灯 | ✅ 2026-11-20（8 项全部通过；修了一个非中文 Windows 控制台崩溃的 bug） |
| V1-4 | `llm-adapter` | F9-01 至 F9-07 | ✅ 2026-11-21（方案 A：两家官方 SDK；实现决定见 ADR 0003；2026-11-21 DeepSeek 实测通过） |
| V1-5 | `chat-reader`：`eia chats dump` / `stats` | F10-01 至 F10-07 | ✅ 2026-11-21（笔记算「我」的话；粘贴内容的区分挪到 V2） |
| V1-6 | `headline-radar`：`eia headlines run` / `check` | F11-01 至 F11-08 | ✅ 2026-11-21（默认 embed + 本地 bge-m3，相似度 0.68 用真实数据校准；实现决定见 ADR 0004） |
| V1-7 | 定时运行：`eia run` / `eia schedule` | F12-01 至 F12-06 | ✅ 2026-11-21（先只做 Windows；作者电脑上已真实登记，每天 8:00；实现决定见 ADR 0005） |
| V1-8 | 兴趣草稿 + 按兴趣过滤 + 评价（取代 feed-digest / config-sync） | F13-01 至 F13-09 | ✅ 代码完成 2026-11-21；`min_score` 校准挪到 V2（2026-11-24 作者决定：草稿多为作业和琐事，等 `profile` 和兴趣分层产出更好的兴趣再校准；数据见 ADR 0006 第 9 条） |
| V1-9 | SQLite 数据库 + 全文搜索（`eia db` / `eia search`） | F14-01 至 F14-08 | ✅ 2026-11-21（作者两个 vault 已迁移；实现决定见 ADR 0007） |
| V1-10 | 新闻源扩展：Google News 搜索（含按兴趣自动搜）、Hacker News、中文热榜、推荐源目录 | F15-01 至 F15-09 | ✅ 2026-11-21（Reddit 挪到 Backlog；实现决定见 ADR 0008） |
| V1-11 | `web-clip`：Obsidian Web Clipper 模板，剪藏落进 `inbox/clips/` 并通过 validate | F16-01 至 F16-05；M-W1 | ✅ 2026-11-25（方案 A，默认 `clip` 标签；M-W1 实测 3 篇：B 站视频、英文 Reddit 帖子、英文维基百科，时间一致、validate 0 错误；「没有作者的页面」没测到，维基百科也带了作者） |
| V1-12 | 面向其他用户的安装和使用文档（ROADMAP「可分发」后半） | README 覆盖三系统安装、V1 全部命令、数据外发说明 | ✅ 2026-11-25（macOS / Linux 定时用 cron 手动配置，`eia schedule` 仍只支持 Windows） |

## 已确认

- 2026-11-20：GitHub 仓库 https://github.com/CaoLongyang/everything-in-ai ，公开，MIT（版权人 CaoLongyang）。AI 助手可直接提交和推送，每次推送前告诉作者。

## 验证记录

- 2026-11-25 Windows：`check.py` ALL PASSED（446 个测试，117 条验收通过，覆盖率 98%）。Gemini 真实导出导入 test-vault：439 个对话 0 跳过，读回与导出逐条一致，重复导入全部「未变」。
- 2026-11-25 手动验收 M-W1（作者）：剪 3 篇均落在 `inbox/clips/`，`created` 与实际时间一致。作者在 Obsidian 里把 `type` 改成 `clip-test` 导致 3 个错误，改回 `clip` 后 0 错误（`type` 只能是固定的 6 种，想做标记用 `tags`）。

- 2026-11-21 Windows：`check.py` ALL PASSED（244 个测试，57 条验收通过，覆盖率 98%，`eia/llm/` 全部 100%）。全部是模拟返回，没有真的调用 AI。
- 2026-11-21 真实调用（DeepSeek，`openai_compatible`，`deepseek-v4-flash`）：`eia llm test` 成功（约 3 秒）；`pytest -m live` 文本与 JSON 输出通过，向量未配置跳过。Claude 后端和向量尚未实测。
- 2026-11-21 Windows：`check.py` ALL PASSED（261 个测试，64 条验收通过，覆盖率 98%）。真实 vault 只读试跑 `eia chats stats` / `dump`：974 个对话，0 个跳过，「我」的消息 4,895 条 + 1 段笔记；运行前后所有文件哈希不变。
- 2026-11-21 新闻雷达真实运行（测试 vault，13 个推荐源，本地 Ollama bge-m3）：13/13 源可用，窗口内 276 篇 → 10 条大事，人工看过每组都是同一件事；`eia validate` 0 错误；第二次运行全部「未变」，4.7 秒。

- 2026-11-18 Windows 实机：`check.py` ALL PASSED（39 条验收通过）；真实导出导入近千个对话，跳过 1 个空对话；重复导入全部「未变」。
- 2026-11-18 手动验收（ChatGPT，作者）：M-01 Obsidian 正常显示 ✅；M-02 全局搜索可用 ✅；M-03 标签重新导入后仍在 ✅；M-04 数量与平台大致一致 ✅。
- 2026-11-18 手动验收发现 bug：Obsidian 清空标签后写成 `tags:`（空值），被误报为错误。已加测试 F2-12 并修复；Windows 重跑 ALL PASSED（120 个测试，40 条验收通过，覆盖率 95%），test-vault 全部文件 0 错误。

- 2026-11-18 用真实 ChatGPT 导出做只读解析（不写文件、不输出内容）：近千个对话、约一万条消息全部解析成功；1 个对话没有可导入的消息，会被跳过；图片占位和语音都正确识别；没有需要转义的行。

- 2026-11-16 Windows（Python 3.13.1）实机运行 `check.py`：ruff、mypy 零错误，92 个测试全部通过，覆盖率 97%。
