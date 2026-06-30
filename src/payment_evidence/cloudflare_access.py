from __future__ import annotations

import base64
import json
import time
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey

JWKSFetcher = Callable[[], Mapping[str, Any]]
Clock = Callable[[], int | float]


@dataclass
class CloudflareAccessValidator:
    """Validate Cloudflare Access JWT assertions and return normalized email.

    The validator is intentionally narrow: RS256 Cloudflare Access assertions,
    configured issuer, configured audience, signature verification against JWKS,
    and minimal time/email claim checks. It raises ``ValueError`` for every
    denial so callers can fail closed without leaking validator internals.
    """

    issuer: str
    audience: str
    jwks_fetcher: JWKSFetcher
    now: Clock = time.time
    leeway_seconds: int = 0
    _jwks_cache: Mapping[str, Any] | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        if not str(self.issuer or "").strip():
            raise ValueError("cloudflare issuer required")
        if not str(self.audience or "").strip():
            raise ValueError("cloudflare audience required")
        if self.jwks_fetcher is None:
            raise ValueError("cloudflare jwks fetcher required")
        self.issuer = self.issuer.strip().rstrip("/")
        self.audience = self.audience.strip()

    def __call__(self, assertion: str) -> str:
        try:
            header_segment, payload_segment, signature_segment = assertion.split(".")
        except ValueError as exc:
            raise ValueError("invalid cloudflare assertion") from exc
        signing_input = f"{header_segment}.{payload_segment}".encode("ascii")
        header = _json_segment(header_segment)
        claims = _json_segment(payload_segment)
        if header.get("alg") != "RS256":
            raise ValueError("invalid cloudflare assertion")
        kid = header.get("kid")
        if not isinstance(kid, str) or not kid.strip():
            raise ValueError("invalid cloudflare assertion")
        signature = _b64url_decode(signature_segment)
        public_key = self._public_key_for_kid(kid)
        try:
            public_key.verify(signature, signing_input, padding.PKCS1v15(), hashes.SHA256())
        except Exception as exc:
            raise ValueError("invalid cloudflare assertion") from exc
        self._validate_claims(claims)
        email = claims.get("email")
        if not isinstance(email, str):
            raise ValueError("invalid cloudflare assertion")
        cleaned = email.strip().lower()
        if not _looks_like_email(cleaned):
            raise ValueError("invalid cloudflare assertion")
        return cleaned

    def _validate_claims(self, claims: Mapping[str, Any]) -> None:
        if str(claims.get("iss") or "").rstrip("/") != self.issuer:
            raise ValueError("invalid cloudflare assertion")
        audience = claims.get("aud")
        if isinstance(audience, str):
            audiences = {audience}
        elif isinstance(audience, list):
            audiences = {item for item in audience if isinstance(item, str)}
        else:
            audiences = set()
        if self.audience not in audiences:
            raise ValueError("invalid cloudflare assertion")
        now = int(self.now())
        exp = _int_claim(claims, "exp")
        if exp is None or now > exp + self.leeway_seconds:
            raise ValueError("invalid cloudflare assertion")
        nbf = _int_claim(claims, "nbf")
        if "nbf" in claims and nbf is None:
            raise ValueError("invalid cloudflare assertion")
        if nbf is not None and now + self.leeway_seconds < nbf:
            raise ValueError("invalid cloudflare assertion")

    def _public_key_for_kid(self, kid: str) -> RSAPublicKey:
        jwks = self._jwks_cache or self._fetch_jwks()
        try:
            return self._public_key_from_jwks(jwks, kid)
        except ValueError:
            if self._jwks_cache is None:
                raise
            jwks = self._fetch_jwks()
            return self._public_key_from_jwks(jwks, kid)

    def _fetch_jwks(self) -> Mapping[str, Any]:
        try:
            jwks = self.jwks_fetcher()
        except Exception as exc:
            raise ValueError("invalid cloudflare assertion") from exc
        self._jwks_cache = jwks
        return jwks

    def _public_key_from_jwks(self, jwks: Mapping[str, Any], kid: str) -> RSAPublicKey:
        keys = jwks.get("keys") if isinstance(jwks, Mapping) else None
        if not isinstance(keys, list):
            raise ValueError("invalid cloudflare assertion")
        for jwk in keys:
            if isinstance(jwk, Mapping) and jwk.get("kid") == kid:
                return _rsa_public_key_from_jwk(jwk)
        raise ValueError("invalid cloudflare assertion")


def validator_from_jwks_url(*, issuer: str, audience: str, jwks_url: str, timeout: int = 5) -> CloudflareAccessValidator:
    issuer_value = str(issuer or "").strip()
    issuer_parts = urllib.parse.urlparse(issuer_value)
    if issuer_parts.scheme != "https" or not issuer_parts.hostname:
        raise ValueError("cloudflare issuer must use https")
    if not str(jwks_url or "").strip():
        raise ValueError("cloudflare jwks url required")
    url = jwks_url.strip()
    jwks_parts = urllib.parse.urlparse(url)
    if jwks_parts.scheme != "https":
        raise ValueError("cloudflare jwks url must use https")
    if jwks_parts.hostname != issuer_parts.hostname:
        raise ValueError("cloudflare jwks host must match issuer host")

    def fetch() -> Mapping[str, Any]:
        request = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    return CloudflareAccessValidator(issuer=issuer_value, audience=audience, jwks_fetcher=fetch)


def _rsa_public_key_from_jwk(jwk: Mapping[str, Any]) -> RSAPublicKey:
    if jwk.get("kty") != "RSA":
        raise ValueError("invalid cloudflare assertion")
    n = jwk.get("n")
    e = jwk.get("e")
    if not isinstance(n, str) or not isinstance(e, str):
        raise ValueError("invalid cloudflare assertion")
    try:
        public_key = rsa.RSAPublicNumbers(e=_int_from_b64url(e), n=_int_from_b64url(n)).public_key()
    except Exception as exc:
        raise ValueError("invalid cloudflare assertion") from exc
    if not isinstance(public_key, RSAPublicKey):
        raise ValueError("invalid cloudflare assertion")
    return public_key


def _json_segment(segment: str) -> Mapping[str, Any]:
    try:
        value = json.loads(_b64url_decode(segment).decode("utf-8"))
    except Exception as exc:
        raise ValueError("invalid cloudflare assertion") from exc
    if not isinstance(value, Mapping):
        raise ValueError("invalid cloudflare assertion")
    return value


def _b64url_decode(value: str) -> bytes:
    try:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except Exception as exc:
        raise ValueError("invalid cloudflare assertion") from exc


def _int_from_b64url(value: str) -> int:
    return int.from_bytes(_b64url_decode(value), "big")


def _int_claim(claims: Mapping[str, Any], key: str) -> int | None:
    value = claims.get(key)
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _looks_like_email(value: str) -> bool:
    if not value or any(ch.isspace() for ch in value):
        return False
    if value.count("@") != 1:
        return False
    local, domain = value.split("@", 1)
    return bool(local and domain and "." in domain)
