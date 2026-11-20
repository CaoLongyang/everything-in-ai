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
| T6 | 写 `SPEC-chat-import.md` | 作者 批准 | ✅ ChatGPT 部分 2026-11-18 批准 |
| T7 | 导入 Claude 对话 | — | ➡️ 挪到 V1 |
| T8 | 导入 ChatGPT 对话 | F4-01 至 F4-10 | ✅ |
| T9 | 重复导入，保留手动标签 | F6-01 至 F6-06 | ✅ |
| T10 | Windows 实机跑 `check.py`；手动验收 | `ALL PASSED`；M-01 至 M-04 | ✅ 2026-11-18 |
| T11 | 文档与实现对齐；补错误分支测试 | 文档无矛盾；覆盖率 ≥ 95% | ✅ 2026-11-20（覆盖率 98%） |

# V1 任务清单

| # | 任务 | 验收标准 | 状态 |
|---|---|---|---|
| V1-1 | 导入 Claude 对话（含 `--include-thinking`） | F5-01 至 F5-09；真实导出试导 | ✅ 2026-11-20 |
| V1-2 | 导入 Gemini 对话 | 规格草案 `SPEC-DELTA-chat-import-gemini.md` | ⏳ 暂缓：等 Takeout |
| V1-3 | GitHub 仓库：LICENSE、三系统 CI | CI 绿灯 | ✅ 2026-11-20（8 项全部通过；修了一个非中文 Windows 控制台崩溃的 bug） |
| V1-4 | `llm-adapter` | 复核 SPEC-llm-adapter.md 后登记 F 编号 | ⬜ |

## 已确认

- 2026-11-20：GitHub 仓库 https://github.com/CaoLongyang/everything-in-ai ，公开，MIT（版权人 CaoLongyang）。AI 助手可直接提交和推送，每次推送前告诉作者。

## 验证记录

- 2026-11-18 Windows 实机：`check.py` ALL PASSED（39 条验收通过）；真实导出导入近千个对话，跳过 1 个空对话；重复导入全部「未变」。
- 2026-11-18 手动验收（ChatGPT，作者）：M-01 Obsidian 正常显示 ✅；M-02 全局搜索可用 ✅；M-03 标签重新导入后仍在 ✅；M-04 数量与平台大致一致 ✅。
- 2026-11-18 手动验收发现 bug：Obsidian 清空标签后写成 `tags:`（空值），被误报为错误。已加测试 F2-12 并修复；Windows 重跑 ALL PASSED（120 个测试，40 条验收通过，覆盖率 95%），test-vault 全部文件 0 错误。

- 2026-11-18 用真实 ChatGPT 导出做只读解析（不写文件、不输出内容）：近千个对话、约一万条消息全部解析成功；1 个对话没有可导入的消息，会被跳过；图片占位和语音都正确识别；没有需要转义的行。

- 2026-11-16 Windows（Python 3.13.1）实机运行 `check.py`：ruff、mypy 零错误，92 个测试全部通过，覆盖率 97%。
