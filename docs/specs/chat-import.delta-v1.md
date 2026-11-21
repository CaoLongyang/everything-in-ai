# Delta Spec: chat-import V1（Claude 部分）

Status: 已批准 2026-11-20（第 7 节三项均按推荐：1A、2A、3 放进 Backlog）　版本：V1
改动对象：`docs/specs/chat-import.md`（已批准，本文件不改它，只在它之上增加内容）
依据：2026-11-20 对作者真实 Claude 官方导出的结构检查（只读字段名、类型和计数，不读、不复制对话内容）。
Gemini 部分见 `docs/specs/chat-import.delta-gemini.md`。

## 1. 真实导出的结构（Claude，2026-11）

官方导出（Settings → Privacy → Export data）先发来一个 `manifest-*.json`，里面是 6 个一次性下载链接，24 小时内有效：

| 文件 | 内容 | V1 处理 |
|---|---|---|
| `conversations-000.zip` | 单个 `conversations.json`，对话列表 | **导入** |
| `projects-000.zip`、`memories-000.zip`、`artifact_data-000.zip` | 项目、记忆、artifact 数据 | **不读**，放进 Backlog |
| `light_metadata-000.zip` | `users.json`、`login_history.json` | **不读**，是账号信息 |
| `frames-000.zip` | 未下载，内容不明 | 不需要 |

`conversations.json` 的结构：

| 观察 | 数值（约） | 对设计的影响 |
|---|---|---|
| 大小 | 不到 4 MB，约 20 个对话、约 400 条消息 | 整个读入内存即可；多于一个 `conversations-NNN.zip` 时逐个读 |
| 每个对话 | `uuid`、`name`、`summary`、`created_at`、`updated_at`、`chat_messages` | `name` 可能为空字符串 → 「未命名对话」 |
| 时间 | ISO 8601，UTC，带微秒（`2026-06-01T08:00:00.123456Z`） | 截到秒 |
| 对话的 `updated_at` | 一半以上的对话**早于**最后一条消息的时间 | `updated` 取「对话 `updated_at`」与「所有消息 `updated_at`」中最晚的一个，否则重复导入会漏掉新消息 |
| 每条消息 | `uuid`、`parent_message_uuid`、`sender`（`human` / `assistant`）、`text`、`content[]`、`attachments[]`、`files[]`、`created_at`、`updated_at` | — |
| 消息顺序 | 列表顺序就是父子链，第一条的父节点是全零 UUID；样例里**没有分支** | 按父子链从根走到最新的叶子（见第 3 节） |
| 顶层 `text` 字段 | 等于各个 `text` 块用空行连接，但每个工具调用会被替换成一行英文占位 `This block is not supported…` | **不用 `text`**，从 `content[]` 里取 |
| `content[]` 块类型 | `text`、`thinking`（数百个）、`tool_use` / `tool_result`（各数百个）、`injected_prompt_block`、`document`、`image` | 见第 2 节 |
| 附件 `attachments[]` | 少数几个，粘贴的长文本，带 `file_name` 和 `extracted_content`（1–22 千字） | 见第 2 节 |
| 文件 `files[]` | 几十条消息带文件，只有 `file_name` 和 `file_uuid`，导出里没有文件本身 | 占位 |
| 模型名 | 导出里没有 | `model` 写 `null` |
| 正文里以 `### 我 · ` / `### Claude · ` / `<!-- eia:` 开头的行 | 有几处 | 已有的转义规则（原 spec 第 10 节第 5 条）同样适用 |

## 2. 每条消息怎么处理

| 内容 | 处理 |
|---|---|
| `text` 块（你和 Claude 的文字） | 原样导入，多个块之间空一行 |
| `thinking` 块 | 默认不导入；加 `--include-thinking` 时以折叠块导入（第 4 节） |
| `tool_use` / `tool_result`（运行命令、搜索、改文件等） | **不导入**，与 ChatGPT 的 tool 消息一致 |
| `injected_prompt_block` | 不导入，是系统自动插入的提示 |
| `files[]`（上传或生成的文件） | 每个文件一行占位：图片扩展名（jpg、png、gif、webp、svg 等）写 `[图片：<文件名>]`，其他写 `[文件：<文件名>]`。你的消息里放在正文前，Claude 的消息里放在正文后 |
| `image` / `document` 块 | 真实导出里它们都已出现在 `files[]` 中（按 `file_uuid` 对应），不重复占位；不在 `files[]` 里的，写 `[图片]` / `[文件：<标题>]` |
| `attachments[]`（粘贴的长文本） | 占位 `[附件：<文件名>，<字数> 字]`，放在正文前（第 7 节第 1 条：A） |
| 去掉以上内容后为空的消息 | 不导入（例如只调用了工具、没有说话的 Claude 消息） |

