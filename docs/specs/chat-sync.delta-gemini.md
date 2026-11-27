# Delta Spec: chat-sync（加上 Gemini）

Status: 已批准 2026-11-27（作者：「复用之前的」，即复用作者自己的 gemini-chat-exporter）　版本：V2
改动对象：`docs/specs/chat-sync.md` 第 2 节的 Gemini 一行、第 10 节「Gemini 这一版不做」

## 为什么现在能做

原规格不做 Gemini，是因为它的接口（`batchexecute`）返回没有字段名的嵌套数组，当时没找到能参考的代码。后来发现作者以前导出 Gemini 用的，就是作者自己写的 [gemini-chat-exporter](https://github.com/CaoLongyang/gemini-chat-exporter)（MIT），它已经走这个接口、实测过每个位置的含义（它的 `DEVELOPMENT.md` 第 13、14 节），vault 里现有的 Gemini 对话就是它导出的。

## 改动

| | 内容 |
|---|---|
| 对话列表 | `MaZiqc`，参数 `[20, 翻页标记, 分组]`，分组 `[0,null,1]` 和 `[1,null,1]` 都取；每项 `[c_id, 标题, _, _, _, [秒, 纳秒]]` |
| 一个对话 | `hNvQHb`，参数 `[c_id, 1000, 翻页标记, 1, [1], [4], null, 1]`；按轮编号去重、按时间排序 |
| 每一轮 | `[2][0][0]` 提问；`[3][0]` 回答候选，取 `[3][3]` 选中的那个，没有就取第一个；`[4]` 时间，提问和回答共用 |
| 令牌 | 网页自己的 `WIZ_global_data`（`SNlM0e`、`cfb2h`、`FdrFJe`），所以 Gemini 的请求在网页的**主环境**里运行（ChatGPT、Claude 在插件的隔离环境里）；只在网页里用，不存、不外传 |
| 谁来拼 | 和另外两家一样：插件把每一轮原样发给 eia，eia 拼成和 gemini-chat-exporter 导出一样的结构，再交给 `chat/gemini.py` 的解析（空消息不导入等规则相同） |
| 多个 Google 账号 | 网址是 `/u/1/app/…`。插件用你开着的 Gemini 标签页；没开时用上次同步成功的那个账号的地址；第一次没开时用 `/app`（默认账号）。多账号时先打开要同步的那个账号的 Gemini |
| 和以前的导出 | 对话编号（`c_…`）、网址、时间都和 gemini-chat-exporter 导出的一样，所以是同一个文件，不重复 |

限速、出错、增量（由 eia 和 vault 比）都和 ChatGPT、Claude 相同。

## 验收（登记到 docs/TEST-PLAN.md）

| 编号 | 验收标准 |
|---|---|
| F19-11 | Gemini 的原始轮转成合规的 chat 文件：按时间排序、只取选中的回答、同一轮共用时间；和 gemini-chat-exporter 导出的是同一个文件；没有时间、格式认不出的拒绝、不写文件 |
| F19-12 | 插件取 Gemini：列表按翻页标记取完、两个分组都取、重复的去掉；对话按翻页取完、按轮去重；网址带账号（`/u/1`）；请求在网页主环境里运行 |
