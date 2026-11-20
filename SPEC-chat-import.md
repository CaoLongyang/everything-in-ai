# Spec: chat-import

Status: 已批准 2026-11-18（ChatGPT 部分）　版本：V0
依据：2026-11-18 对 作者 真实 ChatGPT 导出的结构检查（只读结构和统计，不读、不复制对话内容）。
Claude 和 Gemini 部分挪到 V1，见第 8 节。

## 1. Objective

把 ChatGPT 的官方导出 zip 转成 vault 里的 `chat` 条目：一个对话一个 Markdown 文件。
重复导入只更新、不重复，用户在 Obsidian 里加的标签、改的字段、写的笔记都保留。

## 2. 真实导出的结构（ChatGPT，2026-11）

| 观察 | 数值 | 对设计的影响 |
|---|---|---|
| zip 大小 | 约 52 MB，94 个文件 | 直接读 zip，不需要用户解压 |
| 对话文件 | `conversations-000.json` 到 `-009.json`，每个最多 100 个对话，单个最大约 5 MB | 逐个分片读取即可控制内存，**不需要 ijson** |
| 对话数 / 消息数 | 约 1,000 / 约 10,000 | 一次导入在秒级完成 |
| 时间范围 | 2024-12 到 2026-11 | — |
| 每个对话 | `mapping` 是一棵消息树，`current_node` 指向最后一条 | 从 `current_node` 沿父节点走回根，得到你实际看到的那一版 |
| 不在这条路径上的消息 | 116 条 | 是重新生成或编辑前的旧版本，**不导入** |
| 消息类型 | text 9,407，thoughts 311，reasoning_recap 292，multimodal_text 82 | 见第 4 节 |
| 消息里的图片 | 58 张（`image_asset_pointer`），zip 里有 72 个 `.dat` 附件 | V0 只放占位文字，不复制图片 |
| 语音转写 | 31 条（`audio_transcription`，带文字） | 导入转写文字 |
| 空白段落 | 13 个 | 跳过 |
| 其他文件 | `user.json`、`user_settings.json`、`ads.json`、`index.html` 等 | **不读**，里面是账号信息，与对话无关 |

旧版导出是单个 `conversations.json`，结构相同。两种都支持；单文件版本整个读入内存，超过 300 MB 时给出提示。

## 3. 命令

```
eia import chatgpt <导出的 zip> [--vault <路径>] [--dry-run]
eia import claude  <导出的 zip> [--vault <路径>] [--dry-run]
```

- `--dry-run`：只报告会新建、更新、跳过多少个，不写任何文件。
- 结束时输出：`新建 N，更新 N，未变 N，跳过 N（原因）`，退出码 0。
- 格式不认识时：报出是哪个文件、缺什么字段，退出码 1，**一个文件都不写**（先完整解析，再统一写入）。

## 4. 每条消息怎么处理

| 内容 | V0 处理 |
|---|---|
| 你和 ChatGPT 的文字 | 原样导入 |
| 语音转写 | 导入文字，前面标「（语音）」 |
| 图片 | 写一行占位：`[图片，宽×高]` |
| 思考过程（thoughts、reasoning_recap） | **V0 不导入**。这是模型的内部推理，篇幅大，对你回看用处不大。V1 加选项 `--include-thinking`，打开后以折叠块导入 |
| system / tool 消息、空白段落 | 不导入 |

## 5. 生成的文件

位置：`chats/chatgpt/YYYY-MM-DD-<标题>-<后缀>.md`

```markdown
---
schema: 1
id: chat-20250312-k3f9qakd2mxa
type: chat
title: 对话标题
created: 2025-03-12T08:01:22Z      # 对话创建时间
updated: 2025-03-14T10:40:05Z      # 对话最后更新时间
status: inbox
tags: []
source: chatgpt
platform: chatgpt
conversation_id: 67d0...           # 平台的对话 ID
model: auto
message_count: 14
---

<!-- eia:conversation:start 这一段由 eia import 生成，重新导入时会被替换 -->

### 我 · 2025-03-12 16:01

……

### ChatGPT · 2025-03-12 16:01

……

<!-- eia:conversation:end -->

（这条线以下是你自己的笔记，重新导入不会动）
```

