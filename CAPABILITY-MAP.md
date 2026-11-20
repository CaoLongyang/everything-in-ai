# Capability Map: everything-in-ai

> 模块怎么划分、彼此依赖什么。**每个模块在哪个版本做，以 ROADMAP.md 为准**，本文不重复排期。
> Phase 0 于 2026-11-15 批准；2026-11-20 按后续决策修订。

## 不变的原则

1. **纯 Markdown 是唯一数据源。** 每个用户在自己电脑上拥有自己的 vault。不做服务器、多租户和账号系统。
2. **模块之间不直接调用**，只通过两个契约连接：`vault-schema`（数据格式）和 `llm-adapter`（AI 接口）。
3. **外部工具不 fork**，各配一个转换适配器，把输出转成合规的 vault 条目。
4. **LLM 不绑定厂商**：用户在 `config.yaml` 里选模型，key 放环境变量，只填一次。外部工具（Horizon、Khoj）自带的 LLM 调用不在 `llm-adapter` 的保证范围内，只通过 `config-sync` 共享模型选择。
5. **原始数据只增不改**：AI 生成的内容（画像、摘要）与原始材料分开存放，并能指回来源。
6. 自研部分用 Python 3.11+；中英文都要支持。

## 模块

| 模块 | 职责 | 依赖 |
|---|---|---|
| `vault-schema` | 目录结构、frontmatter 规范、`eia init` / `validate` / `new` | — |
| `chat-import` | 各平台的对话导出 → `chat` 条目；重复导入只更新、保留用户的标签和笔记 | vault-schema |
| `llm-adapter` | 统一的 `complete` / `embed` 接口，底层是 Anthropic、OpenAI 两家官方 SDK（2026-11-20 改，见 SPEC-DELTA-llm-adapter-v1） | — |
| `chat-reader` | 按说话方读取对话，可以只取用户本人的话 | vault-schema |
| `config-sync` | `eia sync-config`：由 `config.yaml` 生成外部工具的模型配置 | llm-adapter |
| `headline-radar` | 大事通道：抓取 → 聚类 → 独立来源计数 | vault-schema、llm-adapter |
| `feed-digest` | 兴趣通道：Horizon 输出 → `digest` 条目 | vault-schema |
| `web-clip` | 网页剪藏模板对齐 schema | vault-schema |
| `vault-site` | `eia site`：本机私密网站（Quartz，只绑定 127.0.0.1） | vault-schema |
| `knowledge-index` | Khoj 索引 vault，问答与定时研究 | vault-schema、llm-adapter |
| `takeout-import` / `activity-import` | Google Takeout、TikTok、B 站等观看与搜索记录 | vault-schema |
| `chat-sync` | 浏览器插件在聊天时实时记录 | vault-schema |
| `interest-bootstrap` | 由搜索、订阅、对话生成兴趣草稿，用户确认后生效 | chat-reader、llm-adapter |
| `profile` | 个人画像，独立目录，每条结论带证据 id | chat-reader、llm-adapter |

```
                 ┌───────────── vault-schema（数据契约）─────────────┐
   导入：chat-import  takeout-import  activity-import  chat-sync  web-clip
   输入：headline-radar  feed-digest
   浏览：vault-site
   读取：chat-reader
   分析：knowledge-index  interest-bootstrap  profile
                 └───────────── llm-adapter（AI 契约）───────────────┘
```

## 明确不做

见 ROADMAP.md 的"明确不做"一节（微信、凭登录凭据读取抖音、模拟登录调用平台接口）。
