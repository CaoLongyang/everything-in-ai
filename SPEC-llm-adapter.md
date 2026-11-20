# Spec: llm-adapter

版本：V1（尚未开始实现）
Status: 已批准 2026-11-16；V1 开工前按当时情况复核

## Objective

为所有需要 AI 的模块提供唯一入口，让用户自由选择任何 LLM provider（含本地模型），模块代码不出现任何厂商名。
同时定义 `config.yaml` 中 LLM 部分的格式——它也是 `config-sync` 生成 Horizon / Khoj 配置的唯一来源。

成功的样子：用户把 `provider` 从 `anthropic` 改成 `ollama`，不改一行代码，所有模块照常工作。

## ASSUMPTIONS

1. 底层用 LiteLLM（开源，统一 OpenAI 风格接口，覆盖主流厂商与 Ollama）。
2. V1 只需要两种能力：`complete`（文本生成，可要求 JSON 输出）和 `embed`（向量）。
3. V1 只配一个文本模型 + 一个 embedding 模型；按用途分组（profiles）留到后期。
4. API key 只从环境变量或 `.env` 读，绝不写进 `config.yaml`（vault 会进 Git）。
5. 不做流式输出（批处理场景用不到）。

## Commands

```
测试连通:   eia llm test                         # 发一个极短请求，报告延迟和是否成功
查看配置:   eia llm show                         # 打印解析后的配置，key 只显示是否存在
测试:       pytest -q tests/llm
```

## 配置格式（config.yaml 片段）

```yaml
llm:
  model: anthropic/claude-sonnet-4-5      # 任意 LiteLLM 支持的 provider/model
  embed_model: ollama/nomic-embed-text    # 可选；不需要向量的模块不用配
  api_base:                               # 可选，本地模型 / 代理时填写
  max_retries: 3
  timeout_s: 60
```

- `model` 采用 LiteLLM 的 `provider/model` 写法。
- key 对应环境变量遵循 LiteLLM 约定（`ANTHROPIC_API_KEY`、`OPENAI_API_KEY`、`DEEPSEEK_API_KEY`…）；`eia llm show` 会提示缺哪个。

## Interface

```python
# src/eia/llm/__init__.py
def complete(prompt: str, *, system: str | None = None) -> str: ...

def complete_json(prompt: str, schema: type[T], *, system: str | None = None) -> T: ...
# provider 不支持原生 JSON 模式时，退化为"prompt 要求 JSON + 解析校验"。

def embed(texts: list[str]) -> list[list[float]]: ...
```

- `complete_json` 返回已校验的 pydantic 对象；模型输出不合法则重试，最终失败抛 `LLMOutputError`。
- 错误类型统一：`LLMConfigError`（没配 / 缺 key）、`LLMCallError`（网络、限流，已重试）、`LLMOutputError`。

## Project Structure

```
src/eia/llm/
├── __init__.py     # complete / embed 公共接口
├── config.py       # 解析 config.yaml 的 llm 段
├── client.py       # LiteLLM 封装、重试、超时
└── errors.py
tests/llm/
```

## Code Style

- 模块代码只 `from eia import llm`，禁止直接 import litellm 或任何厂商 SDK（ruff 规则或测试中检查）。
- 其余同 vault-schema。

## Testing Strategy

- 单元测试全部 mock LiteLLM，不打真实 API：配置解析、重试、JSON 校验重试、缺 key 报错。
- 一个可选的集成测试 `pytest -m live`，用户有 key 时手动跑。
- 覆盖率目标 ≥ 90%。

## Boundaries

- Always：错误信息说明缺哪个配置项或环境变量；日志里不打印 key 和完整 prompt（只打印长度）。
- Ask first：增加新能力（如图像、工具调用）；更换 LiteLLM。
- Never：把 key 写入任何文件；未配置时偷偷回退到某个厂商；把用户数据发给未在 config 中声明的服务。

## Success Criteria

1. 只改 `config.yaml` 中的 `model`，在 mock 下三种 provider（anthropic / openai / ollama）都能走通 `complete` 和 `embed`。
2. 缺 key 时 `eia llm test` 给出明确提示并退出码 1。
3. `complete_json` 输出非法时自动重试，次数不超过 `max_retries`。
4. 代码库中除 `eia/llm/client.py` 外没有 `import litellm`（测试强制）。

## Out of Scope (Deferred)

见 ROADMAP.md。本模块延后项：多模型分组（profiles）、每日预算上限（需先定义未知价格与时区规则）、结果缓存、多 provider 自动切换。

给下游的注意：向量与 `embed_model` 强绑定。存向量的模块必须同时记录模型名和维度，模型变化时强制重建索引（V1 headline-radar 实现）。

## Open Questions

无（V1 范围已收窄）。