说话方：`human` → `### 我 · 时间`，`assistant` → `### Claude · 时间`。时间取消息的 `created_at`。
其他字段（`summary`、`citations`、`account`）不导入。

## 3. 消息树

- 父节点是全零 UUID 的消息是根，必须恰好一个；其余消息沿 `parent_message_uuid` 挂到树上。
- 没有分支时，就是列表顺序。
- 有分支时（编辑或重新生成留下的旧版本），取 `created_at` 最晚的那片叶子，沿父节点走回根，只导入这一条路径。导出里没有 ChatGPT 那样的 `current_node`，这是最接近「你最后看到的那一版」的规则。
- 有循环、有多个根、父节点指向不存在的消息：明确报错，整次导入停止，不写任何文件（与 F4-05 相同）。

## 4. `--include-thinking`（docs/ROADMAP.md V1 已列）

- 默认关闭，行为与 V0 一致。
- 打开后，思考过程放在它所属的 Claude 消息正文**前面**，用 Obsidian 的折叠块，默认收起：

  ```markdown
  > [!note]- 思考过程
  > ……
  ```

  每一行都加 `> ` 前缀，所以思考过程里的 `### Claude · ` 这类行不会被误认成新消息。
- 本次只做 Claude。ChatGPT 的 `thoughts` / `reasoning_recap` 是否也支持，等这一版用过之后再定。
- 已经导入过的对话，换一个开关再导入时，要能更新正文，即使对话本身没有新消息（见第 7 节第 2 条）。

## 5. 命令

```
eia import claude <conversations-NNN.zip> [<更多 zip>...] [--vault <路径>] [--dry-run] [--include-thinking]
```

- 不用解压。传的是 manifest 或其他 zip 时，明确报错：「请传入 conversations-NNN.zip」。
- 其余行为（结束时的汇总、退出码、先完整解析再写入、重复导入规则）与 `eia import chatgpt` 完全相同，复用 `importer.py`。
- 文件位置：`chats/claude/YYYY-MM-DD-<标题>-<后缀>.md`；frontmatter 与 ChatGPT 相同，`platform: claude`、`source: claude`。

## 6. 对 docs/TEST-PLAN.md 的修改（批准后生效）

把「F5 导入 Claude 对话（已挪到 V1）」一节换成：

| 编号 | 验收标准 |
|---|---|
| F5-01 | 样例导出中的每个对话生成一个文件，数量一致；文件在 `chats/claude/`，全部通过 `eia validate` |
| F5-02 | 消息顺序、说话方、正文与原始导出一致；中文、代码块、表情符号不损坏 |
| F5-03 | 工具调用、工具结果、系统插入的提示、思考过程（默认）不出现在正文；图片、文件、附件占位出现；只有工具调用的消息不生成空标题 |
| F5-04 | 消息树有分支时只导入最新叶子所在的路径；有循环、多个根、父节点不存在时报错且不写任何文件 |
| F5-05 | 不是 Claude 对话导出的 zip（含 manifest、其他分类的 zip、ChatGPT 导出）明确报错，不写任何文件 |
| F5-06 | `updated` 取对话与所有消息中最晚的时间；对话 `updated_at` 没变但有新消息时，重新导入会更新原文件 |
| F5-07 | 重复导入满足 F6-01 至 F6-07 的全部要求（用 Claude 样例再测一遍） |
| F5-08 | `--include-thinking` 打开时思考过程以默认收起的折叠块出现在对应消息前；里面像消息标题的行不会被误认 |
| F5-09 | 空标题的对话标为「未命名对话」；导出里没有模型名时 `model` 为空，`eia validate` 通过 |

测试样例是合成的：结构照真实导出，内容全部虚构，放在 `tests/chat/` 的 conftest 里，与 ChatGPT 的做法一致。

## 7. 作者的决定（2026-11-20）

1. **粘贴的长文本附件**怎么导入？
   - A（推荐）：只放占位 `[附件：文件名，N 字]`。和图片、文件一致，正文不会被几万字的粘贴内容撑长。
   - B：全文导入，放进默认收起的折叠块。那是你自己贴进去的内容，以后做画像、搜索时可能有用，但会让个别对话变得很长。
2. **只切换 `--include-thinking`、对话本身没有新消息时**，要不要重写正文？
   - A（推荐）：要。在文件的 frontmatter 里记一个 `thinking: true/false`，开关和上次不同时就重写标记之间的内容。这会给 `chat` 条目加一个可选字段，`docs/specs/vault-schema.md` 要同步写一份 delta。
   - B：不要。只有对话有新消息时才更新，想要思考过程得删掉文件重新导入。（这会违反「不删除用户文件」的习惯，不推荐。）
3. `projects`、`memories`、`artifact_data` 是否以后要导入？默认放进 Backlog，V1 不做。
