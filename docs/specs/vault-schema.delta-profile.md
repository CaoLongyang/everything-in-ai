# Delta Spec: vault-schema V2（profile 条目）

Status: 已批准 2026-11-26（随 docs/specs/profile.md）　版本：V2
改动对象：`docs/specs/vault-schema.md`（已批准，本文件不改它）

## 改动

1. 新增条目类型 `profile`（个人画像），目录 `profile/`，`eia init` 创建这个目录。`type` 可选值从 6 种变成 7 种。
2. id 格式不变：`profile-<UTC日期>-<6 位随机>`。
3. `profile` 条目的额外字段，都是选填：

| 字段 | 类型 | 说明 |
|---|---|---|
| `period` | 字符串 | 画像依据的对话时间范围，如 `2026-08-26 ~ 2026-11-26` |
| `chats` | 整数 | 依据了多少个对话 |
| `model` | 字符串 | 生成画像的模型名 |

- `schema` 版本号不变（仍为 `1`）：只多了一种类型，旧文件全部仍然合规。
- 已有 vault 没有 `profile/` 目录也合规；`eia profile run` 第一次写画像时创建。
