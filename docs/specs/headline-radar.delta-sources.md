# Delta Spec: headline-radar 新闻源扩展

Status: 已批准 2026-11-21（第 10 节三条都按建议；Reddit 实现前复核后挪到 Backlog，见第 8 节）　版本：V1
改的是：`headline-radar.md` 第 3 节（配置）、第 4 节第 1 步（抓取）、第 8 节第 1 条（推荐源）；
顺带改 `interest-digest.md` 第 3 节（起名）。其他不变。

## 1. 为什么改

作者 2026-11-21 试用后的反馈：推送全是政治新闻，和自己关系不大。原因：

| 观察 | 影响 |
|---|---|
| 13 个推荐源全是国际新闻（BBC、卫报、半岛……） | 雷达里能凑够 3 家的几乎都是政治、外交、司法 |
| 按兴趣挑选只能从抓到的报道里挑 | 源里没有 AI、马来西亚用车、神学的报道，挑选做得再好也挑不出来 |
| 源是我替作者手挑的 | 换一个用户，兴趣不同，这份源列表就没用了 |
| 只支持 RSS | 知乎、微博、B站、Hacker News、Reddit 这些人们真正在看的地方都接不进来 |

## 2. 2026-11-21 在作者电脑上实测

| 来源 | 直接访问 | 结论 |
|---|---|---|
| Hacker News 官方接口 | ✅ | 接 |
| Google News 按关键词搜索（RSS） | ✅ | 接，并用于「按兴趣自动搜」 |
| Reddit：`old.reddit.com` 网页 | ⚠️ 只有冒充浏览器才给（Horizon 用 Mac 版 Chrome 的 User-Agent）；Python 正常请求 403 Blocked | **V1 不做**（第 8 节） |
| B站热门（公开接口） | ✅ | 接，直接访问 |
| 知乎、微博、抖音、头条 | ❌ 401 / 403 / 空内容 | 经 **NewsNow** 拿（✅ 4 个都通） |
| X、Facebook、Threads、Quora、TikTok、小红书 | ❌ 要登录、有签名和反爬虫，X 的接口收费 | **不做** |
| GDELT 全球新闻库 | ❌ 429 | 不做 |

NewsNow（开源，TrendRadar 也用它）替我们抓好、缓存好各平台热榜。eia 只读它的结果，**不自己破解签名、不伪造登录**。
它的公共服务器是别人的，可能挂掉或限流；地址可以换成自己架的。

Hacker News、Google News 两种的做法参考 Horizon（MIT，见 `THIRD_PARTY_NOTICES.md`，加上新拿的文件）。
Horizon 不支持中文平台，热榜部分是 eia 自己的。

## 3. 配置（替换 `headline-radar.md` 第 3 节的 feeds 部分）

```yaml
headlines:
  feeds:
    - url: https://feeds.bbci.co.uk/news/world/rss.xml   # 不写 type 就是 rss，旧配置不用改
      publisher: BBC
    - type: hackernews
      min_score: 100            # 只要 100 分以上的帖子（照 Horizon）
    - type: google_news
      query: Malaysia car insurance
      lang: en                  # 搜索语言
      country: MY               # 搜索地区
    - type: hotlist
      platform: zhihu           # zhihu / weibo / douyin / toutiao 经 NewsNow；bilibili 直接访问 B站
  newsnow: https://newsnow.busiyi.world   # 可选，换成自己架的 NewsNow 地址
  interest_search:              # 按兴趣自动搜 Google News（第 5 节）
    enabled: true
    lang: zh-CN
    country: MY
  min_sources: 3
  # window_hours / cluster / similarity 不变
```

## 4. 「几家媒体」怎么算（替换第 4 节第 4 步的说明）

雷达的门槛仍然是「至少 3 家独立来源」，各种源这样算：

| 源 | publisher 是 |
|---|---|
| rss | 配置里写的 `publisher`（不变） |
| google_news | **每篇报道里写的真实媒体名**（Google News 的 `<source>`），一次搜索能带来好几家 |
| hackernews | `Hacker News` |
| hotlist | 平台名：`知乎`、`微博`、`抖音`、`头条`、`B站` |

所以「微博、知乎、抖音同时在热议」也能进雷达（作者 2026-11-21 选定：新源都参与雷达）。
热榜和 Hacker News 只有标题、没有摘要和发布时间，发布时间用第一次抓到的时间。

