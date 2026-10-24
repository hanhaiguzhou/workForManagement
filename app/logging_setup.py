"""访问日志与脱敏。

闸门规则（都有测试守着）：

1. 密码只可能出现在一次请求的表单体里，日志**从不**记录请求体；
2. 会话 cookie、Set-Cookie 头、`password=` 之类的字段即使被别处写进日志，
   也会在落盘前被替换成 `***`；
3. 日志只记录 方法 / 路径 / 状态码 / 账号，不记录课表原文。
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

REDACTED = "***"

# duty_session=abc123 / Set-Cookie: duty_session=...
_COOKIE_VALUE = re.compile(r"(?i)(duty_session[\"']?\s*[:=]\s*)[A-Za-z0-9._\-]+")
_COOKIE_HEADER = re.compile(r"(?i)(cookie\s*:\s*)[^\r\n]+")
# password=xxx / "password": "xxx" / 密码：xxx
_PASSWORD_FIELD = re.compile(
    r"(?i)((?:password|passwd|pwd|密码|口令)[\"']?\s*[:=：]\s*)[^\s,&}\]]+"
)
# 形如 64 位 hex 的 token / hash
_LONG_TOKEN = re.compile(r"\b[0-9a-f]{32,}\b")


def redact(text: str) -> str:
    if not text:
        return text
    text = _COOKIE_HEADER.sub(rf"\1{REDACTED}", text)
    text = _COOKIE_VALUE.sub(rf"\1{REDACTED}", text)
    text = _PASSWORD_FIELD.sub(rf"\1{REDACTED}", text)
    text = _LONG_TOKEN.sub(REDACTED, text)
    return text


class RedactingFilter(logging.Filter):
    """在任何 handler 之前把消息里的敏感片段洗掉。"""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003 - logging API
        try:
            message = record.getMessage()
        except Exception:  # 格式化失败也不能让日志炸掉请求
            return True
        record.msg = redact(message)
        record.args = ()
        return True


def configure(log_path: Optional[Path] = None, level: int = logging.INFO) -> logging.Logger:
    """配置应用日志。重复调用不会重复加 handler。"""
    logger = logging.getLogger("app")
    logger.setLevel(level)
    logger.propagate = False

    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()

    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    redactor = RedactingFilter()

    if log_path is not None:
        path = Path(log_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        handler: logging.Handler = logging.FileHandler(path, encoding="utf-8")
    else:
        handler = logging.StreamHandler()

    handler.setFormatter(formatter)
    handler.addFilter(redactor)
    logger.addHandler(handler)
    return logger