- **说话方是固定格式，供以后的程序区分**：每条消息以 `### 我 · 时间` 或 `### ChatGPT · 时间`（Claude 为 `### Claude · 时间`）开头。这个格式是契约，改动要先写 delta spec。V0 两边都存；以后做画像、兴趣分析时，程序可以只读「我」的部分（见 ROADMAP V1 `chat-reader`）。
- 正文里的时间按 `config.yaml` 的时区显示；frontmatter 里一律是 UTC。
- 消息里的 Markdown（代码块、表格、列表）原样保留。

## 6. 重复导入

1. 导入前扫描整个 vault，按 `platform + conversation_id` 找已有文件。**按 id 找，不按文件名找**，所以你改过文件名、挪过文件夹都能找到。
2. 找到了，且平台的 `update_time` 比文件里的 `updated` 新：
   - 只替换两条 `eia:conversation` 标记之间的内容；
   - frontmatter 只改 `title`、`updated`、`model`、`message_count`（用 `update_frontmatter`，其他行逐字节不动）；
   - `tags`、`status`、你加的任何字段、标记之外的笔记全部保留。
3. 找到了但没有变化：不写文件，计入「未变」。
4. 找到了但两条标记被删掉或损坏：**不修改这个文件**，计入「跳过」并报出路径。
5. 没找到：新建。

## 7. Boundaries

- Always：只读 zip，不解压到磁盘；所有写入走 `atomic_write` / `create_new`。
- Ask first：导入思考过程、复制图片、读取对话以外的文件。
- Never：删除或移动 vault 里的文件；把真实导出的任何内容放进测试样例或代码仓库；联网；调用 LLM。

## 8. Claude 与 Gemini（V1）

两者在 V1 另写 delta spec（`SPEC-DELTA-chat-import-v1.md`）。已知情况：

- **Claude**：官方对话导出（Settings → Privacy → Export data）2026-11-20 前未收到，还没有真实样例。
- **Gemini**：Takeout 三次导出都没有对话（很可能用错了 Google 账号）。2026-11-19 用浏览器插件导出了 182 个对话：每个对话一个 JSON，有说话方和格式块，但**没有任何时间信息**，有 328 处 Gemini 连续发言，另有 251 个对话导出超时。


## 9. 对 TEST-PLAN.md 的修改（批准后生效）

- F4-06 改为：分片导出逐个读取，导入 1,000 个对话的合成样例时内存不超过 300 MB。
- 新增 F4-07：只导入 `current_node` 路径上的消息，旧版本分支不导入。
- 新增 F4-08：思考过程、system 消息、空白段落不出现在正文里；语音转写和图片占位出现。
- 新增 F6-05：标记被删除的文件不被修改，在结果里报为「跳过」。
- 新增 F6-06：你在标记之外写的笔记在重新导入后逐字节不变。
- 新增 F4-09：`--dry-run` 不写任何文件。
- 新增 F4-10：每条消息的标题严格是 `### 我 · ` 或 `### ChatGPT · ` 开头，消息正文里出现同样的文字时不会被误认成新消息。

## 10. 已定决策（2026-11-18）

1. 对话两边都存，说话方用固定格式标记（方案 C）。只读「我」的部分的工具放到 V1。
2. 思考过程 V0 不导入；`--include-thinking` 放到 V1。

3. 正文分两块：两条 `eia:conversation` 标记之间归程序，重新导入时替换；标记之外归用户，程序不动。
4. 图片 V0 不做：只留一行占位 `[图片，宽×高]` 表示这里原本有图，不复制图片文件。
5. 两条标记按**整行**识别（读取程序不能用子串查找）。消息正文里如果有一行恰好以 `### 我 · `、`### ChatGPT · ` 或 `<!-- eia:` 开头，导入时在行首加 `\`，防止被误认成新消息或标记。Markdown 显示效果不变（代码块内会多显示一个 `\`）。
