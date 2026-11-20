# Spec: vault-schema

版本：V0（规格版本 schema 1）

Status: 已批准 2026-11-16；2026-11-20 按实际实现修订（决策见 docs/adr/0001、0002）。

## Objective

定义整个系统唯一的数据契约：vault 的目录结构、每个 Markdown 文件的 frontmatter 格式，以及一个校验器。
所有其他模块只按这个契约读写文件，彼此不直接通信。

用户：安装本工具的个人（包括 作者）。
成功的样子：任何模块、任何外部工具（Obsidian、Khoj、Quartz 私密网站；Logseq 对 YAML frontmatter 支持有限，不作保证）都能直接打开 vault；`eia validate` 能指出哪个文件不合规、为什么。

## ASSUMPTIONS

1. 一个文件 = 一个条目（一篇剪藏 / 一条大事 / 一次对话 / 一篇笔记）。
2. frontmatter 用 YAML；正文是普通 Markdown，不做格式限制。
3. 文件名：`YYYY-MM-DD-<slug>-<id后缀6位>.md`。slug 允许中文，但做 NFC 归一化、去除 Windows 保留字符 `<>:"/\|?*`、截断到 60 字节；带 id 后缀保证同日同名不冲突。
4. `id` 全局唯一且一旦生成不变。一般条目后缀为 6 位随机 base32；**chat 条目后缀为 12 位，由 `platform + conversation_id` 哈希得到（确定性）**，重复导入命中同一 id → 更新该文件而非新建。
5. 时间一律存 UTC（ISO 8601 带 `Z`）；文件名中的日期按 `config.yaml` 的 `timezone`（默认系统时区）计算。
6. 建议用 Git 管理 vault，`eia init` 只生成 `.gitignore`，不建仓库；本模块不执行任何 git 操作。

## Commands

```
安装:     pip install -e ".[dev]"
初始化:   eia init <path>            # 创建目录结构 + config.yaml 模板 + .gitignore
校验:     eia validate [<path>]      # 退出码 0=全部合规，1=有错误；--json 输出机器可读结果
新建条目: eia new <type> --title "..."  # 生成带合法 frontmatter 的空文件
测试:     pytest -q
检查:     ruff check . && ruff format --check .
```

## Project Structure

工具仓库（代码）：
```
everything-in-ai/
├── src/eia/
│   ├── cli.py            # click 入口，注册各模块子命令
│   └── schema/
│       ├── model.py      # 条目数据模型（pydantic）
│       ├── io.py         # 读写 md + frontmatter（只改动变的行，见 ADR 0001）
│       ├── ids.py        # id 生成
│       ├── naming.py     # 文件名规则
│       ├── vault.py      # 目录结构、init、读 config
│       ├── create.py     # eia new
│       └── validate.py   # eia validate
└── tests/schema/
```

用户 vault（数据），由 `eia init` 生成：
```
vault/
├── config.yaml
├── .gitignore        # 至少包含 .env、.eia/
├── inbox/
│   ├── headlines/    # type: headline
│   ├── digest/       # type: digest
│   └── clips/        # type: clip
├── chats/
│   ├── chatgpt/  claude/  gemini/   # type: chat
├── notes/            # type: note
└── topics/           # type: topic
```

## Frontmatter 规范 v1

所有条目必填：

| 字段 | 类型 | 说明 |
|---|---|---|
| `schema` | int | 固定 `1`，供以后迁移 |
| `id` | str | `<type>-<YYYYMMDD>-<后缀>`，后缀为小写字母和 2-7：普通条目 6 位随机，如 `clip-20261115-k3f9qa`；chat 条目 12 位确定性 |
| `type` | enum | `headline` `digest` `clip` `chat` `note` `topic` |
| `title` | str | 非空 |
| `created` | datetime | ISO 8601 UTC |
| `status` | enum | `inbox` `processed` `archived` |

选填（通用）：`updated`、`tags: [str]`（小写，不含空格；写成空值 `tags:` 视为空列表）、`source`（来源标识，如 `reuters`、`claude`）、`url`、`lang`（`zh` / `en` / ...）、`links: [id]`。

按 type 的附加字段：

| type | 必填 | 选填 |
|---|---|---|
| `headline` | `sources: [{publisher, url}]`, `independent_count: int` | `cluster_id` |
| `chat` | `platform` (`chatgpt`/`claude`/`gemini`), `conversation_id` | `model`, `message_count` |
| `digest` | `date` | `score` |
| 其他 | — | — |

未知字段：保留并原样写回（允许外部工具加自己的字段），校验只给 warning 不报错。

校验范围：只检查 `.md` 文件；忽略 `config.yaml`、`.eia/`、`.obsidian/`、`logseq/`、`.git/`。额外规则：`id` 全库唯一；`chat` 的 `(platform, conversation_id)` 全库唯一；`.gitignore` 必须包含 `.env`。

## Code Style

```python
# src/eia/schema/model.py
class Entry(BaseModel):
    model_config = ConfigDict(extra="allow")  # 保留未知字段

    schema_version: Literal[1] = Field(alias="schema")
    id: StrictStr = Field(pattern=ID_PATTERN)
    type: EntryType
    title: NonBlankStr
    created: UtcDatetime
    status: Status
    tags: list[Tag] = []   # 空值 `tags:` 视为 []
```

- 类型注解全覆盖；pydantic v2 做校验；PyYAML 解析，修改已有文件时只替换被改动的字段所在的行（ADR 0001）。
- 函数短小、纯函数优先；IO 只在 `io.py`。
- 报错信息写清楚：文件路径 + 字段 + 期望值。

## Testing Strategy

- pytest；`tests/schema/fixtures/` 放合法与非法样例 md。
- 单元：每个必填字段缺失 / 类型错误各一条用例；未知字段 round-trip 不丢失；中文 slug。
- 集成：`eia init` → `eia new` 每种 type → `eia validate` 退出码 0。
- 覆盖率目标：`schema/` ≥ 90%。

## Boundaries

- Always：读写保持未知字段和正文原样；写文件用原子替换（先写临时文件再 rename）。
- Ask first：改动任何必填字段、增加新 type、修改 `schema` 版本号。
- Never：删除用户文件；在校验时自动"修复"文件（V1 只报告）；执行 git 操作。

## Success Criteria

1. `eia init /tmp/v` 生成上述目录和 `config.yaml` 模板。
2. `eia new clip --title "测试"` 产生的文件通过 `eia validate`。
3. fixtures 中每个非法样例，`eia validate` 都报出正确的文件和字段，退出码 1。
4. 读入再写出带未知字段和中文的文件，逐字节不变。
6. 文件名含中文、超长标题、保留字符时，V0 在 Windows 上能正常创建（CI：windows-latest）；路径处理一律用 pathlib，不写死分隔符，V1 扩到三系统。
7. 两个文件 `id` 相同或 chat `conversation_id` 重复时，`eia validate` 报错。
5. 用 Obsidian 打开 `eia init` 生成的 vault，无报错，frontmatter 正常显示（手动验证一次）。

## Out of Scope (Deferred)

见 ROADMAP.md。本模块延后项：`validate --fix`、schema 迁移工具、长对话拆分。

## Decisions（原 Open Questions，按默认值定下）

1. `id` 采用可读格式 `<type>-<YYYYMMDD>-<后缀>`；普通条目 6 位，chat 条目 12 位（ADR 0002）。
2. 一次 AI 对话 = 一个文件，不拆分。
