"""
HTTP / OpenAI 可重试错误上的指数退避（2s / 4s / 8s，最多 4 次尝试 = 首击 + 3 次重试）。
不引入第三方依赖，供 transcriber / llm_judge 共用。
仓库发版 V7.2（与 build_release.CURRENT_VERSION 对齐）。
"""
from __future__ import annotations

import logging
import time
from typing import Callable, TypeVar

import requests
from openai import APIError, RateLimitError

T = TypeVar("T")

BACKOFF_SECS = (2.0, 4.0, 8.0)
MAX_ATTEMPTS = 1 + len(BACKOFF_SECS)


def is_retryable_exception(exc: BaseException) -> bool:
    if isinstance(exc, RateLimitError):
        return True
    if isinstance(exc, APIError):
        code = getattr(exc, "status_code", None)
        if code is not None and int(code) in (429, 502, 503, 504):
            return True
    if isinstance(exc, requests.exceptions.HTTPError) and exc.response is not None:
        try:
            return int(exc.response.status_code) in (429, 502, 503, 504)
        except (TypeError, ValueError):
            return False
    if isinstance(
        exc,
        (
            requests.exceptions.Timeout,
            requests.exceptions.ConnectionError,
        ),
    ):
        return True
    return isinstance(exc, (TimeoutError, ConnectionError, OSError))


def run_with_backoff(
    fn: Callable[[], T],
    *,
    logger: logging.Logger | None = None,
    operation: str = "call",
) -> T:
    log = logger or logging.getLogger(__name__)
    for attempt in range(MAX_ATTEMPTS):
        try:
            return fn()
        except Exception as e:
            if attempt == MAX_ATTEMPTS - 1 or not is_retryable_exception(e):
                raise
            delay = BACKOFF_SECS[attempt]
            log.warning(
                "%s 可重试错误 (第 %d/%d 次)，%.0fs 后重试: %s",
                operation,
                attempt + 1,
                MAX_ATTEMPTS,
                delay,
                e,
            )
            time.sleep(delay)
    raise RuntimeError("run_with_backoff: unreachable")
