from __future__ import annotations

import os
import time
import uuid
from collections.abc import Callable
from typing import Any

import jwt
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken, TokenVerifier
from pydantic import BaseModel, ConfigDict, Field, model_validator


ALL_CAPABILITIES = frozenset(
    {
        "read_manifest",
        "search_tender_evidence",
        "read_evidence_context",
        "compare_document_versions",
        "validate_evidence_refs",
    }
)


class ScopeTokenError(ValueError):
    pass


class TenderScope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str = Field(min_length=1, max_length=160)
    assessment_id: str = Field(min_length=1, max_length=160)
    agent_run_id: str = Field(min_length=1, max_length=160)
    subject: str = Field(min_length=1, max_length=200)
    allowed_tools: frozenset[str] = Field(min_length=1)
    issued_at: int = Field(ge=0)
    expires_at: int = Field(ge=1)
    audience: str = Field(min_length=1, max_length=500)
    issuer: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def validate_scope(self) -> "TenderScope":
        unknown = self.allowed_tools - ALL_CAPABILITIES
        if unknown:
            raise ValueError(f"unknown tender evidence capabilities: {sorted(unknown)}")
        if self.expires_at <= self.issued_at:
            raise ValueError("expires_at must be after issued_at")
        return self

    def allows(self, capability: str) -> bool:
        return capability in self.allowed_tools


class ScopedTokenCodec:
    """Issues and verifies short-lived internal service tokens.

    HS256 is deliberately limited to the local prototype. The codec boundary
    can later be replaced with an internal OIDC/JWKS verifier without changing
    the MCP tool contracts.
    """

    def __init__(
        self,
        *,
        secret: str,
        issuer: str,
        audience: str,
        now: Callable[[], float] = time.time,
    ):
        if len(secret) < 32:
            raise ValueError("TENDER_MCP_JWT_SECRET must contain at least 32 characters")
        if not issuer.strip() or not audience.strip():
            raise ValueError("issuer and audience are required")
        self._secret = secret
        self._issuer = issuer
        self._audience = audience
        self._now = now

    @property
    def issuer(self) -> str:
        return self._issuer

    @property
    def audience(self) -> str:
        return self._audience

    def issue(
        self,
        *,
        case_id: str,
        assessment_id: str,
        agent_run_id: str,
        subject: str,
        allowed_tools: set[str] | frozenset[str],
        ttl_seconds: int = 300,
    ) -> str:
        if ttl_seconds < 1 or ttl_seconds > 3600:
            raise ValueError("ttl_seconds must be between 1 and 3600")
        now = int(self._now())
        scope = TenderScope(
            case_id=case_id,
            assessment_id=assessment_id,
            agent_run_id=agent_run_id,
            subject=subject,
            allowed_tools=frozenset(allowed_tools),
            issued_at=now,
            expires_at=now + ttl_seconds,
            audience=self._audience,
            issuer=self._issuer,
        )
        payload: dict[str, Any] = {
            "iss": self._issuer,
            "aud": self._audience,
            "sub": subject,
            "iat": scope.issued_at,
            "exp": scope.expires_at,
            "jti": uuid.uuid4().hex,
            "client_id": "bid-intake-agent",
            "tender_scope": scope.model_dump(mode="json"),
        }
        return jwt.encode(payload, self._secret, algorithm="HS256")

    def verify(self, token: str) -> TenderScope:
        try:
            payload = jwt.decode(
                token,
                self._secret,
                algorithms=["HS256"],
                audience=self._audience,
                issuer=self._issuer,
                options={
                    "require": [
                        "iss",
                        "aud",
                        "sub",
                        "iat",
                        "exp",
                        "jti",
                        "tender_scope",
                    ]
                },
            )
            scope = TenderScope.model_validate(payload["tender_scope"])
        except (jwt.PyJWTError, KeyError, TypeError, ValueError) as exc:
            raise ScopeTokenError("invalid or expired tender evidence token") from exc
        if scope.issuer != self._issuer or scope.audience != self._audience:
            raise ScopeTokenError("tender evidence scope issuer or audience mismatch")
        if scope.subject != payload.get("sub"):
            raise ScopeTokenError("tender evidence token subject mismatch")
        if (
            scope.issued_at != payload.get("iat")
            or scope.expires_at != payload.get("exp")
        ):
            raise ScopeTokenError("tender evidence token time claims mismatch")
        return scope


class ScopedTokenVerifier(TokenVerifier):
    def __init__(self, codec: ScopedTokenCodec):
        self._codec = codec

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            scope = self._codec.verify(token)
        except ScopeTokenError:
            return None
        return AccessToken(
            token=token,
            client_id="bid-intake-agent",
            scopes=[f"tender:{item}" for item in sorted(scope.allowed_tools)],
            expires_at=scope.expires_at,
            resource=scope.audience,
            subject=scope.subject,
            claims={"tender_scope": scope.model_dump(mode="json")},
        )


class McpAccessTokenScopeProvider:
    def __call__(self) -> TenderScope:
        access_token = get_access_token()
        if access_token is None or not access_token.claims:
            raise ScopeTokenError("authenticated MCP access token is required")
        try:
            return TenderScope.model_validate(access_token.claims["tender_scope"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ScopeTokenError("MCP access token has no valid tender scope") from exc


class EnvironmentScopeProvider:
    """Scope provider for local stdio transport.

    The token remains outside tool arguments, so the model cannot choose or
    alter case_id, assessment_id, or agent_run_id.
    """

    def __init__(
        self,
        codec: ScopedTokenCodec,
        *,
        environment_variable: str = "TENDER_MCP_SCOPE_TOKEN",
    ):
        self._codec = codec
        self._environment_variable = environment_variable

    def __call__(self) -> TenderScope:
        token = os.environ.get(self._environment_variable, "").strip()
        if not token:
            raise ScopeTokenError(
                f"{self._environment_variable} is required for MCP stdio transport"
            )
        return self._codec.verify(token)
