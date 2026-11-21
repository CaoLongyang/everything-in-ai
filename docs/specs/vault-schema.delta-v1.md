# Delta Spec: vault-schema V1

Status: 已批准 2026-11-20（随 docs/specs/chat-import.delta-v1.md 第 7 节第 2 条：A）　版本：V1
改动对象：`docs/specs/vault-schema.md`（已批准，本文件不改它）

## 改动

`chat` 条目增加一个**选填**字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `thinking` | bool | 这个文件的正文里有没有导入模型的思考过程。由 `eia import` 写入，用户一般不用改 |

- 只有支持 `--include-thinking` 的平台（目前是 Claude）才写这个字段；ChatGPT 导入的文件不写，已有文件不受影响。
- 字段缺失视为 `false`。
- 重新导入时，开关和这个字段不同就重写两条标记之间的对话内容，即使对话没有新消息。
- `schema` 版本号不变（仍为 `1`）：只加选填字段，旧文件全部仍然合规。
