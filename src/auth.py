from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import wraps
from typing import Any

import jwt
from flask import jsonify, request
from jwt import PyJWKClient


@dataclass(frozen=True)
class Principal:
    subject: str
    roles: frozenset[str]
    claims: dict[str, Any]


def _roles(claims: dict[str, Any]) -> frozenset[str]:
    values: list[str] = []
    for key in ("roles", "groups"):
        value = claims.get(key, [])
        values.extend(value if isinstance(value, list) else [value])
    realm_access = claims.get("realm_access", {})
    if isinstance(realm_access, dict):
        values.extend(realm_access.get("roles", []))
    return frozenset(str(value) for value in values)


def authenticate() -> Principal | None:
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        return None
    token = header[7:].strip()
    issuer = os.getenv("OIDC_ISSUER", "").strip() or None
    audience = os.getenv("OIDC_AUDIENCE", "").strip() or None
    try:
        if os.getenv("OIDC_JWKS_URL", "").strip():
            key = PyJWKClient(os.environ["OIDC_JWKS_URL"]).get_signing_key_from_jwt(token).key
            claims = jwt.decode(
                token,
                key,
                algorithms=["RS256", "RS384", "RS512"],
                issuer=issuer,
                audience=audience,
                options={"require": ["sub", "exp"]},
            )
        else:
            secret = os.getenv("JWT_SECRET", "").strip()
            if not secret:
                return None
            claims = jwt.decode(
                token,
                secret,
                algorithms=["HS256"],
                issuer=issuer,
                audience=audience,
                options={"require": ["sub", "exp"]},
            )
    except jwt.PyJWTError:
        return None
    return Principal(str(claims["sub"]), _roles(claims), claims)


def require_roles(*required: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    required_roles = frozenset(required)

    def decorator(view: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(view)
        def wrapped(*args: Any, **kwargs: Any) -> Any:
            principal = authenticate()
            if principal is None:
                return jsonify(error="authentication required"), 401
            if required_roles and not (principal.roles & required_roles):
                return jsonify(error="insufficient role"), 403
            return view(*args, **kwargs)

        return wrapped

    return decorator


def issue_local_token(username: str, password: str) -> str | None:
    if os.getenv("AUTH_LOCAL_ENABLED", "0") != "1":
        return None
    configured_user = os.getenv("AUTH_LOCAL_USERNAME", "").strip()
    configured_password = os.getenv("AUTH_LOCAL_PASSWORD", "")
    if not configured_user or not configured_password:
        return None
    if username != configured_user or password != configured_password:
        return None
    now = datetime.now(UTC)
    roles = [
        item.strip()
        for item in os.getenv("AUTH_LOCAL_ROLES", "operator").split(",")
        if item.strip()
    ]
    claims: dict[str, Any] = {
        "sub": username,
        "roles": roles,
        "iat": now,
        "exp": now + timedelta(hours=8),
    }
    audience = os.getenv("OIDC_AUDIENCE", "").strip()
    issuer = os.getenv("OIDC_ISSUER", "").strip()
    if audience:
        claims["aud"] = audience
    if issuer:
        claims["iss"] = issuer
    secret = os.getenv("JWT_SECRET", "").strip()
    return jwt.encode(claims, secret, algorithm="HS256") if secret else None
