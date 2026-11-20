# Constraints（交付边界与质量底线）

Last reviewed: 2026-11-15
适用阶段：V0 雏形起。数值采用 constraint-driven-development 的默认值（雏形期不做访谈），V1 结束时按实测重新评估。

## Floor（始终强制，任何改动都不得违反）

- 不新增静默检查的注释：`# noqa`、`# type: ignore`、`pytest.mark.skip`（确需时在 commit message 写明原因）
- 不留未实现的桩：`raise NotImplementedError`、空的 `except: pass`
- 不删除或跳过已有测试来让 CI 变绿
- 源码、vault、日志中不出现任何 API key 或个人凭据
- 不为了让改动通过而放宽本文件

## 产品边界（V0–V1 不做的事）

- 不做服务器、多租户、账号系统；每个用户在自己机器上运行自己的 vault
- 不公开发布任何内容；私密网站只监听 `127.0.0.1`
- 不绑定任何 LLM 厂商；未配置时报错，绝不偷偷调用默认服务
- 不删除、不自动修改用户已有文件（导入时只新增；重复导入按 `conversation_id` 跳过或更新同一文件）
- 不执行 git push / commit（是否同步由用户决定）
- V0 不调用任何 LLM；之后凡是会把对话内容发给 LLM 的功能，必须由用户明确开启

## Enforced with numbers

| 维度 | 规则 | 检查命令 | 运行时机 |
|---|---|---|---|
| Lint / 格式 | 零错误 | `ruff check . && ruff format --check .` | 每次改动 |
| 类型 | 零错误（`src/` 下） | `mypy src` | 每次改动 |
| 测试 | 全部通过 | `pytest -q` | 每个任务结束、CI |
| 覆盖率 | 新增/修改代码行 ≥ 80% | `pytest --cov=eia --cov-report=term-missing` | 任务结束、CI |
| 密钥 | 零发现 | `gitleaks`（GitHub Actions 里的 gitleaks-action，扫全部提交历史） | CI，每次推送（2026-11-20 启用） |
| 依赖安全 | 零发现（比"无 high 及以上"更严：有任何已知漏洞都失败） | 安装后 `pip freeze` 出版本清单，`osv-scanner scan source -L` 扫描（项目没有锁文件） | CI，每次推送（2026-11-20 启用） |
| 跨平台 | macOS / Linux / Windows × Python 3.11 / 3.13 全通过 | GitHub Actions 跑 `python scripts/check.py`（`.github/workflows/ci.yml`） | CI，每次推送（2026-11-20 启用） |

失败处理：Floor 和测试 **阻断**；覆盖率、依赖安全在 V0 期间 **警告**，V1 起阻断。

## Measured, not yet enforced

| 指标 | 现状 | 方向 |
|---|---|---|
| 项目整体覆盖率 | 95%（2026-11-18，Windows；加入对话导入后） | 不得低于 95% |
| `eia validate` 处理 1 万个文件耗时 | 待测 | 不得变慢 |

## Exceptions

| ID | 规则 | 路径 | 原因 | 到期 |
|---|---|---|---|---|
| — | — | — | — | — |
