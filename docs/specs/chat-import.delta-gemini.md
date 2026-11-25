# Delta Spec: chat-import V1（Gemini 部分）

Status: **草案，等作者批准**（2026-11-25 按新版插件导出重写；第 7 节两条作者已口头同意推荐做法）　版本：V1
改动对象：`docs/specs/chat-import.md`（已批准）；与 `docs/specs/chat-import.delta-v1.md`（Claude）并列
依据：2026-11-25 对作者用浏览器插件导出的 `gemini_chats_2026-11-25.zip` 做的结构检查（只读字段名、类型、格式和计数，不读、不复制对话内容）。

## 0. 和 2026-11-20 那版草案的区别

旧草案依据 11-19 的旧版插件导出：没有任何时间、正文拆成页面元素块（`blocks[]`）、约 430 个对话只导出了约 180 个，所以设计了「时间未知」、`time_known` 标记、按内容比较是否变化、拼回表格单元格等做法。
新版导出有真实时间、正文已是 Markdown、全部导出成功，这些做法**全部删掉**，改成和 Claude 一样的处理方式。旧版导出不再支持（第 5 节）。
Google Takeout 一直没导出对话，不再等它。

## 1. 为什么不是官方导出

Gemini 没有可用的官方对话导出（Takeout 没有导出对话）。数据来自一个浏览器插件：它打开 gemini.google.com 上的每个对话，把内容存成 JSON。所以：

- 格式由插件决定，不是 Google 的契约，插件改版就可能变。解析器只认下面这一版，认不出就明确报错（F8-07）。
- 导出里没有 Gemini 的思考过程，`--include-thinking` 对 Gemini 不适用（传了也不报错，没有效果）。
- 导出里没有模型名，`model` 写 `null`。

## 2. 导出文件的结构（2026-11-25 导出）

一个 zip（`gemini_chats_YYYY-MM-DD.zip`），根目录下全是 JSON，没有子目录：

| 文件 | 内容 | 处理 |
|---|---|---|
| 每个对话一个 JSON（439 个，文件名 `<4 位序号>_<标题>.json`） | 见下表 | 导入 |
| 一个汇总报告 JSON（`generatedAt`、`listTotal`、`exported`、`stoppedManually`、`failures[]`、`missing[]`、`possiblyIncomplete[]`） | 侧边栏一共多少个对话、哪些没导出成功 | 只读计数，打印提示（第 5 节）；不导入 |

对话 JSON：

| 字段 | 观察 | 处理 |
|---|---|---|
| `conversationId` | 全部是 `c_` + 16 位十六进制 | 作为 `conversation_id`，原样保存 |
| `title` | 都不为空 | 空的时候写「未命名对话」 |
| `url` | `https://gemini.google.com/u/<n>/app/<id 去掉 c_>` | 写进 frontmatter 的 `url`，在 Obsidian 里点一下能回到原对话 |
| `createdAt` / `updatedAt` / `exportedAt` | ISO 8601，UTC，带毫秒（`2026-06-01T08:00:00.123Z`）；439 个对话里 `createdAt` 全部等于第一条消息的时间，`updatedAt` 全部等于最后一条 | `created` 取 `createdAt`，`updated` 取 `updatedAt` 与所有消息时间里最晚的一个（防插件以后改了算法）；截到秒；`exportedAt` 不用 |
| `turnCount`、`messageCount` | `messageCount` 与 `messages[]` 条数一致；`turnCount` 有 130 个对话与「我的消息数」不同，含义不明 | 都不用，`message_count` 按实际导入的消息数算 |
| `messages[]` | 每条只有 `role`（`user` / `assistant`）、`time`、`content` | 见第 3 节 |

规模：439 个对话、10,223 条消息（我 4,724 条、Gemini 5,499 条），2025-11 到 2026-11。整个读入内存即可。

## 3. 每条消息怎么处理

| 观察 | 处理 |
|---|---|
| `content` 已是 Markdown（标题、列表、代码块、表格、引用都在） | 原样导入。正文里像 `### 我 · ` / `### Gemini · ` / `<!-- eia:` 的行按已有规则转义（原 spec 第 10 节第 5 条）；普通的 `###` 标题不受影响 |
| `time` **按一轮记**：你的一句话和紧接着的 Gemini 回答共用同一个时间（就是你发出的时间）；毫秒各不相同，是真实时间 | 原样用。消息标题 `### 我 · 时间` / `### Gemini · 时间`，同一轮的两条显示同一分钟 |
| 784 处 Gemini 连续发言（中间没有你的消息），各自有时间 | 原样导入，两个 `### Gemini` 标题挨着；不猜、不合并 |
| 49 个对话第一条是 Gemini | 原样导入 |
| 7 条我的消息 `content` 为空（大概是只发了图片或文件，导出里没有文件） | 不导入，与 Claude「去掉后为空的消息」一致。整个对话一条消息都没有时跳过并说明 |
| 旧版的「你说 …」读屏标签 | 新版基本没有了（我的消息里 2 条以它开头），不做特殊处理 |

