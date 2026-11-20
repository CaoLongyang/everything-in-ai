# Delta Spec: llm-adapter V1（开工前复核）

Status: 已批准 2026-11-20（第 6 节第 1 条选 A：两家官方 SDK，固定为 `anthropic==2.6.0`、`openai==3.46.0`，均发布满两周）　版本：V1
改动对象：`SPEC-llm-adapter.md`（已批准 2026-11-16，注明"V1 开工前按当时情况复核"）
复核日期：2026-11-20

原 spec 的目标、接口（`complete` / `complete_json` / `embed`）、错误类型、测试策略、边界都不变。只改下面几处。

## 1. 底层不再用 LiteLLM（**需要作者决定**，见第 6 节第 1 条）

原 spec 假设 1 选了 LiteLLM。复核时发现：

- 2026-03-24，LiteLLM 在 PyPI 上的 1.82.7、1.82.8 两个版本被植入了窃取凭据的代码（攻击者先攻破了它发布流程里用到的另一个工具，拿到了发布令牌）。恶意代码专门偷云服务凭据、SSH 密钥等，下架前被下载了数万次。
- 本模块的核心就是保管用户的 API key。一个体量很大、依赖很多、出过这种事的中间层，放在 key 和厂商之间，风险不划算。
- 本项目只需要三个能力（文本生成、JSON 输出、向量），用不到 LiteLLM 覆盖上百家厂商的大部分功能。

推荐的替代方案（A）：只用两家**官方** SDK，自己写一层很薄的转换：

| `provider` | 底层 | 能接的服务 |
|---|---|---|
| `anthropic` | Anthropic 官方 `anthropic` SDK | Claude |
| `openai_compatible` | OpenAI 官方 `openai` SDK + `api_base` | OpenAI，以及提供 OpenAI 兼容接口的服务：DeepSeek、通义千问、Kimi、智谱、本地 Ollama 等 |

- 两个 SDK 放在可选依赖 `pip install -e ".[llm]"` 里，**版本写死**（`==`），升级是有意识的动作；不用 AI 功能的人不需要安装。
- CI 的 osv-scanner 会扫这两个包。
- 原 Success Criteria 4"除 client.py 外没有 `import litellm`"改为：除 `eia/llm/` 下的两个后端文件外，没有 `import anthropic` / `import openai`（测试强制）。

## 2. 配置格式

```yaml
llm:
  provider: anthropic              # anthropic | openai_compatible
  model: claude-opus-5-5           # 该服务里的模型名，原样传过去
  api_base:                        # openai_compatible 时填，如 https://api.deepseek.com
  api_key_env: ANTHROPIC_API_KEY   # key 放在哪个环境变量里（只写变量名，不写 key）
  embed:                           # 可选；不需要向量的模块不用配
    provider: openai_compatible
    model: nomic-embed-text
    api_base: http://127.0.0.1:11434/v1   # 本地 Ollama
    api_key_env:                   # 本地服务可以留空
  max_retries: 3
  timeout_s: 60
```

- 原 spec 用 LiteLLM 的 `provider/model` 写法和它的环境变量约定；改成显式写 `provider` 和 `api_key_env`，换服务时一眼能看出数据发给谁。
- 原示例里的模型名 `claude-sonnet-4-5` 已过时，示例改为当前的 `claude-opus-5-5`。这只是示例，不是默认值：**没配置时依然报错，不偷偷用任何服务**（原 Boundaries 不变）。
- 向量和文本分开配，因为 Claude 不提供向量模型；本地 Ollama 是不花钱、不出电脑的默认建议。

## 3. 各后端要处理的平台差异（实现细节，不影响接口）

- `anthropic`：`complete_json` 用平台原生的结构化输出；模型以 `refusal` 停止时抛 `LLMOutputError`，不当成正常输出。
- `openai_compatible`：不是所有兼容服务都支持 JSON 模式，按原 spec 退化为"prompt 要求 JSON + 解析校验 + 重试"。
- 两者的网络错误、限流统一成 `LLMCallError`，重试次数由 `max_retries` 控制。

## 4. 不变的部分

- 模块代码只 `from eia import llm`。
- 日志不打印 key 和完整 prompt，只打印长度。
- 测试全部模拟返回，不打真实 API、不花钱；`pytest -m live` 手动跑。
- 延后项（多模型分组、预算上限、缓存、自动切换）不变。

## 5. 对 TEST-PLAN.md 的修改（批准后生效）

新增「F9 llm-adapter」（F8 留给暂缓中的 Gemini 导入）：

| 编号 | 验收标准 |
|---|---|
| F9-01 | 没有 `llm` 配置、缺 `provider` / `model`、`api_key_env` 指向的环境变量不存在时，报出缺哪一项，退出码 1，不发任何请求 |
| F9-02 | 只改 `config.yaml`，`anthropic` 与 `openai_compatible`（含本地地址）两种后端都能走通 `complete` 和 `embed`（模拟返回） |
| F9-03 | `complete_json` 返回校验过的对象；输出不合法时重试，次数不超过 `max_retries`，最终抛 `LLMOutputError` |
| F9-04 | 网络错误、限流重试后抛 `LLMCallError`；模型拒绝回答抛 `LLMOutputError` |
| F9-05 | 日志、错误信息、`eia llm show` 里从不出现 key 本身和 prompt 全文 |
| F9-06 | 除 `eia/llm/` 的后端文件外，代码库没有 `import anthropic` / `import openai` |
| F9-07 | `eia llm test` 成功时报告延迟，失败时说明原因，退出码 1 |

## 6. 需要作者决定

1. **底层用什么**：
   - A（推荐）：换成两家官方 SDK（第 1 节）。
   - B：保留 LiteLLM，版本写死在一个干净的版本上。
2. 其余改动（配置格式、示例模型名、F9 编号）是复核时的技术调整，随第 1 条一起批准即可。
