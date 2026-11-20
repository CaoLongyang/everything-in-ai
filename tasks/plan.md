# V0 实施计划

依据：ROADMAP.md（V0）、SPEC-vault-schema.md、TEST-PLAN.md。

## 依赖关系

```
T1 检查脚本 + 验收报告
 └─ T2 数据模型与读写（model / io / ids / naming）
     ├─ T3 eia init
     ├─ T4 eia validate
     ├─ T5 eia new
     └─ T6 SPEC-chat-import.md        ← 需要真实导出文件
         ├─ T7 导入 Claude 对话
         ├─ T8 导入 ChatGPT 对话
         └─ T9 重复导入（保留标签）
             └─ T10 Windows 实机验收 + 手动验收 M-01 至 M-04
```

T1 至 T5 不依赖导出文件，先做。T6 起要等 作者 的导出文件。

## 切分原则

每个任务做完都是能独立运行、能验证的：T3 做完就能 `eia init`，T4 做完就能检查任意 vault。
不先把所有底层写完再接命令。

## 风险

| 风险 | 应对 |
|---|---|
| 代码只在 Linux 上测过，Windows 文件名和编码行为可能不同 | T10 在 作者 的机器上跑 `check.py`；文件名规则已按 Windows 限制设计 |
| 导出格式没有官方文档，可能和预期不同 | T6 先看真实文件再写 spec；格式不认识时明确报错（F4-05） |
| ChatGPT 导出可能很大 | 流式解析；F4-06 用合成的大文件测内存 |
| mypy 在开发环境缺少 PyYAML 的类型包，无法确认零错误 | T10 在装了完整开发依赖的机器上确认 |
