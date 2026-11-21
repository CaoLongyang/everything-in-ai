# ADR 0003：llm-adapter 实现中的决定

日期：2026-11-21　状态：已采纳
相关：`docs/specs/llm-adapter.md`、`docs/specs/llm-adapter.delta-v1.md`（方案 A：两家官方 SDK）

规格没写到、实现时定下的几件事：

1. **`openai_compatible` 必须写 `api_base`**，连 OpenAI 本身也要写 `https://api.openai.com/v1`。
   不写就会默认发给 OpenAI，这违反"不把数据发给未在 config 中声明的服务"。`anthropic` 的 `api_base` 可留空（官方地址）。
2. **重试只在 `client.py` 做一层**，两个 SDK 的 `max_retries` 都设为 0，免得次数叠加、超过 `max_retries`。
   可重试：连接失败 / 超时、HTTP 429、HTTP 5xx（含 Claude 的 529 过载）。其余（401、400 等）立即抛 `LLMCallError`。
   等待时间 1、2、4… 秒，最多 30 秒。`max_retries: N` 表示最多 N+1 次请求；JSON 不合法的重试另算一层。
3. **公共接口多一个可选参数 `vault`**（默认当前目录），因为配置在 vault 的 `config.yaml` 里。
   连续多次调用时用 `llm.get_client(vault)` 拿一个客户端复用。
4. **`.env` 自己解析**（`KEY=VALUE`，支持注释、引号、`export ` 前缀），不引入 python-dotenv。环境变量优先于 `.env`。
5. **厂商错误信息先脱敏再显示**：去掉 key、压成一行、截到 200 字。
6. **SDK 延迟导入**：没装 `[llm]` 时，eia 的其他命令照常可用；用到 AI 时提示 `pip install -e ".[llm]"`。
7. 单次输出上限固定为 4096 token（`_base.MAX_TOKENS`）。V1 的用途都是短输出；以后需要再做成配置。
8. `openai_compatible` 的 JSON 输出：在 prompt 末尾附上 JSON Schema，回复外面包了 ```json 代码块的也接受。
