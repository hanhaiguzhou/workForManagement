"""会话与 CSRF 的底层原语。

约束：token 只在 Set-Cookie 与请求头里出现，库里存 sha256；
任何日志、异常信息都不得包含 token 或密码。
"""

from __future__ import annotations

import hashlib
import secrets

COOKIE_NAME = "duty_session"
CSRF_FIELD = "csrf"


def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_csrf() -> str:
    return secrets.token_urlsafe(24)


def redact(text: str) -> str:
    """粗粒度脱敏，供日志使用（不追求完美，只求不泄露）。"""
    out = text
    for marker in ("password", "passwd", "pwd", "cookie", "token"):
        idx = out.lower().find(marker)
        if idx >= 0:
            out = out[:idx] + f"{marker}=***"
    return out
