# Spec: web-clip（网页剪藏对齐 schema）

Status: 已批准 2026-11-24（第 6 节：方案 A，默认加 `clip` 标签）　版本：V1
依据：docs/ROADMAP.md V1 `web-clip`；docs/specs/vault-schema.md（`clip` 类型已存在，目录 `inbox/clips/`）；
2026-11-24 读了 Obsidian Web Clipper 官方帮助的 variables / filters / templates 三页，
以及插件源码（obsidianmd/obsidian-clipper 1.8.5 的 `types.ts`、`import-export.ts`、`shared.ts`）和它用的过滤器库 knap 0.6.0。

## 1. 要做的事

用户在浏览器里点 Obsidian Web Clipper（官方插件，免费）剪下一篇网页，文件直接落进 vault 的 `inbox/clips/`，并且：

- 通过 `eia validate`（不出错误）；
- 能被 `eia search`、以后的画像和兴趣模块当作材料使用（剪藏是「你主动存下来的东西」，比对话频率更能说明兴趣）。

eia 不写浏览器插件，只提供**插件模板**和说明。

## 2. 插件能做什么、不能做什么（官方帮助，2026-11-24）

| 需要 | 插件 | 影响 |
|---|---|---|
| 标题、链接、正文、作者、发布时间、站点名 | 有：`{{title}}` `{{url}}` `{{content}}`（Markdown）`{{author}}` `{{published}}` `{{site}}` | 直接用 |
| 当前时间 | 有：`{{time}}`，可用 `date` 过滤器按 day.js 格式输出，`date_modify` 加减时间 | 见下一行 |
| **UTC 时间**（schema 要求 `Z` 结尾） | **没有**转 UTC 的选项 | 只能写本地时间，或按固定时差手动减 |
| **随机 id 后缀**（6–12 位 `[a-z2-7]`） | **没有**随机 / uuid 变量 | 只能从时间派生 |
| 字符替换 | 有：`replace`，支持多组和正则 | 可以把时间里的数字换成字母 |
| 存到子文件夹 | 帮助页没写清楚（印象中模板有「位置」设置） | 需要实测 |
| 模板导入导出 | 有，是 `.json` 文件 | 字段名帮助页没写，要从插件导出一个模板确认 |

## 3. 方案

### A（推荐）：模板生成全部字段，时差在生成模板时写死

`eia clip template [--vault]` 按 vault 的 `config.yaml` 时区生成一个模板 `.json`，用户在插件里导入一次。模板写出：

```yaml
schema: 1
id: clip-20261124-hcdaeb        # 日期 + 时分秒，数字 0–9 用 replace 换成字母（见下）
type: clip
title: <网页标题>
created: 2026-11-24T06:30:14Z   # {{time}} 先 date_modify 减去时差，再格式化成带 Z 的 UTC
status: inbox
source: <站点名>
url: <链接>
author: <作者>                   # 未知字段，validate 只警告不报错
published: <发布时间>             # 同上
tags: []
```

- id 后缀：取本地时间 `HHmmss`（6 位），数字按 `0→a 1→b … 9→j` 换成字母。同一天同一秒剪两篇才会撞，`eia validate` 会报重复 id。
- 时差写死在模板里：作者在马来西亚（+08:00，没有夏令时）没有问题。有夏令时的地区一年会错两次一小时，说明里写清楚，换季时重新生成模板。
- 优点：剪完就合规，不需要再跑任何命令；不改 schema。
- 风险：时间格式化和 `date_modify` 的实际行为只能在插件里实测（第 5 节 M-W1）。实测不行就退到 B。

### B：模板只写能写的，`eia clip adopt` 补齐

模板只写 `type`、`title`、`url` 等。剪下的文件先是不合规的，用户运行 `eia clip adopt`（或加进每天的定时任务），给 `inbox/clips/` 里缺字段的文件补上 `schema`、`id`、`created`（UTC）、`status`。

- 优点：不依赖插件的时间格式化细节。
- 缺点：要多一步；剪完到补齐之间 `validate` 会报错；会修改用户文件（只用 `update_frontmatter` 追加缺失字段，不动已有字段和正文），和 CONSTRAINTS「不自动修改用户已有文件」有冲突，需要作者明确同意。

### C：放宽 schema，`created` 接受带时差的时间