## 5. 按兴趣自动搜

- `interest_search.enabled: true` 时，`config.yaml` 里每条兴趣自动变成一个 `google_news` 源，publisher 按第 4 节取真实媒体名。
- 搜什么：兴趣的新字段 `search`（1–3 个关键词，起名时由 AI 给出，用户可改）；没有 `search` 就用兴趣名。
- 这样每个用户的源都不同，不用任何人手配。
- **隐私**：兴趣关键词会发给 Google。所以只在配置里明确写了 `enabled: true` 才搜；`--dry-run` 和网站上列出将要搜的关键词。

## 6. 推荐源目录（替换第 8 节第 1 条）

- eia 自带一份按话题分组的源目录（`src/eia/headlines/catalog.yaml`）：国际、中文新闻、科技、AI、开发者、马来西亚、新加坡、基督教、中文热榜……
- 每一项实现时逐个实测，在目录里写上测试日期；`eia headlines check` 也能测。
- `eia headlines catalog` 列出目录；网站新增「源」页面，勾选即加入 `config.yaml`（用 `configedit`，先备份）。
- `eia init` 的模板改成：国际新闻 4 个 + Hacker News + 中文热榜 + `interest_search` 示例，其他从目录挑。

## 7. 起名提示词的修改（`interest-digest.md` 第 3 节）

作者的草稿里一半是作业和生活琐事（卡诺图、Java 作业、Subway 份量），不适合拿来挑新闻。改成：

- AI 给每组起名时，同时给出 `search`（关键词）和 `news`（这个话题会不会有新闻报道：是 / 否）。
- `news: 否` 的组放进草稿最后的「不太适合挑新闻」一节，**默认不勾**；用户仍然可以勾上。

## 8. 不做的（进 Backlog）

- X / Facebook / Threads / Quora / TikTok / 小红书：要登录或付费，抓取违反平台条款。
- Reddit：作者先同意接（第 10 节第 3 条），实现前复核发现只有冒充浏览器才能读到，与「不伪造」冲突。
  正路是 Reddit 官方接口（免费申请密钥，key 放 `.env`），进 Backlog。
- GitHub 动态（Horizon 有）：等有用户需要再做。
- 抓网页全文、AI 给热榜写摘要：V2。

## 9. 测试编号（批准后登记到 `docs/TEST-PLAN.md`，F15）

测试全部用本地样例（虚构的 JSON / HTML / RSS），不连真实网站。

| 编号 | 验收标准 |
|---|---|
| F15-01 | 不写 `type` 的源仍按 rss 处理，旧配置不用改；`type` 写错、缺必填项时报出是第几项 |
| F15-02 | hackernews：解析官方接口返回，低于 `min_score` 的不要 |
| F15-03 | google_news：按 `query`、`lang`、`country` 拼出地址；publisher 取每篇报道的真实媒体名 |
| F15-04 | hotlist：解析 NewsNow 和 B站的返回；`newsnow` 地址可换；一个平台失败不影响其他源 |
| F15-05 | 第 4 节的计数规则：同一平台只算一家，Google News 按真实媒体算 |
| F15-06 | `interest_search`：每条兴趣变成一个搜索源，用 `search`，没有就用兴趣名；没开就不搜；运行时列出关键词 |
| F15-07 | 推荐源目录的每一项都是合法配置；`eia headlines catalog` 能列出；网站加源后 `config.yaml` 先备份、注释保留 |
| F15-08 | 起名结果带 `search` 和 `news`；`news: 否` 的组默认不勾 |
| F15-09 | `eia headlines check` 支持所有新类型 |

## 10. 作者的决定（2026-11-21：都按建议）

1. **兴趣关键词发给 Google**：我建议默认模板里写上 `enabled: true`，并在网站上显示会搜哪些词；
   也可以默认关闭，用户自己打开。
2. **NewsNow 公共服务器**：我建议模板里默认开知乎、微博、B站三个热榜，注明是第三方、可能断；
   也可以默认不开，只在目录里列出。
3. **Reddit**：我建议接（照 Horizon 读 `old.reddit.com` 网页，用标准库解析，不加新依赖），但它随时可能被 Reddit 封；
   也可以先不做。
