# 第三方代码声明

本项目的部分代码复制或改写自下列开源项目，按其许可证要求保留原版权声明。

## Horizon

- 项目：https://github.com/Thysrael/Horizon （参考的版本：提交 `12d5fbd`，2026-11-15）
- 用到的地方：
  - `src/eia/headlines/urls.py`：链接规范化（改写自 `src/orchestrator.py` 的 `_deduplication_url_key` 和跟踪参数列表）
  - `src/eia/interests/select.py`：按组限量的选取算法（改写自 `src/orchestrator.py` 的 `apply_balanced_digest`）
  - `src/eia/interests/prompts.py`：提示词里的安全规则（改写自 `src/ai/prompting/common.py`）
  - `src/eia/headlines/feeds.py`、`src/eia/headlines/config.py`：Google News 搜索地址的拼法（hl / gl / ceid）、
    取每篇报道的真实媒体名，Hacker News 按分数过滤（参考 `src/scrapers/google_news.py`、`src/scrapers/hackernews.py`）
- 说明：见 `docs/specs/interest-digest.md` 第 10 节、`docs/specs/headline-radar.delta-sources.md` 第 2 节。

```
MIT License

Copyright (c) 2026 Thysrael

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## chatgpt-exporter

- 项目：https://github.com/pionxzh/chatgpt-exporter （参考的版本：提交 `9364915`，2026-11-27）
- 用到的地方：`extension/lib/sites.js`：ChatGPT 对话列表和单个对话的接口地址、分页方式、先向 `/api/auth/session` 取临时令牌再带上的写法（改写自 `src/api.ts`、`src/constants.ts`）
- 说明：见 `docs/specs/chat-sync.md` 第 2 节、`docs/adr/0010-chat-sync-site-api.md`。

```
MIT License

Copyright (c) 2022-Present Pionxzh

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## claude-exporter

- 项目：https://github.com/agoramachina/claude-exporter （参考的版本：提交 `d5adf03`，2026-11-27）
- 用到的地方：`extension/lib/sites.js`：Claude 取组织编号（挑带 `chat` 能力的）、对话列表和单个对话的接口地址与参数（改写自 `chrome/content.js`）
- 说明：见 `docs/specs/chat-sync.md` 第 2 节、`docs/adr/0010-chat-sync-site-api.md`。

```
MIT License

Copyright 2025 agoramachina

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## gemini-chat-exporter

- 项目：https://github.com/CaoLongyang/gemini-chat-exporter （本项目作者自己的另一个项目，MIT；参考的是 2026-11-25 的版本）
- 用到的地方：`extension/lib/sites.js` 的 Gemini 部分（`batchexecute` 的请求写法、`MaZiqc` / `hNvQHb` 的参数和翻页，改写自 `gemini-export.js` 的 `rpc()`、`parseBatch()`、`listAll()`、`readConversation()`）；`src/eia/sync/convert.py` 的 `gemini_record`（每一轮各位置的含义，改写自 `turnToMessages()`、`buildRecord()`）
- 说明：见 `docs/specs/chat-sync.delta-gemini.md`。

```
MIT License

Copyright (c) 2026 CaoLongyang
```
