# Delta Spec: chat-import V1（Gemini 部分）

Status: **暂缓**（2026-11-20 作者选第 7 节第 1 条 B：等用对的 Google 账号拿到 Takeout，看有没有时间信息再做）　版本：V1
改动对象：`docs/specs/chat-import.md`（已批准）；与 `docs/specs/chat-import.delta-v1.md`（Claude）并列
依据：2026-11-20 对作者用浏览器插件导出的 Gemini 对话做的结构检查（只读字段名、类型和计数，不读、不复制对话内容）。

## 1. 为什么不是官方导出

Google Takeout 三次导出都没有对话（很可能用错了 Google 账号，见 docs/ROADMAP.md V1）。现有数据来自一个浏览器插件：
它打开 gemini.google.com 上的每个对话，把页面上显示的内容存成 JSON。所以：

- **没有任何时间信息**：对话和消息都没有创建时间、更新时间，只有导出时间。
- 格式由插件决定，不是 Google 的契约，插件改版就可能变。解析器只认下面这一版，认不出就明确报错。
- 本地保存的页面内容不包括 Gemini 的思考过程，`--include-thinking` 对 Gemini 不适用。

## 2. 导出文件的结构（插件版本未标注，2026-11-19 导出）

一个 zip（`gemini_chats_YYYY-MM-DD.zip`），根目录下全是 JSON，没有子目录：

| 文件 | 内容 | 处理 |
|---|---|---|
| 每个对话一个 JSON（约 180 个，文件名是 `<4 位序号>_<标题>.json`） | `conversationId`（16 位十六进制）、`title`、`url`、`exportedAt`、`messageCount`、`messages[]` | 导入 |
| 一个汇总报告 JSON（有 `sidebarTotal`、`missing[]`、`exported` 等字段） | 侧边栏一共多少个对话、哪些没导出成功 | 只读几个计数，打印一行提示；不导入 |

每条消息：`role`（`user` / `assistant`）、`content`（纯文本）、`blocks[]`。每个 block 有 `tag`（页面元素：`p`、`li`、`h1`–`h6`、`pre`、`blockquote`、`th`、`td`）、`text`（纯文本）、`formatted`（已经转成 Markdown 的文字）。

| 观察 | 数值（约） | 对设计的影响 |
|---|---|---|
| 对话数 / 消息数 | 约 180 / 约 4,500 | 整个读入内存即可 |
| 侧边栏总数 vs 导出成功 | 约 430 个里有约 250 个「打开超时」没导出 | 导入时提示；以后用插件补导出后再导入一次即可（重复导入是安全的） |
| 你的消息开头 | 几乎每条都有一个 `h5` 块：`你说 <前几十个字>…`，是页面给读屏软件用的隐藏标签，真正的正文在后面的块里 | **跳过这个标签块**；后面没有块时，才用标签里的文字（去掉「你说」） |
| `formatted` | 标题、列表、代码块、引用都带 Markdown 标记 | **用 `formatted`，不用 `content`**，这样格式不会丢 |
| 表格 | 表格被拆成一个个 `th` / `td` 单元格 | 按「先连续的 `th` 是表头，后面的 `td` 按列数分行」拼回 Markdown 表格；拼不回的（极少数）每个单元格单独一行 |
| 列表 | 每个 `li` 一个块，`formatted` 都是 `- ` 开头（有序列表的序号丢了） | 连续的 `li` 紧挨着放，不空行 |
| Gemini 连续发言 | 三百多处两条 Gemini 回复挨在一起，中间没有你的消息 | 原样导入，两个 `### Gemini` 标题挨着；不猜、不补 |
| 以 Gemini 开头的对话 | 约 20 个对话第一条就是 Gemini | 原样导入 |
| `url` | `https://gemini.google.com/u/1/app/<conversationId>?pageId=none` | 写进 frontmatter 的 `url`，在 Obsidian 里点一下能回到原对话 |

## 3. 时间（需要作者决定，见第 7 节第 1 条）

推荐做法：

- frontmatter 的 `created` 和 `updated` 都写**第一次导入时这份导出的 `exportedAt`**。之后重新导入不改 `created`。
- 文件名里的日期因此是导出那天，不是聊天那天。
- 每条消息的标题写成 `### 我 · 时间未知` / `### Gemini · 时间未知`。前缀 `### 我 · ` 与已有契约一致，以后的 `chat-reader` 不用改。
- frontmatter 加 `time_known: false`，以后拿到带时间的数据（比如 Takeout）时，程序能认出哪些文件的时间要补。这是 chat 条目的一个新的可选字段，`docs/specs/vault-schema.delta-v1.md` 同步补一行。

## 4. 重复导入怎么判断「有没有变」

没有更新时间可比，改成比内容：

1. 按 `platform + conversation_id` 找已有文件（与原 spec 相同）。
2. 把这次导出渲染成对话块，与文件里两条标记之间的内容比较：一样 → 「未变」。
3. 不一样，且这次的消息数 **≥** 文件里的 `message_count` → 替换标记之间的内容，frontmatter 只改 `title`、`updated`、`message_count`。
4. 不一样，但这次的消息**更少** → **不修改**，计入「跳过」，原因写「这次导出的消息比文件里少，可能导出不完整」。防止插件某次没加载完，把已经导入的内容冲掉。

## 5. 命令

```
eia import gemini <插件导出的 zip> [--vault <路径>] [--dry-run]
```

- 文件位置：`chats/gemini/YYYY-MM-DD-<标题>-<后缀>.md`，`platform: gemini`、`source: gemini`、`model: null`。
- 结束时多打印一行：`提示  插件报告侧边栏共 N 个对话，其中 M 个没有导出成功`。
- 其余行为（汇总、退出码、先完整解析再写入）与另外两个平台相同，复用 `importer.py`。

## 6. 对 docs/TEST-PLAN.md 的修改（批准后生效）

新增「F8 导入 Gemini 对话」：

| 编号 | 验收标准 |
|---|---|
| F8-01 | 每个对话 JSON 生成一个文件，数量一致；汇总报告不生成文件；全部通过 `eia validate` |
| F8-02 | 消息顺序、说话方与导出一致；「你说」标签块不出现在正文；标题、列表、代码块、引用的 Markdown 保留 |
| F8-03 | 表格单元格拼回 Markdown 表格；拼不回时每个单元格单独一行，内容不丢 |
| F8-04 | 没有时间时，`created` / `updated` 取导出时间，消息标题为 `· 时间未知`，`time_known: false`；重新导入不改 `created` |
| F8-05 | 内容没变 → 未变；消息变多 → 更新原文件；消息变少 → 跳过并说明原因，文件不动 |
| F8-06 | 用户的标签、字段、标记外的笔记在重新导入后保留（F6 的要求用 Gemini 样例再测一遍） |
| F8-07 | 不认识的 JSON、不是 zip、Claude 或 ChatGPT 的导出，明确报错，不写任何文件 |
| F8-08 | 汇总报告里没导出成功的对话数被打印出来；没有汇总报告时也能正常导入 |

## 7. 需要作者决定

1. **时间**：插件导出里没有聊天时间。
   - A（推荐）：照第 3 节，日期写导出那天，消息写「时间未知」，并做标记以后补。
   - B：先不导入 Gemini，等用对的 Google 账号拿到 Takeout（可能带时间）再做。
2. **另外约 250 个没导出成功的对话**：不影响这次导入。以后你用插件再导出一次，重新运行导入，只会补上新的。要不要现在先补导出，由你决定。