## 4. 重复导入

和 ChatGPT、Claude 相同：按 `platform + conversation_id` 找已有文件（不依赖文件名）；`updated` 没变 → 「未变」；变新了 → 只替换标记之间的内容和 frontmatter 的少数字段，保留标签、自加字段和标记下面的笔记。

多加一条保护（第 7 节第 2 条）：`updated` 变新了，但这次的消息数**少于**文件里的 `message_count` → **不修改**，计入「跳过」，原因写「这次导出的消息比文件里少，可能导出不完整」。防止插件某次没加载完，把已经导入的内容冲掉。代价：你在 Gemini 网页上删掉的消息，vault 里会一直留着，要自己删。

## 5. 命令

```
eia import gemini <插件导出的 zip> [--vault <路径>] [--dry-run]
```

- 文件位置：`chats/gemini/YYYY-MM-DD-<标题>-<后缀>.md`，`platform: gemini`、`source: gemini`、`model: null`。
- 汇总报告里 `failures`、`missing`、`possiblyIncomplete` 有任何一项不为空，或 `stoppedManually` 为真，结束时多打印一行提示，例如：
  `提示  插件报告侧边栏共 439 个对话，导出 430 个；9 个失败或缺失，3 个可能不完整`。没有汇总报告时不提示，照常导入。
- 认不出的格式明确报错，不写任何文件：不是 zip、zip 里没有对话 JSON、对话缺 `conversationId` / `messages` / 消息缺 `time`。
  **旧版插件导出**（消息带 `blocks[]`、没有 `time`）单独提示：「这是旧版插件的导出，没有时间信息，请用新版插件重新导出」。
- 其余行为（汇总、退出码、先完整解析再写入）与另外两个平台相同，复用 `importer.py`；新增 `src/eia/chat/gemini.py`，产出 `Conversation` / `Message`。
- `thinking` 为 `None`：不写 frontmatter 的 `thinking` 字段（与 `chat/model.py` 现有约定一致）。
- `url`：`Conversation` 新增可选字段 `url`（默认 `None`）。新建文件时写进 frontmatter；更新时只在文件里没有 `url` 时补上，用户改过的不动。ChatGPT、Claude 是 `None`，行为不变。

## 6. 对 docs/TEST-PLAN.md 的修改（批准后生效）

新增「F8 导入 Gemini 对话」（编号早已预留）：

| 编号 | 验收标准 |
|---|---|
| F8-01 | 每个对话 JSON 生成一个文件，数量一致；汇总报告不生成文件；全部通过 `eia validate` |
| F8-02 | 消息顺序、说话方与导出一致；Markdown 原样保留；像消息标题或 eia 标记的行被转义，`chats dump` 读回与原文一致 |
| F8-03 | `created` / `updated` / 消息时间来自导出，截到秒，文件名日期按 vault 时区；同一轮的两条消息时间相同 |
| F8-04 | 空消息不导入；全是空消息的对话跳过并说明；空标题写「未命名对话」 |
| F8-05 | `updated` 没变 → 未变；变新且消息变多 → 更新原文件；变新但消息变少 → 跳过并说明原因，文件不动 |
| F8-06 | 用户的标签、字段、标记外的笔记在重新导入后保留（F6 的要求用 Gemini 样例再测一遍） |
| F8-07 | 不是 zip、不认识的 JSON、Claude 或 ChatGPT 的导出、旧版插件导出，明确报错（旧版有专门提示），不写任何文件 |
| F8-08 | 汇总报告里有失败、缺失、可能不完整或手动停止时打印提示；没有汇总报告时也能正常导入 |

测试数据用 `tests/chat/` 里合成的 zip，绝不使用 `exports/` 里的真实导出。

## 7. 需要作者决定

1. **Gemini 连续发言**：原样导入，不合并（推荐；2026-11-25 作者同意）。
2. **消息变少时跳过**：保留这条保护，代价是网页上删掉的消息 vault 里不会跟着删（推荐；2026-11-25 作者同意）。
3. **批准本草案**：批准后按第 6 节登记 F8，然后实现。
