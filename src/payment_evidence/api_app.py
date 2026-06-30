from __future__ import annotations

import time
from argparse import Namespace
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, cast

from fastapi import Body, Depends, FastAPI, HTTPException, Path as PathParam, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from . import cli as cli_module
from .api_models import (
    ApiErrorDetail,
    ArtifactListResponse,
    ArtifactMetadata,
    CapabilitiesResponse,
    CapabilityField,
    DeniedResponse,
    ErrorResponse,
    GatewayCapability,
    HealthResponse,
    IdentityScope,
    InvestigateRequest,
    InvestigateResponse,
    MerchantScope,
    SearchRequest,
    SearchResponse,
    WhoamiResponse,
)
from .access import authorize_merchant
from .artifacts import ArtifactRecord, ArtifactStore
from .config import load_merchant_config, resolve_default_merchant_alias
from .gateways.nmi import NMI_ADAPTER_INFO
from .identity import CloudflareValidator, extract_identity
from .secrets import resolve_security_key  # noqa: F401 - exercised by later parity route wiring
from .service_requests import validate_investigate_request, validate_search_request, validation_error_response
from .tenant_registry import TenantRegistry


class ApiSettings(BaseModel):
    """Runtime wiring for the FastAPI boundary.

    This object is deliberately constructed by callers/tests instead of reading
    shell environment or .env files at import/app creation time.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    config_path: Path | None = None
    artifact_root: Path = Field(default_factory=lambda: Path("payment-evidence-web-artifacts"))
    tenant_registry_path: Path | None = None
    identity_mode: str = "production"
    dev_identity_enabled: bool = False
    cors_origins: list[str] = Field(default_factory=list)
    cors_allow_credentials: bool = True
    static_frontend_build_path: Path | None = None
    dependency_overrides: dict[str, object] = Field(default_factory=dict)


def create_app(settings: ApiSettings | None = None) -> FastAPI:
    settings = settings or ApiSettings()
    _validate_settings(settings)

    app = FastAPI(title="Transaction Search API")
    app.state.api_settings = settings
    register_error_handlers(app)

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=settings.cors_allow_credentials,
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=["*"],
        )

    @app.middleware("http")
    async def add_no_store_header(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/api/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse()

    @app.get("/api/whoami", response_model=WhoamiResponse, responses={401: {"model": DeniedResponse}})
    def whoami(request: Request) -> WhoamiResponse | JSONResponse:
        return _whoami_response(request, settings)

    @app.get("/api/capabilities", response_model=CapabilitiesResponse, responses={501: {"model": ErrorResponse}})
    def capabilities() -> CapabilitiesResponse:
        return _default_capabilities()

    @app.post(
        "/api/search",
        response_model=SearchResponse,
        responses={401: {"model": DeniedResponse}, 403: {"model": DeniedResponse}, 400: {"model": ErrorResponse}, 422: {"model": ErrorResponse}, 502: {"model": ErrorResponse}},
    )
    def search(http_request: Request, request: SearchRequest = Body(...)) -> JSONResponse:
        return _search_response(http_request, request, settings)

    @app.post(
        "/api/investigate",
        response_model=InvestigateResponse,
        responses={401: {"model": DeniedResponse}, 403: {"model": DeniedResponse}, 400: {"model": ErrorResponse}, 422: {"model": ErrorResponse}, 502: {"model": ErrorResponse}},
    )
    def investigate(http_request: Request, _preauth: None = Depends(_investigate_preauth_dependency(settings)), request: InvestigateRequest = Body(...)) -> JSONResponse:
        _ = _preauth
        return _investigate_response(http_request, request, settings)

    @app.get("/api/artifacts", response_model=ArtifactListResponse, responses={401: {"model": DeniedResponse}, 403: {"model": DeniedResponse}})
    def artifacts(request: Request) -> JSONResponse:
        return _artifact_list_response(request, settings)

    @app.get(
        "/api/artifacts/{artifact_id}",
        responses={401: {"model": DeniedResponse}, 403: {"model": DeniedResponse}, 404: {"model": ErrorResponse}, 410: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
    )
    def artifact(request: Request, artifact_id: str = PathParam(..., min_length=1, max_length=128)) -> Response:
        return _artifact_get_response(request, artifact_id, settings)

    if settings.static_frontend_build_path is not None and (settings.static_frontend_build_path / "index.html").is_file():
        app.mount("/", StaticFiles(directory=settings.static_frontend_build_path, html=True), name="frontend")

    return app


def _validate_settings(settings: ApiSettings) -> None:
    if settings.identity_mode == "production" and settings.dev_identity_enabled:
        raise RuntimeError("dev identity cannot be enabled in production mode")
    if settings.cors_allow_credentials and any(origin == "*" for origin in settings.cors_origins):
        raise ValueError("wildcard CORS is forbidden when credentials are allowed")
    settings.artifact_root = settings.artifact_root.expanduser().resolve()
    if settings.tenant_registry_path is not None:
        settings.tenant_registry_path = settings.tenant_registry_path.expanduser().resolve()
    if settings.static_frontend_build_path is not None:
        settings.static_frontend_build_path = settings.static_frontend_build_path.expanduser().resolve()


def _not_implemented() -> dict[str, str]:
    return ErrorResponse(error="not_implemented").model_dump(exclude={"errors", "elapsed_ms"})


def _default_capabilities() -> CapabilitiesResponse:
    nmi = _nmi_capability("nmi")
    synthetic = _nmi_capability("synthetic_nmi")
    return CapabilitiesResponse(gateways=[nmi, synthetic])


def _search_response(http_request: Request, request: SearchRequest, settings: ApiSettings) -> JSONResponse:
    started = time.perf_counter()
    form = _search_form(request)
    auth = _authorize_api_request(http_request, form, settings)
    if auth.get("status") != "ok":
        code = status.HTTP_401_UNAUTHORIZED if _identity_denial(str(auth.get("reason") or "")) else status.HTTP_403_FORBIDDEN
        return _denied_response(str(auth.get("reason") or "denied"), code=code)

    validation = validate_search_request(form)
    if not validation.valid:
        return _error_response(validation_error_response(validation), status.HTTP_422_UNPROCESSABLE_ENTITY, started)

    try:
        merchant = load_merchant_config(settings.config_path, str(auth["merchant_alias"]))
        security_key = resolve_security_key(merchant)
    except Exception:
        return _error_response({"status": "error", "error": "credential_resolution_failed"}, status.HTTP_502_BAD_GATEWAY, started)

    try:
        timeout = int(cast(int | str, settings.dependency_overrides.get("gateway_timeout") or 5))
        raw = cli_module._run_search(_api_search_args(validation.normalized, timeout=timeout), merchant, security_key)
    except Exception:
        return _error_response({"status": "error", "error": "request_failed"}, status.HTTP_400_BAD_REQUEST, started)

    payload = _sanitize_api_search_response(raw)
    payload["elapsed_ms"] = _elapsed_ms(started)
    return JSONResponse(status_code=status.HTTP_200_OK, content=payload)


def _investigate_preauth_dependency(settings: ApiSettings) -> Callable[[Request], None]:
    def dependency(http_request: Request) -> None:
        if settings.tenant_registry_path is None:
            return None
        mode = _effective_identity_mode(settings.identity_mode)
        reason = _preauth_denial_reason(http_request, settings, mode)
        if reason is not None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=DeniedResponse(reason=reason).model_dump())
        try:
            registry = TenantRegistry(settings.tenant_registry_path)
        except Exception:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=DeniedResponse(reason="denied: registry_invalid").model_dump())
        cloudflare_validator = settings.dependency_overrides.get("cloudflare_validator")
        validator = cast(CloudflareValidator, cloudflare_validator) if callable(cloudflare_validator) else None
        extracted = extract_identity(
            http_request.headers,
            registry,
            mode=mode,
            dev_enabled=settings.dev_identity_enabled,
            cloudflare_validator=validator,
        )
        if not extracted.allowed or extracted.identity is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=DeniedResponse(reason=extracted.reason).model_dump())

    return dependency


def _investigate_response(http_request: Request, request: InvestigateRequest, settings: ApiSettings) -> JSONResponse:
    started = time.perf_counter()
    form = _search_form(request)
    auth = _authorize_api_request(http_request, form, settings)
    if auth.get("status") != "ok":
        code = status.HTTP_401_UNAUTHORIZED if _identity_denial(str(auth.get("reason") or "")) else status.HTTP_403_FORBIDDEN
        return _denied_response(str(auth.get("reason") or "denied"), code=code)

    validation = validate_investigate_request(form)
    if not validation.valid:
        return _error_response(validation_error_response(validation), status.HTTP_422_UNPROCESSABLE_ENTITY, started)

    try:
        merchant = load_merchant_config(settings.config_path, str(auth["merchant_alias"]))
        security_key = resolve_security_key(merchant)
    except Exception:
        return _error_response({"status": "error", "error": "credential_resolution_failed"}, status.HTTP_502_BAD_GATEWAY, started)

    try:
        timeout = int(cast(int | str, settings.dependency_overrides.get("gateway_timeout") or 5))
        args = _api_search_args(validation.normalized, timeout=timeout)
        args.output_dir = str(settings.artifact_root)
        args.case_id = _safe_case_id(validation.normalized)
        args.title = "Transaction Search Detail"
        args.lookback_days = int(_clean(validation.normalized.get("lookback_days")) or 365)
        args.lookahead_days = int(_clean(validation.normalized.get("lookahead_days")) or 0)
        args.match = _clean(validation.normalized.get("match")) or "customer_id,masked_card,email,billing_zip"
        args.pretty = True
        raw = cli_module._run_investigate(args, merchant, security_key)
    except Exception:
        return _error_response({"status": "error", "error": "request_failed"}, status.HTTP_400_BAD_REQUEST, started)

    if raw.get("status") in {"api_error", "failed"}:
        return _error_response({"status": "error", "error": str(raw.get("error") or "request_failed")}, status.HTTP_502_BAD_GATEWAY, started)

    payload = _sanitize_api_investigate_response(raw, auth, artifact_root=settings.artifact_root)
    payload["elapsed_ms"] = _elapsed_ms(started)
    return JSONResponse(status_code=status.HTTP_200_OK, content=payload)


def _artifact_list_response(http_request: Request, settings: ApiSettings) -> JSONResponse:
    auth = _authenticate_artifact_request(http_request, settings)
    if auth.get("status") != "ok":
        code = status.HTTP_401_UNAUTHORIZED if _identity_denial(str(auth.get("reason") or "")) else status.HTTP_403_FORBIDDEN
        return _denied_response(str(auth.get("reason") or "denied"), code=code)
    identity = auth["identity"]
    registry = cast(TenantRegistry, auth["registry"])
    merchant_aliases = _authorized_artifact_merchants(identity, registry)
    store = ArtifactStore(settings.artifact_root, ttl_seconds=3600)
    artifacts = [
        _artifact_metadata_from_record(record)
        for record in store.list_for_access(
            owner_user_id=getattr(identity, "user_id", ""),
            tenant_id=str(getattr(identity, "tenant_id", "") or ""),
            merchant_aliases=merchant_aliases,
        )
    ]
    return JSONResponse(status_code=status.HTTP_200_OK, content=ArtifactListResponse(artifacts=artifacts).model_dump())


def _artifact_get_response(http_request: Request, artifact_id: str, settings: ApiSettings) -> Response:
    if _unsafe_artifact_id(artifact_id):
        return _artifact_status_response(http_request, "not_found", status.HTTP_404_NOT_FOUND)
    auth = _authenticate_artifact_request(http_request, settings)
    if auth.get("status") != "ok":
        code = status.HTTP_401_UNAUTHORIZED if _identity_denial(str(auth.get("reason") or "")) else status.HTTP_403_FORBIDDEN
        return _denied_response(str(auth.get("reason") or "denied"), code=code)
    identity = auth["identity"]
    registry = cast(TenantRegistry, auth["registry"])
    store = ArtifactStore(settings.artifact_root, ttl_seconds=3600)
    metadata = store.metadata(artifact_id)
    if metadata.get("status") == "not_found":
        return _artifact_status_response(http_request, "not_found", status.HTTP_404_NOT_FOUND)
    merchant_alias = str(metadata.get("merchant_alias") or "")
    authorized = authorize_merchant(identity, merchant_alias, registry.as_auth_registry())
    if not authorized.allowed:
        return _artifact_status_response(http_request, "denied", status.HTTP_403_FORBIDDEN)
    result = store.resolve_for_access(
        artifact_id,
        owner_user_id=getattr(identity, "user_id", ""),
        tenant_id=str(getattr(identity, "tenant_id", "") or ""),
        merchant_alias=merchant_alias,
    )
    if result.status == "ok" and result.path is not None:
        return Response(content=result.path.read_bytes(), media_type=_artifact_content_type(result.record))
    if result.status == "expired":
        return _artifact_status_response(http_request, "expired", status.HTTP_410_GONE)
    if result.status == "denied":
        return _artifact_status_response(http_request, "denied", status.HTTP_403_FORBIDDEN)
    return _artifact_status_response(http_request, "not_found", status.HTTP_404_NOT_FOUND)


def _authenticate_artifact_request(http_request: Request, settings: ApiSettings) -> dict[str, Any]:
    mode = _effective_identity_mode(settings.identity_mode)
    preauth_denial = _preauth_denial_reason(http_request, settings, mode)
    if preauth_denial is not None:
        return {"status": "denied", "reason": preauth_denial}
    if settings.tenant_registry_path is None:
        return {"status": "denied", "reason": "denied: registry_not_configured"}
    try:
        registry = TenantRegistry(settings.tenant_registry_path)
    except Exception:
        return {"status": "denied", "reason": "denied: registry_invalid"}
    cloudflare_validator = settings.dependency_overrides.get("cloudflare_validator")
    validator = cast(CloudflareValidator, cloudflare_validator) if callable(cloudflare_validator) else None
    extracted = extract_identity(
        http_request.headers,
        registry,
        mode=mode,
        dev_enabled=settings.dev_identity_enabled,
        cloudflare_validator=validator,
    )
    if not extracted.allowed or extracted.identity is None:
        return {"status": "denied", "reason": extracted.reason}
    return {"status": "ok", "identity": extracted.identity, "registry": registry}


def _authorized_artifact_merchants(identity: Any, registry: TenantRegistry) -> set[str]:
    role = getattr(identity, "role", "")
    if role == "ethion_admin":
        return set(registry.merchant_ids())
    if role in {"iso_admin", "iso_user"}:
        return set(registry.iso_merchants(str(getattr(identity, "iso_id", "") or "")))
    if role in {"merchant_admin", "merchant_user"}:
        return set(getattr(identity, "assigned_merchants", frozenset()) or frozenset())
    return set()


def _artifact_metadata_from_record(record: ArtifactRecord) -> ArtifactMetadata:
    return ArtifactMetadata(
        artifact_id=record.artifact_id,
        label=_artifact_label(record.artifact_type),
        kind=record.artifact_type,
        content_type=_artifact_content_type(record),
        merchant=record.merchant_alias,
        expires_at=record.expires_at.isoformat(),
    )


def _artifact_label(kind: str) -> str:
    return {
        "dashboard": "Transaction detail",
        "packet": "Transaction packet",
        "history": "Transaction history",
        "operator_report": "Operator report",
    }.get(kind, "Transaction artifact")


def _artifact_content_type(record: ArtifactRecord | None) -> str:
    if record is not None:
        if record.artifact_type == "dashboard" or record.original_name.endswith(".html"):
            return "text/html; charset=utf-8"
        if record.artifact_type == "history" or record.original_name.endswith(".json"):
            return "application/json; charset=utf-8"
    return "text/plain; charset=utf-8"


def _artifact_status_response(http_request: Request, safe_status: str, code: int) -> Response:
    if _wants_html(http_request):
        return Response(content=_render_artifact_status_page(safe_status), status_code=code, media_type="text/html; charset=utf-8")
    if safe_status == "denied":
        return JSONResponse(status_code=code, content=DeniedResponse(reason="denied").model_dump())
    error = "artifact_expired" if safe_status == "expired" else "artifact_not_found"
    return JSONResponse(status_code=code, content=ErrorResponse(error=error).model_dump(exclude={"errors", "elapsed_ms"}))


def _wants_html(http_request: Request) -> bool:
    return "text/html" in http_request.headers.get("accept", "")


def _render_artifact_status_page(safe_status: str) -> str:
    title = "Transaction detail expired" if safe_status == "expired" else "Transaction detail unavailable"
    message = "This transaction detail link has expired. Return to search and open a fresh detail link." if safe_status == "expired" else "This transaction detail is unavailable or you do not have access."
    return f'<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{title}</title></head><body><main><h1>{title}</h1><p>{message}</p><a href="/">New search</a></main></body></html>'


def _unsafe_artifact_id(artifact_id: str) -> bool:
    return "/" in artifact_id or "\\" in artifact_id or ".." in artifact_id


def _authorize_api_request(http_request: Request, form: dict[str, Any], settings: ApiSettings) -> dict[str, Any]:
    mode = _effective_identity_mode(settings.identity_mode)
    preauth_denial = _preauth_denial_reason(http_request, settings, mode)
    requested_merchant = _clean(form.get("merchant") or form.get("merchant_id"))
    if preauth_denial is not None:
        return {"status": "denied", "reason": preauth_denial, "merchant_alias": requested_merchant}
    if settings.tenant_registry_path is None:
        return {"status": "denied", "reason": "denied: registry_not_configured", "merchant_alias": requested_merchant}
    try:
        registry = TenantRegistry(settings.tenant_registry_path)
    except Exception:
        return {"status": "denied", "reason": "denied: registry_invalid", "merchant_alias": requested_merchant}

    cloudflare_validator = settings.dependency_overrides.get("cloudflare_validator")
    validator = cast(CloudflareValidator, cloudflare_validator) if callable(cloudflare_validator) else None
    extracted = extract_identity(
        http_request.headers,
        registry,
        mode=mode,
        dev_enabled=settings.dev_identity_enabled,
        cloudflare_validator=validator,
    )
    if not extracted.allowed or extracted.identity is None:
        return {"status": "denied", "reason": extracted.reason, "merchant_alias": requested_merchant}
    identity = extracted.identity
    merchant_alias = resolve_default_merchant_alias(settings.config_path, requested_merchant)
    if not merchant_alias:
        return {"status": "denied", "reason": "denied: merchant_required", "identity": identity, "merchant_alias": requested_merchant}
    authorized = authorize_merchant(identity, merchant_alias, registry.as_auth_registry())
    if not authorized.allowed:
        return {"status": "denied", "reason": authorized.reason, "identity": identity, "merchant_alias": merchant_alias}
    return {"status": "ok", "identity": identity, "merchant_alias": merchant_alias}


def _search_form(request: SearchRequest) -> dict[str, Any]:
    form = request.model_dump(exclude_none=True)
    if "amount" in form:
        form["amount"] = str(form["amount"])
    for merchant_field in ("merchant", "merchant_id"):
        if merchant_field in form:
            form[merchant_field] = str(form[merchant_field])
    return form


def _api_search_args(form: dict[str, Any], *, timeout: int) -> Namespace:
    return Namespace(
        start_date=_clean(form.get("start_date")),
        end_date=_clean(form.get("end_date")),
        amount=_clean(form.get("amount")),
        last_four=_clean(form.get("last_four")),
        order_id=_clean(form.get("order_id")),
        transaction_id=_clean(form.get("transaction_id")),
        action_type=_clean(form.get("action_type")),
        condition=_clean(form.get("condition")),
        transaction_type=_clean(form.get("transaction_type")),
        result_limit=int(form.get("result_limit") or 100),
        max_pages=int(form.get("max_pages") or 5),
        timeout=timeout,
    )


def _safe_case_id(form: dict[str, Any]) -> str:
    for key in ("transaction_id", "order_id"):
        value = _clean(form.get(key))
        if value:
            return "case-" + "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in value)[:80]
    return "case-api-investigate"


SUMMARY_SAFE_TOP_LEVEL_KEYS = {"search_lookup", "candidate_summary", "candidates"}
SUMMARY_SAFE_CANDIDATE_KEYS = {"rank", "score", "transaction_id", "order_id", "amount", "date", "last_four", "condition", "transaction_type", "currency", "cc_type", "action_summaries", "explanations"}


def _sanitize_api_search_response(result: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {"status": "ok"}
    for key in SUMMARY_SAFE_TOP_LEVEL_KEYS:
        if key in result:
            if key == "search_lookup":
                payload[key] = _sanitize_search_lookup(result[key])
            elif key == "candidate_summary":
                payload[key] = _sanitize_candidate_summary(result[key])
            else:
                payload[key] = result[key]
    if "candidate_summary" not in payload:
        payload["candidate_summary"] = {"candidate_count": 0, "top_score": 0, "ambiguous": False}
    candidates = result.get("candidates")
    if isinstance(candidates, list):
        payload["candidates"] = [_sanitize_api_candidate(candidate) for candidate in candidates if isinstance(candidate, dict)]
    else:
        payload["candidates"] = []
    return payload


def _sanitize_api_investigate_response(result: dict[str, Any], authorization: dict[str, Any], *, artifact_root: Path) -> dict[str, Any]:
    raw_status = str(result.get("status") or "error")
    if raw_status == "error":
        return {"status": "error", "error": str(result.get("error") or "request_failed")}
    payload: dict[str, Any] = {"status": raw_status}
    if result.get("selected_transaction_id"):
        payload["selected_transaction_id"] = str(result["selected_transaction_id"])
    if "match_status" in result:
        payload["match_status"] = str(result["match_status"])
    if "message" in result:
        payload["message"] = str(result["message"])
    if "candidate_summary" in result:
        payload["candidate_summary"] = _sanitize_candidate_summary(result["candidate_summary"])
    candidates = result.get("candidates")
    if isinstance(candidates, list):
        payload["candidates"] = [_sanitize_api_candidate(candidate) for candidate in candidates if isinstance(candidate, dict)]
    selected_candidate = result.get("selected_candidate")
    if isinstance(selected_candidate, dict):
        payload["selected_candidate"] = _sanitize_api_candidate(selected_candidate)
    if raw_status == "completed":
        artifacts = _store_investigate_artifacts(result.get("artifacts"), authorization, artifact_root=artifact_root)
        if artifacts:
            payload["artifacts"] = [artifact.model_dump() for artifact in artifacts]
    return payload


def _store_investigate_artifacts(raw_artifacts: Any, authorization: dict[str, Any], *, artifact_root: Path) -> list[ArtifactMetadata]:
    if not isinstance(raw_artifacts, dict):
        return []
    identity = authorization.get("identity")
    if identity is None:
        return []
    merchant_alias = str(authorization.get("merchant_alias") or "")
    store = ArtifactStore(artifact_root, ttl_seconds=3600)
    artifacts: list[ArtifactMetadata] = []
    for source_key, kind, label, content_type in (
        ("dashboard_file", "dashboard", "Transaction detail", "text/html"),
        ("packet_file", "packet", "Transaction packet", "text/markdown"),
        ("history_file", "history", "Transaction history", "application/json"),
        ("operator_report_file", "operator_report", "Operator report", "text/markdown"),
    ):
        source = raw_artifacts.get(source_key)
        if not source:
            continue
        record = store.put_existing_file(
            source,
            artifact_type=kind,
            owner_user_id=getattr(identity, "user_id", ""),
            tenant_id=str(getattr(identity, "tenant_id", "") or ""),
            merchant_alias=merchant_alias,
            original_name=Path(str(source)).name,
        )
        artifacts.append(ArtifactMetadata(artifact_id=record.artifact_id, label=label, kind=kind, content_type=content_type, merchant=merchant_alias, expires_at=record.expires_at.isoformat()))
    return artifacts


def _sanitize_api_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    sanitized = {key: candidate[key] for key in SUMMARY_SAFE_CANDIDATE_KEYS if key in candidate and key != "action_summaries"}
    action_summaries = candidate.get("action_summaries")
    if isinstance(action_summaries, list):
        sanitized["action_summaries"] = [_sanitize_action_summary(item) for item in action_summaries if isinstance(item, dict)]
    return sanitized


def _sanitize_candidate_summary(value: Any) -> dict[str, int | bool]:
    if not isinstance(value, dict):
        return {"candidate_count": 0, "top_score": 0, "ambiguous": False}
    return {
        "candidate_count": int(value.get("candidate_count") or 0),
        "top_score": int(value.get("top_score") or 0),
        "ambiguous": bool(value.get("ambiguous")),
    }


def _sanitize_action_summary(action: dict[str, Any]) -> dict[str, Any]:
    return {key: action[key] for key in ("action_type", "amount", "date", "success") if key in action}


def _sanitize_search_lookup(value: Any) -> dict[str, str | int | bool | None]:
    if not isinstance(value, dict):
        return {}
    sanitized: dict[str, str | int | bool | None] = {}
    for key, item in value.items():
        safe_key = str(key)
        if _unsafe_lookup_key(safe_key):
            continue
        if isinstance(item, (str, int, bool)) or item is None:
            sanitized[safe_key] = item
    return sanitized


def _unsafe_lookup_key(key: str) -> bool:
    lowered = key.lower()
    return any(fragment in lowered for fragment in ("raw", "secret", "security", "key", "token", "config", "path"))


def _error_response(payload: dict[str, Any], code: int, started: float) -> JSONResponse:
    safe_payload: dict[str, Any] = {"status": "error", "error": str(payload.get("error") or "request_failed")}
    if isinstance(payload.get("errors"), list):
        safe_payload["errors"] = payload["errors"]
    safe_payload["elapsed_ms"] = _elapsed_ms(started)
    return JSONResponse(status_code=code, content=safe_payload)


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.perf_counter() - started) * 1000))


def _identity_denial(reason: str) -> bool:
    return reason in {
        "denied: missing_identity",
        "denied: malformed_identity",
        "denied: unknown_identity",
        "denied: dev_mode_disabled",
        "denied: missing_cloudflare_assertion",
        "denied: cloudflare_validator_required",
        "denied: invalid_cloudflare_assertion",
        "denied: unsupported_identity_mode",
    }


def _whoami_response(request: Request, settings: ApiSettings) -> WhoamiResponse | JSONResponse:
    mode = _effective_identity_mode(settings.identity_mode)
    preauth_denial = _preauth_denial_reason(request, settings, mode)
    if preauth_denial is not None:
        return _denied_response(preauth_denial)
    if settings.tenant_registry_path is None:
        return _denied_response("denied: registry_not_configured")
    try:
        registry = TenantRegistry(settings.tenant_registry_path)
    except Exception:
        return _denied_response("denied: registry_invalid")
    cloudflare_validator = settings.dependency_overrides.get("cloudflare_validator")
    validator = cast(CloudflareValidator, cloudflare_validator) if callable(cloudflare_validator) else None
    extracted = extract_identity(
        request.headers,
        registry,
        mode=mode,
        dev_enabled=settings.dev_identity_enabled,
        cloudflare_validator=validator,
    )
    if not extracted.allowed or extracted.identity is None:
        return _denied_response(extracted.reason)
    identity = extracted.identity
    return WhoamiResponse(
        identity=IdentityScope(
            user_id=identity.user_id,
            role=identity.role,
            tenant_id=identity.tenant_id,
            iso_id=identity.iso_id,
        ),
        authorized_merchants=[_merchant_scope(alias, registry) for alias in _authorized_merchant_aliases(identity, registry)],
    )


def _denied_response(reason: str, *, code: int = status.HTTP_401_UNAUTHORIZED) -> JSONResponse:
    payload = DeniedResponse(reason=reason).model_dump()
    return JSONResponse(status_code=code, content=payload)


def _effective_identity_mode(mode: str) -> str:
    return "cloudflare" if mode == "production" else mode


def _preauth_denial_reason(request: Request, settings: ApiSettings, mode: str) -> str | None:
    headers = {str(key).lower(): str(value).strip() for key, value in request.headers.items()}
    if mode == "dev":
        if not settings.dev_identity_enabled:
            return "denied: dev_mode_disabled"
        email = headers.get("x-payment-evidence-dev-user")
        if not email:
            return "denied: missing_identity"
        if not _looks_like_email(email):
            return "denied: malformed_identity"
        return None
    if mode == "cloudflare":
        assertion = headers.get("cf-access-jwt-assertion")
        if not assertion:
            return "denied: missing_cloudflare_assertion"
        cloudflare_validator = settings.dependency_overrides.get("cloudflare_validator")
        if not callable(cloudflare_validator):
            return "denied: cloudflare_validator_required"
        try:
            email = cast(CloudflareValidator, cloudflare_validator)(assertion)
        except Exception:
            return "denied: invalid_cloudflare_assertion"
        if not email or not _looks_like_email(email.strip().lower()):
            return "denied: invalid_cloudflare_assertion"
        return None
    return "denied: unsupported_identity_mode"


def _looks_like_email(value: str) -> bool:
    if not value or any(ch.isspace() for ch in value):
        return False
    if value.count("@") != 1:
        return False
    local, domain = value.split("@", 1)
    return bool(local and domain and "." in domain)


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _authorized_merchant_aliases(identity: object, registry: TenantRegistry) -> list[str]:
    auth_registry = registry.as_auth_registry()
    return sorted(
        merchant_alias
        for merchant_alias in registry.merchant_ids()
        if authorize_merchant(identity, merchant_alias, auth_registry).allowed
    )


def _merchant_scope(alias: str, registry: TenantRegistry) -> MerchantScope:
    return MerchantScope(alias=alias, display_name=registry.merchant_display_name(alias))


def _nmi_capability(gateway_name: str) -> GatewayCapability:
    adapter_operations = [capability.name for capability in NMI_ADAPTER_INFO.capabilities if capability.supported]
    operations = [*adapter_operations, "search", "investigate", "artifact_list", "artifact_get"]
    return GatewayCapability(
        gateway=gateway_name,
        supported_operations=operations,
        fields=[
            CapabilityField(name="merchant", label="Merchant", required=False, input_type="text"),
            CapabilityField(name="start_date", label="Start date", required=False, input_type="timestamp", help_text="UTC YYYYMMDDHHMMSS date-window start.", pattern=r"^\d{14}$"),
            CapabilityField(name="end_date", label="End date", required=False, input_type="timestamp", help_text="UTC YYYYMMDDHHMMSS date-window end.", pattern=r"^\d{14}$"),
            CapabilityField(name="amount", label="Amount", required=False, input_type="decimal", min_value=0),
            CapabilityField(name="order_id", label="Order ID", required=False, input_type="text"),
            CapabilityField(name="transaction_id", label="Transaction ID", required=False, input_type="text"),
            CapabilityField(name="last_four", label="Card last four", required=False, input_type="digits", pattern=r"^\d{4}$"),
            CapabilityField(name="result_limit", label="Result limit", required=False, input_type="integer", min_value=1, max_value=500),
            CapabilityField(name="max_pages", label="Maximum pages", required=False, input_type="integer", min_value=1, max_value=25),
            CapabilityField(name="redaction_mode", label="Redaction mode", required=False, input_type="select", choices=["summary", "internal_file"]),
        ],
        strongest_identifiers=["transaction_id", "order_id", "amount", "last_four"],
        date_window_required=True,
        redaction_modes=["summary", "internal_file"],
        artifact_outputs=["html_dashboard", "transaction_packet", "operator_report"],
        caveats=[
            "Live gateway reads require explicit approved runtime configuration and scope.",
            "Transaction search only; client evidence gathering and legal chargeback outcome claims remain out of scope.",
        ],
    )


def _not_implemented_response() -> JSONResponse:
    return JSONResponse(status_code=status.HTTP_501_NOT_IMPLEMENTED, content=_not_implemented())


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(HTTPException, _http_exception_handler)
    app.add_exception_handler(RequestValidationError, _validation_exception_handler)
    app.add_exception_handler(Exception, _unhandled_exception_handler)


def _http_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    _ = request
    assert isinstance(exc, HTTPException)
    if isinstance(exc.detail, dict) and exc.detail.get("status") == "denied":
        return JSONResponse(status_code=exc.status_code, content=exc.detail)
    return JSONResponse(status_code=exc.status_code, content=ErrorResponse(error="request_failed").model_dump(exclude={"errors", "elapsed_ms"}))


def _validation_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    _ = request
    assert isinstance(exc, RequestValidationError)
    errors = [
        ApiErrorDetail(
            field=_safe_error_field(error.get("loc", ())),
            code=str(error.get("type") or "invalid"),
        )
        for error in exc.errors()
    ]
    payload = ErrorResponse(error="invalid_request", errors=errors, elapsed_ms=0).model_dump()
    return JSONResponse(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, content=payload)


def _unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    _ = request, exc
    payload = ErrorResponse(error="internal_error").model_dump(exclude={"errors", "elapsed_ms"})
    return JSONResponse(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, content=payload)


def _safe_error_field(location: object) -> str:
    if not isinstance(location, (list, tuple)):
        return "request"
    parts = [_truncate_field_part(str(part)) for part in location if str(part) not in {"body", "query", "path"}]
    field = ".".join(parts) if parts else "request"
    return field[:80]


def _truncate_field_part(value: str) -> str:
    return value[:80]
