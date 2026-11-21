# 第三方代码声明

本项目的部分代码复制或改写自下列开源项目，按其许可证要求保留原版权声明。

## Horizon

- 项目：https://github.com/Thysrael/Horizon （参考的版本：提交 `12d5fbd`，2026-11-15）
- 用到的地方：
  - `src/eia/headlines/urls.py`：链接规范化（改写自 `src/orchestrator.py` 的 `_deduplication_url_key` 和跟踪参数列表）
  - `src/eia/interests/select.py`：按组限量的选取算法（改写自 `src/orchestrator.py` 的 `apply_balanced_digest`）
  - `src/eia/interests/prompts.py`：提示词里的安全规则（改写自 `src/ai/prompting/common.py`）
- 说明：见 `docs/specs/interest-digest.md` 第 10 节。

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
