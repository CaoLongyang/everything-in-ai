# Spec: headline-radar

Status: 已批准 2026-11-21（第 8 节：推荐源以注释给出、门槛 3、默认 embed、AI 标题挪到 V2、用 feedparser）　版本：V1
相关：`SPEC-vault-schema.md`（`headline` 条目字段）、`SPEC-llm-adapter.md`、ROADMAP V1「大事不漏」。

## 1. Objective

每天从你指定的新闻源抓标题，把讲同一件事的报道归成一组，**数有几家独立媒体在报**。
达到门槛的写成 `inbox/headlines/` 里的一个 `headline` 条目。目标是大事不漏，不是读全部新闻。

成功的样子：运行 `eia headlines run`，几秒后 `inbox/headlines/` 出现当天的大事，每条列出哪几家媒体报了、各自的链接。
同一件事第二天还在发酵，更新原来那个文件，不新建。

## 2. 2026-11 复核到的现实情况

| 观察 | 对设计的影响 |
|---|---|
| 路透社、美联社已经没有公开 RSS（路透 2020 年停更） | 它们的消息会通过转载它们的媒体进来，不单独抓 |
| BBC、半岛电视台等仍有官方 RSS，但 CNN 的 RSS 2024 年后不再更新 | 新闻源由你在配置里列；`eia headlines check` 报出哪个源坏了、多久没更新 |
| 很多中文源只有 RSSHub 这类第三方转换 | 能用，但算"非官方"，见第 8 节第 1 条 |
| 新闻 RSS 只给标题、摘要、链接，不给全文 | 只用标题 + 摘要聚类，不抓网页全文 |

## 3. 配置（config.yaml）

```yaml
headlines:
  feeds:
    - url: https://feeds.bbci.co.uk/news/rss.xml
      publisher: BBC            # 同一个 publisher 的多个源只算一家
    - url: https://www.aljazeera.com/xml/rss/all.xml
      publisher: Al Jazeera
  min_sources: 3                # 至少几家独立媒体在报才算大事
  window_hours: 36              # 多长时间内的报道放在一起比较
  cluster: embed                # embed（默认，用 llm.embed，能跨语言）| lexical（不需要向量模型）
  similarity: 0.68              # 达到多少算同一件事；embed 默认 0.68、lexical 默认 0.35（2026-11-21 用真实数据校准）
```

- 没有 `headlines` 配置时报错退出，不偷偷用任何默认源。
- 抓取只访问列出的地址，User-Agent 用通用名称，不带个人信息。

## 4. 流程

1. **抓取**：逐个源下载（超时 20 秒），一个源失败只记一条警告，其他照常。
2. **暂存**：标题、摘要、链接、发布时间、来源追加到 `.eia/headlines/items.jsonl`（`.eia/` 已在 `.gitignore`，不进 Git）。按链接去重；超过 `window_hours` 两倍的旧记录清掉。
3. **聚类**：在 `window_hours` 内的报道里，把讲同一件事的归成一组。
   - `lexical`：标题 + 摘要切成词（中文按字的二元组，英文按词），相似度超过阈值就归一组。不联网、不花钱。**中英文报道同一件事时分不到一组**。
   - `embed`：用 `llm.embed` 算向量再按相似度分组，能跨语言。需要配置向量模型（例如本地 Ollama，不花钱、数据不出电脑）。
4. **计数**：一组里不同 `publisher` 的数量就是 `independent_count`。
5. **写入**：`independent_count ≥ min_sources` 的组：
   - 新的组 → 新建 `headline` 条目；
   - 已有条目的组（按 `cluster_id` 找）→ 只更新 `sources`、`independent_count`、`updated`，不动你的 tags 和笔记；
   - 来源数后来掉到门槛以下 → 不删文件。

## 5. 生成的文件

位置：`inbox/headlines/YYYY-MM-DD-<标题>-<后缀>.md`

```markdown
---
schema: 1
id: headline-20261121-k3f9qa
type: headline
title: 代表性标题（取最早发布那家的标题）
created: 2026-11-21T02:10:00Z
updated: 2026-11-21T09:40:00Z
status: inbox
tags: []
lang: zh
sources:
  - publisher: BBC
    url: https://...
  - publisher: Al Jazeera
    url: https://...
independent_count: 3
cluster_id: c-20261121-8d1e2f
---

<!-- eia:headline:start 这一段由 eia headlines 生成，重新运行时会被替换 -->

- **BBC** · 11-21 10:10 · 原标题
- **Al Jazeera** · 11-21 11:02 · 原标题

<!-- eia:headline:end -->
```

## 6. 命令

```
eia headlines run   [--vault <路径>] [--dry-run]   # 抓取 → 聚类 → 写入，结束时报告新建 / 更新 / 失败的源
eia headlines check [--vault <路径>]               # 逐个源测试能否访问、最新一条是什么时候
```

定时运行属于 V1 的「调度」模块，这里只做手动命令。

## 7. 测试编号（批准后登记到 TEST-PLAN.md，F11）

| 编号 | 验收标准 |
|---|---|
| F11-01 | 没有 `headlines` 配置或源列表为空时报错退出，不访问网络 |
| F11-02 | RSS 2.0 和 Atom 都能解析；坏的 XML、超时、HTTP 错误只影响那一个源，并报出来 |
| F11-03 | 同一事件不同措辞的标题归为一组；无关事件不混在一起（固定样例集） |
| F11-04 | 同一 `publisher` 的多个源只算一家 |
| F11-05 | 达到门槛才写文件；再次运行时更新同一文件，保留用户的 tags 和标记外的笔记 |
| F11-06 | 生成的文件通过 `eia validate` |
| F11-07 | `--dry-run` 不写 vault，也不写 `.eia/` |
| F11-08 | `cluster: embed` 时用 `llm.embed`；没配置向量模型时说明原因并退出 |

测试全部用本地样例 XML，不连真实网站。

## 8. 作者的决定（2026-11-21）

1. **推荐源**：挑 8–10 个有官方 RSS 的中英文主流媒体，实现时逐个实测，以**注释**写进 `eia init` 的配置模板。RSSHub 等第三方转换源不列入推荐，只在文档里说明可以自己加。
2. **门槛**：`min_sources` 默认 3。
3. **聚类默认 `embed`**：作者本机已有 Ollama，推荐向量模型 `bge-m3`（约 1.2 GB，多语言，中文好），配置在 `llm.embed`。
   - 没有配置 `llm.embed`、或者 Ollama 没开时，报出原因并退出，**不悄悄改用 lexical**。`lexical` 只在配置里明确写了才用。
   - 向量缓存在 `.eia/headlines/` 里，记录模型名和维度；模型或维度变了，旧向量全部作废重算（SPEC-llm-adapter「给下游的注意」）。
4. **AI 中文标题**：V1 不做，挪到 V2。
5. **RSS 解析**：用 `feedparser`，版本写死，放在可选依赖 `[news]`。