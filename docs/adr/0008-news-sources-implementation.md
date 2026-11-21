# ADR 0008：新闻源扩展实现中的决定

日期：2026-11-21　状态：已采纳
相关：`docs/specs/headline-radar.delta-sources.md`

1. **Reddit 不做（V1）**。作者同意接之后实测：Python 正常请求 `old.reddit.com` 返回 403 Blocked；
   Horizon 能读到，是因为它把 User-Agent 写成 Mac 版 Chrome。冒充浏览器绕过封锁和我们对中文平台定的「不伪造」冲突，
   所以挪到 Backlog，正路是 Reddit 官方接口（用户申请免费密钥）。
2. **User-Agent 改成 `Mozilla/5.0 (compatible; eia-headlines/1)`**（原来是 `eia-headlines/1`）。
   NewsNow 拒绝不以 `Mozilla/5.0` 开头的请求（实测 403）。`compatible; 名字` 是爬虫自报身份的通行写法（Googlebot 也这么写），
   不冒充浏览器，也不带任何个人信息。改完后 13 个国际新闻源仍全部可用。
3. **Hacker News 用 Algolia 的接口**（`hn.algolia.com/api/v1/search?tags=front_page`），不用 Horizon 用的 Firebase 官方接口：
   一次请求就拿到标题、链接、分数、时间；Firebase 要先取 ID 列表再逐条请求（30 条就是 31 次）。
4. **抓取接口不变**：每个源仍是「一个地址 → 一段字节」，`fetcher(url)` 照旧，测试和定时任务都不用改。
   `Feed` 加了 `kind`、`min_score`、`label`、`query` 四个字段，`Feed(url, publisher)` 的老写法照旧。
5. **按兴趣搜的语言**：关键词里有中文就用配置的 `lang`（默认 zh-CN），否则用英文。
   英文关键词配中文界面，Google 只返回很少的中文媒体报道。
6. **Google News 搜索加 `when:Nd`**（N = 时间窗口换算成天，向上取整），只要近几天的报道；每个源最多取 30 条，
   热榜、Hacker News 也是 30 条，免得一次搜索带进几百篇、本机向量模型算很久。
7. **微博热搜链接去掉 `band_rank` 等参数，只留搜索词**：排名每次都变，不去掉的话同一条热搜每次都算新报道。
8. **Google News 的链接是 Google 的跳转地址**，和同一篇报道在 RSS 里的原链接对不上，所以同一篇文章可能既从 RSS、
   又从搜索进来。媒体名也可能写法不同（「BBC」和「BBC News」），会被算成两家。暂不处理，先看实际影响。
9. **网站保存配置时各类源原样保存**，只留认识的键（type、publisher、url、min_score、query、lang、country、platform）；
   `newsnow` 地址表单里没有，保存时从原配置保留。
10. 推荐源目录 48 个，2026-11-21 逐个实测。没收进来的：The Star（404）、星洲日报（403）、联合早报（404）——
    马来西亚和新加坡的中文媒体暂时没有能用的 RSS，靠按兴趣搜 Google News 补。