改 vault-schema：`created` / `updated` 允许 `+08:00` 这类时差，读入时转成 UTC。

- 优点：模板最简单。
- 缺点：改动全库的必填字段规则（vault-schema 的 Boundaries 要求先问）；所有模块读时间的地方都要确认没问题。为了一个插件改契约，不划算。

## 3.1 插件源码里查到的、决定写法的事实（2026-11-24）

| 事实 | 出处 | 影响 |
|---|---|---|
| `{{time}}` = `dayjs().format('YYYY-MM-DDTHH:mm:ssZ')`，本机时间带时差 | clipper `shared.ts` | 要自己换算 UTC |
| `date_modify` 的结果是 `format("YYYY-MM-DD")`，**只有日期** | knap `date_modify.ts` | 不能「减 8 小时再格式化」；但它得到的日期正好是 UTC 日期 |
| `date` 过滤器加载了 advancedFormat，`X` 是 Unix 秒数 | knap `date.ts` | id 后缀用 Unix 秒数，与时区无关 |
| `replace` 多组替换按顺序执行，后面的组看得到前面的结果；搜索词必须加引号 | knap `replace.ts` | 替换表要设计成输出不会被后面的组再命中 |
| `datetime` 类型的属性，值里有 `\|date:` 就原样写、不加引号；`text` 加双引号；`multitext` 写成列表；`number` 不加引号 | clipper `shared.ts` | `created` 用 `datetime`，YAML 读出来是时间 |
| 导出的模板 JSON：`schemaVersion` 0.1.0、`name`、`behavior`、`noteContentFormat`、`properties[{name,value,type}]`、`triggers`、`noteNameFormat`、`path`；不含 vault | clipper `import-export.ts` | vault 要用户在插件里自己选 |

因此（取代第 3 节 A 里「date_modify 减去时差」的写法）：

- **id**：`clip-<UTC 日期>-<后缀>`。UTC 日期 = `{{time|date_modify:"-8 hours"|replace:"-":""}}`；
  后缀 = Unix 秒数的后 6 位 + 前 4 位，数字 0–9 换成 a–j，共 10 位（文件名取前 6 位，所以把变化快的放前面）。同一秒剪两篇才会重复。
- **created**：`<UTC 日期>T<UTC 小时>:<分:秒>Z`。UTC 小时用两轮字面替换：先把本地的两位小时直接换成「UTC 小时的字母编码」（24 组），
  再把字母换回数字（10 组）。只用字面替换，不依赖正则开关；只支持整小时的时差，不是整小时的时区直接报错。
- 默认 `tags: [clip]`。

## 4. 测试编号（批准后登记到 docs/TEST-PLAN.md，F16）

按方案 A：

| 编号 | 验收标准 |
|---|---|
| F16-01 | `eia clip template` 生成合法 JSON；路径指向 `inbox/clips`；字段与第 3 节一致 |
| F16-02 | 按模板规则（用 Python 复现插件的变量、`date` / `date_modify` / `replace` / `slice` 和属性写法）渲染出的样例文件通过 `eia validate`；一天 24 个小时、跨日、跨年时 `created` 和 id 的日期都等于真实 UTC |
| F16-03 | 时差按 `config.yaml` 时区计算；时区留空用本机时区；有夏令时的时区打印提醒 |
| F16-04 | 同一秒的两篇剪藏 id 相同，`eia validate` 报重复 id（已有规则，用剪藏样例再测一遍） |
| F16-05 | 剪藏的 `author` / `published` 等额外字段只警告不报错；`eia search` 能搜到剪藏正文 |

手动验收：

| 编号 | 内容 |
|---|---|
| M-W1 | 作者在浏览器里导入模板，剪 3 篇（中文、英文、无作者的页面），文件落在 `inbox/clips/`，`eia validate` 0 错误，`created` 与实际时间一致 |

## 5. 不做

- 不写、不改浏览器插件；其他剪藏插件（MarkDownload 等）以后有需要再说。
- 不抓全文以外的东西（评论、图片下载）；图片保留插件给的外链。
- 剪藏的 AI 摘要、自动打标签：V2，需要明确开启。

## 6. 需要作者决定

1. 方案：**A（推荐）** / B / C。→ 2026-11-24 作者选 **A**。
2. 剪藏要不要默认加 `tags: [clip]` 之类的标签（不加也能按 `type: clip` 查）。→ 作者选**加**。
