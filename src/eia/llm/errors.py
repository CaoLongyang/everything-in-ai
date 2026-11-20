"""llm 模块对外的错误类型。调用方只需要认识这三个。"""

from __future__ import annotations


class LLMError(Exception):
    """所有 llm 错误的基类。"""


class LLMConfigError(LLMError):
    """没配置、配置不完整，或者 key 所在的环境变量不存在。不会发出任何请求。"""


class LLMCallError(LLMError):
    """网络错误、限流、服务端错误（已按 max_retries 重试），或认证等不可重试的错误。"""


class LLMOutputError(LLMError):
    """模型拒绝回答，或输出不合法（已按 max_retries 重试）。"""


class TransientError(LLMCallError):
    """后端内部使用：可以重试的错误。重试用完后以 LLMCallError 抛给调用方。"""
