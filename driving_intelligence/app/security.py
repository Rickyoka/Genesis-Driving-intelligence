"""CSRF protection, security headers and input validation helpers."""
from __future__ import annotations

import hmac
import secrets

from urllib.parse import urlparse

from flask import Flask, abort, current_app, request, session

CSRF_SESSION_KEY = "_csrf_token"
CSRF_FORM_FIELD = "csrf_token"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

CSP_TEMPLATE = ("default-src 'self'; img-src 'self' data:{tiles}; style-src 'self'; script-src 'self'; "
                "object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'")


def build_csp(tile_url: str | None) -> str:
    """Allow map tiles only from the configured https origin (nothing else external)."""
    origin = ""
    if tile_url:
        u = urlparse(tile_url)
        if u.scheme == "https" and u.netloc and all(ch.isalnum() or ch in ".-:" for ch in u.netloc):
            origin = f" https://{u.netloc}"
    return CSP_TEMPLATE.format(tiles=origin)


def csrf_token() -> str:
    token = session.get(CSRF_SESSION_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        session[CSRF_SESSION_KEY] = token
    return token


def _verify_csrf() -> None:
    if request.method in SAFE_METHODS:
        return
    expected = session.get(CSRF_SESSION_KEY)
    sent = request.form.get(CSRF_FORM_FIELD, "")
    if not expected or not sent or not hmac.compare_digest(expected, sent):
        abort(400, description="보안 토큰이 만료되었거나 올바르지 않습니다. 페이지를 새로고침한 뒤 다시 시도하세요.")


def _set_headers(response):
    h = response.headers
    h.setdefault("Content-Security-Policy", current_app.config["CSP"])
    h.setdefault("X-Content-Type-Options", "nosniff")
    h.setdefault("X-Frame-Options", "DENY")
    h.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    h.setdefault("Permissions-Policy", "geolocation=(self), camera=(), microphone=()")  # self: stamp check-in
    if request.is_secure:
        h.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    if response.mimetype == "text/html":
        h.setdefault("Cache-Control", "no-store")
    return response


def init_security(app: Flask) -> None:
    app.config["CSP"] = build_csp(app.config.get("MAP_TILE_URL"))
    app.before_request(_verify_csrf)
    app.after_request(_set_headers)
    app.jinja_env.globals["csrf_token"] = csrf_token


# ----------------------------------------------------------------- validation helpers
class FormError(ValueError):
    """Validation error with a user-safe message."""


def clean_text(value: str | None, label: str, max_len: int, required: bool = True) -> str:
    text = (value or "").strip()
    if required and not text:
        raise FormError(f"{label}을(를) 입력하세요.")
    if len(text) > max_len:
        raise FormError(f"{label}은(는) {max_len}자 이하로 입력하세요.")
    if any(ord(ch) < 32 for ch in text):
        raise FormError(f"{label}에 허용되지 않는 문자가 있습니다.")
    return text


def clean_int(value: str | None, label: str, lo: int, hi: int) -> int:
    try:
        number = int(str(value or "").strip().replace(",", ""))
    except ValueError:
        raise FormError(f"{label}은(는) 숫자로 입력하세요.") from None
    if not lo <= number <= hi:
        raise FormError(f"{label}은(는) {lo:,}~{hi:,} 범위로 입력하세요.")
    return number


def clean_choice(value: str | None, label: str, choices) -> str:
    if value not in choices:
        raise FormError(f"{label} 값이 올바르지 않습니다.")
    return value  # type: ignore[return-value]
