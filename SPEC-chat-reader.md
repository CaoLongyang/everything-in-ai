# Spec: chat-reader

Status: 已批准 2026-11-21（第 6 节：笔记算「我」的话，单独标记；粘贴内容的问题挪到 V2）　版本：V1
依据：`SPEC-chat-import.md` 第 5 节的正文契约（`### 我 · 时间` / `### <平台> · 时间`，两条 `eia:conversation` 标记）。

## 1. Objective

从 vault 里已导入的 `chat` 文件，按说话方读回对话，**能只取「我」说的话**。
给以后的分析、画像、兴趣模块用，避免把 AI 的观点当成你的想法。

只读：不改任何文件，不调用 AI，不联网。

成功的样子：`eia chats dump --only-me` 输出你在所有对话里说过的话，每条带时间、平台和来源文件 id，AI 的回答一句都不混进来。

## 2. 读什么、不读什么

| 内容 | 处理 |
|---|---|
| 两条标记之间、`### 我 · 时间` 下的文字 | 「我」的消息 |
| `### ChatGPT / Claude / Gemini · 时间` 下的文字 | AI 的消息 |
| 消息前的「思考过程」折叠块 | 属于 AI，放在 AI 消息的 `thinking` 里；`--only-me` 时不出现 |
| 导入时加的转义（行首 `\`） | 还原成原文 |
| `### 我 · 时间未知`（Gemini 将来可能这样） | 时间为空，消息照常读 |
| 结束标记下方你自己写的笔记 | 见第 6 节第 1 条 |
| frontmatter | 只取 id、title、platform、created、updated、tags |

坏文件（标记缺失或重复、YAML 坏掉、不是 chat 类型）报"跳过 + 原因"，不中断其他文件。

## 3. 命令

```
eia chats dump [--vault <路径>] [--only-me] [--no-notes] [--platform chatgpt|claude|gemini]
               [--since 2026-01-01] [--until 2026-06-30] [--format text|jsonl]
eia chats stats [--vault <路径>]     # 每个平台：对话数、我的消息数、我的字数
```

- `--format jsonl`：一行一条消息：`{"id", "platform", "title", "role", "time", "text"}`，给其他程序用。
- `--format text`（默认）：人能读的格式，用来自己检查读得对不对。
- `--since` / `--until` 按**消息时间**筛选，日期按 `config.yaml` 的时区算。

## 4. Interface

```python
# src/eia/chat/reader.py
@dataclass(frozen=True)
class ReadMessage:
    role: Literal["user", "assistant"]
    time: datetime | None
    text: str
    thinking: str = ""

@dataclass(frozen=True)
class ReadChat:
    id: str
    path: Path
    title: str
    platform: str
    tags: tuple[str, ...]
    updated: datetime | None
    messages: tuple[ReadMessage, ...]
    notes: str                    # 标记之外你自己写的笔记，没有则为空

    def user_messages(self) -> tuple[ReadMessage, ...]: ...

def read_chat(path: Path, tz) -> ReadChat                       # 坏文件抛 ChatReadError
def iter_chats(vault: Path, *, platform=None) -> Iterator[ReadChat | Skipped]
```

和导入用的 `Conversation` 分开：这里的消息来自 vault 文件（你可能改过），时间可能为空，带文件 id 作为证据来源。

## 5. 测试编号（批准后登记到 TEST-PLAN.md，F10）

| 编号 | 验收标准 |
|---|---|
| F10-01 | 用 `eia import` 生成的 ChatGPT、Claude 文件，读回的每条消息的说话方、时间、文字与导入前一致（往返测试） |
| F10-02 | `--only-me` 的输出里没有任何 AI 消息和思考过程 |
| F10-03 | 导入时被转义的行（看起来像标题或标记的行）读回后还原成原文 |
| F10-04 | 对话文件标记坏掉、YAML 坏掉 → 跳过并说明原因，其他文件照常输出；非对话文件直接忽略 |
| F10-05 | `--platform`、`--since`、`--until` 筛选正确；`--format jsonl` 每行都是合法 JSON |
| F10-06 | 命令执行前后 vault 里所有文件逐字节不变 |
| F10-07 | 标记之外的笔记以 `role: note` 输出（不含导入提示行），`--no-notes` 时不出现；没写笔记的对话没有这一条 |

## 6. 作者的决定（2026-11-21）

1. **你自己写的笔记算「我」的话**，单独标成 `role: note`，默认输出，`--no-notes` 关掉。
   - 笔记 = 对话标记之外的正文，去掉导入时写的那行提示。没有内容就没有这一条。
   - 笔记没有时间；`--since` / `--until` 按这个对话 frontmatter 里的 `updated` 筛选笔记。
2. **消息里粘贴的别人的文字（文章、报错、AI 之前的回答）V1 不处理**，程序会当成「我」的话。挪到 V2（ROADMAP「`chat-reader` 区分粘贴内容」）。

## 7. 实现细节

- 哪些文件算对话：frontmatter 的 `type` 是 `chat`，或者在 `chats/` 目录下。其他文件直接忽略，不报跳过。
- 跳过信息写到 stderr，`--format jsonl` 的 stdout 只有数据。
- 消息标题的时间按 `config.yaml` 的时区解析，精确到分钟（导入时就只写到分钟）。解析不了的时间当作「时间未知」，不跳过整个文件。
- 转义还原的局限：如果原文某行本来就以 `\### 我 · ` 这样的反斜杠开头，读回时反斜杠会被去掉。概率极低，不处理。