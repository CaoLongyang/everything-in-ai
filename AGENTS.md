# 给 AI 编码助手的说明

写任何代码之前先读这四份文件：

1. `docs/CONSTRAINTS.md`：质量底线和产品边界。不许为了让改动通过而放宽它。
2. `docs/ROADMAP.md`：每个版本做什么、明确不做什么。版本进行中不扩范围。
3. `docs/TEST-PLAN.md`：功能编号和验收标准。新功能先在这里登记编号，再写测试，再写代码。
4. 对应模块的规格 `docs/specs/<模块>.md`（及同名的 `.delta-*.md` 变更），以及 `docs/adr/` 里的决策记录。

所有文档的一览见 `docs/README.md`。

规则：

- 测试函数名带验收编号，如 `test_F2_04_...`。没登记的编号会让检查失败。
- 交付前运行 `python scripts/check.py`，把实际输出贴出来。
- 所有文件 IO 走 `src/eia/schema/io.py`。修改已有文件只用 `update_frontmatter` + `atomic_write`。
- 不删除用户文件，不调用任何 LLM（V0）。
- git commit / push：作者 2026-11-20 授权 AI 助手直接提交并推送本仓库，但**每次推送都要先告诉作者推什么**；推送前用 `git status` 确认 `exports/`、`.workbuddy-ai/`、`.venv/` 没有被加入。（`docs/CONSTRAINTS.md` 的"不执行 git push"指 eia 工具本身，不变。）
- `exports/` 里是真实的私人对话，不要读进测试样例，不要提交。
