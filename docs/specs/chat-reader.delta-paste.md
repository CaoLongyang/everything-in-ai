# Delta Spec: chat-reader V2（区分粘贴内容）

Status: 草案 2026-11-26，等批准（第 7 节有 4 个问题要作者定）　版本：V2
改动对象：`docs/specs/chat-reader.md`（已批准）
依据：`docs/ROADMAP.md` V2「`chat-reader` 区分粘贴内容」（2026-11-21 从 V1 挪来，2026-11-24 补充做法）；`chat-reader.md` 第 6 节第 2 条。

## 1. Objective

V1 把「我」消息里的所有文字都当成我的话。可实际上我常把别人的文字贴进去：AI 上一轮的回答、报错、代码、文章。
这些内容如果被画像（`profile`）和兴趣模块当成「我认为什么」，结论会错。

V2 把「我」的每条消息切成几段，**每段标上它是谁的**：

| 标记 | 意思 | 谁来定 |
|---|---|---|
| `own` | 我自己写的 | 程序（没命中任何规则就是这个） |
| `pasted` | 确定是粘贴的，带原因 | 程序，规则能确定的才标 |
| `maybe` | 疑似粘贴，带原因 | 程序标出来，交给以后的分析模块（`profile`）里的 AI 结合上下文再判断 |

**粘贴内容不删**：它说明我「关注了什么」，只是不能当成我「认为什么」。

不变的部分：只读，不改任何文件，不调用 AI，不联网；vault 文件的格式不变（标记是读取时算出来的，不写回文件）。

成功的样子：`eia chats dump --only-me --format jsonl` 的每条消息带分段和标记；把一段 ChatGPT 回答原样贴回去的那段被标成 `pasted`（原因 `ai_echo`）。

## 2. 怎么切段

- 按空行把消息切成段。
- 代码块（三个反引号包起来的部分）整块算一段，里面有空行也不切开。
- 段的顺序和原文一致，所有段的文字拼起来（中间补回空行）等于原消息。

## 3. 规则（按顺序，先命中的算）

| # | 规则 | 标记 | 原因代码 |
|---|---|---|---|
| R1 | 这一段（去掉空白和 Markdown 符号后比较）在**同一对话里更早的 AI 回答**中原样出现，且长度 ≥ 30 字 | `pasted` | `ai_echo` |
| R2 | 代码块 | `pasted` | `code` |
| R3 | 报错堆栈：连续 ≥ 2 行像堆栈的行（`Traceback (most recent call last):`、`  File "...", line N`、`    at xxx(...)`、`Exception in thread`、`error[E0308]:` 一类） | `pasted` | `traceback` |
| R4 | 导入时留下的附件占位 `[附件：...]`（Claude 粘贴长文本） | `pasted` | `attachment` |
| R5 | 没用代码块包起来、但多数行像代码（行尾 `;` `{` `}`、以 `def ` `import ` `function ` 开头等）的段，≥ 3 行 | `maybe` | `code_like` |
| R6 | 很长的段（≥ 400 字），或者有 Markdown 结构（`#` 标题、≥ 3 项的列表、表格） | `maybe` | `long` / `formatted` |
| R7 | 带引号的大段引用：以 `>` 开头的行组成的段 | `maybe` | `quote` |

- R1 只比较同一对话里**这条消息之前**的 AI 回答。贴到别的对话里的 AI 回答 R1 认不出来，交给 R6。
- R5–R7 的门槛（3 行、400 字）是初值，批准后用作者的真实 vault 做只读统计再调（只看数量，不看内容），调后的数写进 ADR。
- 我自己写的笔记（`role: note`）不切段、不打标记，整条算 `own`。

## 4. Interface

```python
# src/eia/chat/paste.py（新文件；reader.py 仍然只做 render.py 的反向，不变）
PartKind = Literal["own", "pasted", "maybe"]

@dataclass(frozen=True)
class Part:
    kind: PartKind
    reason: str          # own 时为空；其他见第 3 节的原因代码
    text: str

def split_message(message: ReadMessage, earlier_ai: Sequence[str]) -> tuple[Part, ...]
def label_chat(chat: ReadChat) -> tuple[tuple[Part, ...], ...]   # 和 chat.user_messages() 一一对应
def own_text(parts: Sequence[Part], *, keep_maybe: bool) -> str  # 只拼出 own（和 maybe）的段
```

## 5. 命令的变化

```
eia chats dump ... [--own-only [--keep-maybe]]   # 加不加见第 7 节第 2 条
eia chats stats ...     # 每个平台多一列：粘贴 N 字、疑似 M 字
```

- `--format jsonl`：「我」的消息多一个 `parts` 字段：`[{"kind": "pasted", "reason": "ai_echo", "text": "..."}, ...]`；`text` 字段仍是整条原文，旧的读法不受影响。
- `--format text`：粘贴的段前面加一行 `〔粘贴：AI 回答〕`、疑似的加 `〔疑似粘贴：很长〕`，方便自己检查标得对不对。

## 6. 测试编号（批准后登记到 docs/TEST-PLAN.md，F17）

| 编号 | 验收标准 |
|---|---|
| F17-01 | 把同一对话里之前的 AI 回答原样贴回的段标成 `pasted` / `ai_echo`；只改了空白或 Markdown 符号也认得；不到 30 字的不标 |
| F17-02 | 代码块、报错堆栈、附件占位分别标成 `pasted` / `code`、`traceback`、`attachment`；代码块内部的空行不会把它切开 |
| F17-03 | R5–R7 命中的段标 `maybe` 并带原因；普通短句标 `own` |
| F17-04 | 所有段拼回去等于原消息（不丢字、不重复） |
| F17-05 | `dump --format jsonl` 的「我」消息带 `parts`，`text` 不变；`--format text` 显示粘贴标签；`stats` 显示粘贴和疑似字数 |
| F17-06 | `interests draft` 第 1 步只用第 7 节第 3 条定下的那部分文字 |
| F17-07 | 命令执行前后 vault 里所有文件逐字节不变 |

## 7. 要作者定的问题

1. **`--only-me` 默认输出什么？**
   - A（推荐）：仍输出整条消息，带上分段标记；要不要去掉粘贴内容由使用方自己决定。和 V1 的输出兼容，粘贴的内容也不会悄悄消失。
   - B：默认去掉 `pasted` 的段，只留 `own` 和 `maybe`。
2. **要不要加一个开关直接只输出「我自己的话」？**
   - A（推荐）：加 `--own-only`：只输出 `own` 段；再加 `--own-only --keep-maybe` 连疑似的一起留。
   - B：不加，只靠 jsonl 里的 `parts`。
3. **V1 的兴趣草稿（`interests draft`）要不要跟着改？**
   - A（推荐）：改。归组只用 `own` + `maybe` 的文字，去掉贴回的 AI 回答和报错（作业对话里贴的题目和报错正是草稿里「混有作业」的一个来源）。
   - B：不动，等 V2-3 兴趣分层时一起改。
4. **R1 的门槛 30 字**：太短会误伤「对，就是『xxx』这句」这种引用一小句 AI 原话的情况，太长会漏掉贴回的短段落。
   - A（推荐）：先定 30 字，真实数据统计后再调。
   - B：作者给一个数。

## 8. 已知局限

- 从网页、文章复制的文字，如果不长、没有格式，规则认不出来，会算 `own`。
- 贴到另一个对话里的 AI 回答，R1 认不出来（只比同一对话），只能靠 R6 标成疑似。
- 规则只看文字本身，不看前后文（比如「下面是报错：」后面那段）；结合上下文的判断留给 `profile` 里的 AI。
