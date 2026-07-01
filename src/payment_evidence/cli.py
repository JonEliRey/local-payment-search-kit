from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .candidate_search import rank_candidate_transactions
from .config import DEFAULT_CONFIG_PATH, load_configured_aliases, load_merchant_config, resolve_default_merchant_alias
from .dashboard import render_dashboard_html
from .history import summarize_customer_history
from .packet import render_transaction_packet
from .query import build_query_params, execute_query
from .secret_store import LocalSecretStore, SecretStoreError
from .secrets import SecretResolutionError, resolve_security_key


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=_display_prog(),
        description="Deterministic payment search and transaction lookup CLI with redacted JSON output.",
    )
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH, help="Merchant/gateway config JSON path")
    parser.add_argument("--gateway", default="nmi", choices=["nmi"], help="Gateway adapter to use")
    parser.add_argument("--merchant", default=None, help="Merchant alias from config")
    parser.add_argument("--merchant-id", dest="merchant", help="Merchant alias/id from config; synonym for --merchant")
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--redaction", choices=["summary", "internal"], default="summary")
    parser.add_argument("--detail-output", help="Write full JSON result to a local file; required for internal redaction")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON")

    sub = parser.add_subparsers(dest="command", required=True)

    txn = sub.add_parser("transaction", help="Look up by MBCard transaction_id")
    txn.add_argument("--transaction-id", required=True)
    txn.add_argument("--result-limit", default="10")
    txn.add_argument("--pretty", action="store_true", help=argparse.SUPPRESS)

    order = sub.add_parser("order", help="Look up by order_id")
    order.add_argument("--order-id", required=True)
    order.add_argument("--start-date")
    order.add_argument("--end-date")
    order.add_argument("--result-limit", default="10")
    order.add_argument("--pretty", action="store_true", help=argparse.SUPPRESS)

    amount = sub.add_parser("amount", help="Look up by amount inside a bounded UTC date window")
    amount.add_argument("--amount", required=True)
    amount.add_argument("--start-date", required=True)
    amount.add_argument("--end-date", required=True)
    amount.add_argument("--action-type", default="sale")
    amount.add_argument("--condition")
    amount.add_argument("--transaction-type")
    amount.add_argument("--result-limit", default="25")
    amount.add_argument("--pretty", action="store_true", help=argparse.SUPPRESS)

    search = sub.add_parser("search", help="Search bounded date-window candidates with local safe clue ranking")
    search.add_argument("--start-date", required=True)
    search.add_argument("--end-date", required=True)
    search.add_argument("--amount")
    search.add_argument("--last-four")
    search.add_argument("--order-id")
    search.add_argument("--transaction-id")
    search.add_argument("--action-type")
    search.add_argument("--condition")
    search.add_argument("--transaction-type")
    search.add_argument("--result-limit", default="100", help="Candidate page size; defaults to 100 records per page")
    search.add_argument("--max-pages", type=int, default=25, help="Maximum candidate pages to retrieve")
    search.add_argument("--pretty", action="store_true", help=argparse.SUPPRESS)

    history = sub.add_parser("history", help="Summarize same-customer history for a confirmed transaction")
    history.add_argument("--transaction-id", required=True)
    history.add_argument("--lookback-days", type=int, default=365)
    history.add_argument("--lookahead-days", type=int, default=0)
    history.add_argument("--match", default="customer_id,masked_card,email,billing_zip")
    history.add_argument("--result-limit", default="100", help="History page size; defaults to 100 records per page")
    history.add_argument("--max-pages", type=int, default=25, help="Maximum history pages to retrieve")
    history.add_argument("--pretty", action="store_true", help=argparse.SUPPRESS)

    investigate = sub.add_parser("investigate", help="Run deterministic search -> history -> dashboard/packet workflow")
    investigate.add_argument("--start-date")
    investigate.add_argument("--end-date")
    investigate.add_argument("--amount")
    investigate.add_argument("--last-four")
    investigate.add_argument("--order-id")
    investigate.add_argument("--transaction-id")
    investigate.add_argument("--action-type")
    investigate.add_argument("--condition")
    investigate.add_argument("--transaction-type")
    investigate.add_argument("--result-limit", default="100", help="Candidate/history page size; defaults to 100 records per page")
    investigate.add_argument("--max-pages", type=int, default=25, help="Maximum candidate/history pages to retrieve")
    investigate.add_argument("--lookback-days", type=int, default=365)
    investigate.add_argument("--lookahead-days", type=int, default=0)
    investigate.add_argument("--match", default="customer_id,masked_card,email,billing_zip")
    investigate.add_argument("--output-dir", required=True, help="Private directory for search/history/dashboard/packet artifacts")
    investigate.add_argument("--case-id", default="case", help="Artifact filename prefix")
    investigate.add_argument("--title", default="Transaction Search Detail")
    investigate.add_argument("--pretty", action="store_true", help=argparse.SUPPRESS)

    dashboard = sub.add_parser("dashboard", help="Render a static local HTML dashboard from a local case/detail JSON file")
    dashboard.add_argument("--case-file", required=True)
    dashboard.add_argument("--output", required=True)
    dashboard.add_argument("--title", default="Transaction Detail")
    dashboard.add_argument("--pretty", action="store_true", help=argparse.SUPPRESS)

    packet = sub.add_parser("packet", help="Render a print-friendly markdown transaction packet from a local case/detail JSON file")
    packet.add_argument("--case-file", required=True)
    packet.add_argument("--output", required=True)
    packet.add_argument("--title", default="Transaction Information Packet")
    packet.add_argument("--pretty", action="store_true", help=argparse.SUPPRESS)

    secrets = sub.add_parser("secrets", help="Manage the portable local secret store without printing secret values")
    secret_sub = secrets.add_subparsers(dest="secret_command", required=True)
    secret_set = secret_sub.add_parser("set", help="Set a local secret value")
    _add_secret_ref_args(secret_set)
    input_group = secret_set.add_mutually_exclusive_group(required=True)
    input_group.add_argument("--value-stdin", action="store_true", help="Read the secret value from stdin")
    input_group.add_argument("--value-env", help="Read the secret value from an environment variable")
    secret_set.add_argument("--pretty", action="store_true", help=argparse.SUPPRESS)
    secret_list = secret_sub.add_parser("list", help="List local secret refs without values")
    secret_list.add_argument("--pretty", action="store_true", help=argparse.SUPPRESS)
    secret_remove = secret_sub.add_parser("remove", help="Remove a local secret value")
    _add_secret_ref_args(secret_remove)
    secret_remove.add_argument("--pretty", action="store_true", help=argparse.SUPPRESS)

    list_merchants = sub.add_parser("merchants", help="List configured merchant aliases without secrets")
    list_merchants.add_argument("--pretty", action="store_true", help=argparse.SUPPRESS)
    list_merchants.set_defaults(no_secret=True)

    add_merchant = sub.add_parser("add-merchant", help="Add or update a local merchant credential for Payment Search")
    add_merchant.add_argument("--alias", help="Local merchant alias, for example merchant-local")
    add_merchant.add_argument("--display-name", help="Human-readable merchant name")
    add_merchant.add_argument("--gateway", default="nmi", choices=["nmi"], help="Gateway adapter to use")
    add_merchant.add_argument("--base-url", default="https://mbcard.transactiongateway.com", help="Gateway base URL")
    merchant_input = add_merchant.add_mutually_exclusive_group()
    merchant_input.add_argument("--api-key-stdin", action="store_true", help="Read the merchant API/security key from stdin")
    merchant_input.add_argument("--api-key-env", help="Read the merchant API/security key from an environment variable")
    add_merchant.add_argument("--config-output", help="Generated local config path; defaults to ~/.payment-search/config.json")
    add_merchant.add_argument("--secret-store", help="Local secret store path; defaults to ~/.payment-search/secrets.json")
    add_merchant.add_argument("--pretty", action="store_true", help=argparse.SUPPRESS)

    start = sub.add_parser("start", help="Start the local Payment Search browser app")
    start.add_argument("--host", default="127.0.0.1")
    start.add_argument("--port", type=int, default=8787)
    start.add_argument("--output-dir", help="Private local directory for browser-generated transaction detail artifacts")
    start.add_argument("--pretty", action="store_true", help=argparse.SUPPRESS)

    serve = sub.add_parser("serve", help="Run a local human-first search dashboard backed by the gateway API")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8787)
    serve.add_argument("--output-dir", help="Private local directory for transaction detail artifacts generated from the web dashboard")
    serve.add_argument("--tenant-registry", help="Tenant registry JSON path for scoped service identity")
    serve.add_argument("--identity-mode", choices=["cloudflare", "dev"], help="Service identity mode; defaults to PAYMENT_EVIDENCE_IDENTITY_MODE or cloudflare")
    serve.add_argument("--enable-dev-identity", action="store_true", help="Enable X-Payment-Evidence-Dev-User identity for local synthetic UAT only")
    serve.add_argument("--cloudflare-issuer", help="Expected Cloudflare Access JWT issuer")
    serve.add_argument("--cloudflare-audience", help="Expected Cloudflare Access JWT audience")
    serve.add_argument("--cloudflare-jwks-url", help="Cloudflare Access JWKS URL")
    serve.add_argument("--audit-path", help="Metadata-only JSONL audit log path")
    serve.add_argument("--pretty", action="store_true", help=argparse.SUPPRESS)

    return parser


def _display_prog() -> str:
    invoked = Path(sys.argv[0]).name if sys.argv else ""
    if invoked in {"payment-evidence", "mbcard-investigate"}:
        return invoked
    return "payment-search"


def _add_secret_ref_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--scope", required=True, choices=["gateway", "iso", "merchant", "agent", "case"])
    parser.add_argument("--owner", required=True)
    parser.add_argument("--name", required=True)


def _serve_arg(args: argparse.Namespace, attr: str, env_name: str) -> str | None:
    value = getattr(args, attr, None)
    if value not in (None, ""):
        return str(value)
    env_value = os.environ.get(env_name)
    if env_value not in (None, ""):
        return env_value
    return None


def _truthy_env(env_name: str) -> bool:
    return os.environ.get(env_name, "").strip().lower() in {"1", "true", "yes", "on"}


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "add-merchant":
        return _handle_add_merchant(args)

    if args.command == "start":
        return _handle_start(args)

    if args.command == "merchants":
        aliases = load_configured_aliases(args.config)
        return _emit({"status": "completed", "merchants": aliases}, args.pretty)

    if args.command == "serve":
        from .web_dashboard import serve_human_search_dashboard

        serve_human_search_dashboard(
            config_path=args.config,
            gateway=args.gateway,
            host=args.host,
            port=args.port,
            timeout=args.timeout,
            output_dir=_serve_arg(args, "output_dir", "PAYMENT_EVIDENCE_ARTIFACT_ROOT"),
            tenant_registry_path=_serve_arg(args, "tenant_registry", "PAYMENT_EVIDENCE_TENANT_REGISTRY"),
            identity_mode=_serve_arg(args, "identity_mode", "PAYMENT_EVIDENCE_IDENTITY_MODE") or "cloudflare",
            dev_identity_enabled=bool(args.enable_dev_identity or _truthy_env("PAYMENT_EVIDENCE_DEV_IDENTITY_ENABLED")),
            cloudflare_issuer=_serve_arg(args, "cloudflare_issuer", "PAYMENT_EVIDENCE_CLOUDFLARE_ISSUER"),
            cloudflare_audience=_serve_arg(args, "cloudflare_audience", "PAYMENT_EVIDENCE_CLOUDFLARE_AUDIENCE"),
            cloudflare_jwks_url=_serve_arg(args, "cloudflare_jwks_url", "PAYMENT_EVIDENCE_CLOUDFLARE_JWKS_URL"),
            audit_path=_serve_arg(args, "audit_path", "PAYMENT_EVIDENCE_AUDIT_PATH"),
        )
        return 0

    if args.command == "secrets":
        return _handle_secrets(args)

    if args.command == "dashboard":
        return _handle_dashboard(args)

    if args.command == "packet":
        return _handle_packet(args)

    if args.redaction == "internal" and not args.detail_output:
        return _emit_error("internal redaction requires --detail-output so cardholder details are written to a local file, not stdout", args.pretty, code=2)

    if args.command == "history" and args.detail_output and args.redaction != "internal":
        return _emit_error("history detail output requires --redaction internal because it writes same-customer internal evidence to a local file", args.pretty, code=2)

    if args.command == "investigate" and not _investigate_detail_clues(args):
        return _emit_error("investigate requires at least one detail clue such as --amount, --transaction-id, or --order-id; --last-four is supporting context only. Use search for broad exploratory date-window pulls", args.pretty, code=2)

    merchant_alias = resolve_default_merchant_alias(args.config, args.merchant)
    if not merchant_alias:
        return _emit_error("--merchant or --merchant-id is required for API lookups unless PAYMENT_EVIDENCE_MERCHANT, MBCARD_MERCHANT, config default_merchant, or a single configured merchant is available", args.pretty, code=2)

    try:
        merchant = load_merchant_config(args.config, merchant_alias)
        if merchant.gateway != args.gateway:
            return _emit_error(f"Merchant '{merchant.alias}' is configured for gateway '{merchant.gateway}', not '{args.gateway}'", args.pretty, code=2)
        security_key = resolve_security_key(merchant)
        if args.command == "history":
            result = _run_history(args, merchant, security_key)
        elif args.command == "investigate":
            result = _run_investigate(args, merchant, security_key)
        elif args.command == "search":
            result = _run_search(args, merchant, security_key)
        else:
            params = _params_for_args(args, security_key)
            result = execute_query(merchant.base_url, params, timeout=args.timeout, redaction_mode=args.redaction)
            result["merchant"] = _merchant_summary(merchant)
        if args.detail_output:
            detail_path = _write_detail_file(args.detail_output, result, args.pretty)
            summary = _summarize_search_result(result, detail_path) if args.command == "search" else _summarize_detail_file_result(result, detail_path)
            return _emit(summary, args.pretty, code=0 if result.get("status") == "completed" else 3)
        if args.command == "history":
            return _emit(_summarize_history_result(result), args.pretty, code=0 if result.get("status") == "completed" else 3)
        return _emit(result, args.pretty, code=0 if result.get("status") == "completed" else 3)
    except (KeyError, ValueError, SecretResolutionError) as exc:
        return _emit_error(str(exc), args.pretty, code=2)


def _handle_secrets(args: argparse.Namespace) -> int:
    store = LocalSecretStore()
    try:
        if args.secret_command == "set":
            if args.value_stdin:
                value = sys.stdin.read().strip()
            else:
                value = os.environ.get(args.value_env or "", "")
            metadata = store.set_secret(args.scope, args.owner, args.name, value)
            return _emit({"status": "completed", "secret": metadata}, args.pretty)
        if args.secret_command == "list":
            return _emit({"status": "completed", "secrets": store.list_metadata()}, args.pretty)
        if args.secret_command == "remove":
            metadata = store.remove_secret(args.scope, args.owner, args.name)
            return _emit({"status": "completed", "secret": metadata}, args.pretty)
    except SecretStoreError as exc:
        return _emit_error(str(exc), args.pretty, code=2)
    return _emit_error(f"Unsupported secrets command: {args.secret_command}", args.pretty, code=2)


def _handle_add_merchant(args: argparse.Namespace) -> int:
    try:
        alias = _merchant_setup_value(args.alias, "Merchant alias")
        display_name = _merchant_setup_value(args.display_name, "Merchant display name", default=alias)
        api_key = _read_setup_api_key(args)
        if not alias:
            return _emit_error("merchant alias is required", args.pretty, code=2)
        if not display_name:
            return _emit_error("merchant display name is required", args.pretty, code=2)
        if not api_key:
            return _emit_error("api key is required; pass --api-key-stdin, --api-key-env, or run interactively", args.pretty, code=2)
        config_path = _payment_search_config_path(args.config_output)
        secret_store_path = Path(args.secret_store).expanduser() if args.secret_store else _payment_search_secret_store_path()
        secret_ref = f"merchant/{alias}/security_key"
        metadata = LocalSecretStore(secret_store_path).set_secret("merchant", alias, "security_key", api_key)
        config = _read_local_config(config_path)
        merchants = config.setdefault("merchants", {})
        merchants[alias] = {
            "display_name": display_name,
            "gateway": args.gateway,
            "base_url": args.base_url,
            "local_secret_ref": secret_ref,
        }
        config["default_merchant"] = alias
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n")
        return _emit(
            {
                "status": "completed",
                "merchant": {"alias": alias, "display_name": display_name, "gateway": args.gateway, "base_url": args.base_url},
                "config_path": str(config_path),
                "secret_ref": secret_ref,
                "secret": metadata,
            },
            args.pretty,
        )
    except (OSError, json.JSONDecodeError, SecretStoreError, ValueError) as exc:
        return _emit_error(str(exc), args.pretty, code=2)


def _merchant_setup_value(value: str | None, prompt: str, *, default: str | None = None) -> str:
    if value not in (None, ""):
        return str(value).strip()
    if sys.stdin.isatty():
        suffix = f" [{default}]" if default else ""
        entered = input(f"{prompt}{suffix}: ").strip()
        return entered or (default or "")
    return default or ""


def _read_setup_api_key(args: argparse.Namespace) -> str:
    if args.api_key_stdin:
        return sys.stdin.read().strip()
    if args.api_key_env:
        return os.environ.get(args.api_key_env, "").strip()
    if sys.stdin.isatty():
        return input("Merchant API/security key: ").strip()
    return ""


def _payment_search_config_path(config_output: str | None) -> Path:
    if config_output:
        return Path(config_output).expanduser()
    from .local_state import default_config_path

    return default_config_path()


def _payment_search_secret_store_path() -> Path:
    from .local_state import default_secret_store_path

    return default_secret_store_path()


def _read_local_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"merchants": {}}
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        raise ValueError(f"Config root must be an object: {path}")
    merchants = data.setdefault("merchants", {})
    if not isinstance(merchants, dict):
        raise ValueError(f"Config merchants must be an object: {path}")
    return data


def _handle_start(args: argparse.Namespace) -> int:
    from .local_state import default_artifact_dir, default_config_path
    from .web_dashboard import serve_human_search_dashboard

    config_path = args.config if args.config != DEFAULT_CONFIG_PATH else str(default_config_path())
    output_dir = args.output_dir or str(default_artifact_dir())
    serve_human_search_dashboard(
        config_path=config_path,
        gateway=args.gateway,
        host=args.host,
        port=args.port,
        timeout=args.timeout,
        output_dir=output_dir,
        identity_mode="dev",
        dev_identity_enabled=True,
    )
    return 0


def _handle_dashboard(args: argparse.Namespace) -> int:
    try:
        payload = _read_json_file(args.case_file)
        html = render_dashboard_html(payload, title=args.title)
        output_path = _write_text_file(args.output, html)
        return _emit({"status": "completed", "dashboard_file": output_path}, args.pretty)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return _emit_error(str(exc), args.pretty, code=2)


def _handle_packet(args: argparse.Namespace) -> int:
    try:
        payload = _read_json_file(args.case_file)
        packet = render_transaction_packet(payload, title=args.title)
        output_path = _write_text_file(args.output, packet)
        return _emit({"status": "completed", "packet_file": output_path}, args.pretty)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return _emit_error(str(exc), args.pretty, code=2)


def _run_search(args: argparse.Namespace, merchant: Any, security_key: str) -> dict[str, Any]:
    page_result = _execute_candidate_pages(
        merchant.base_url,
        security_key=security_key,
        start_date=args.start_date,
        end_date=args.end_date,
        page_size=int(args.result_limit),
        max_pages=args.max_pages,
        timeout=args.timeout,
        amount=args.amount,
        last_four=args.last_four,
        order_id=args.order_id,
        transaction_id=args.transaction_id,
        action_type=args.action_type,
        condition=args.condition,
        transaction_type=args.transaction_type,
    )
    candidates = page_result.get("transactions", [])
    ranked = rank_candidate_transactions(
        candidates,
        amount=args.amount,
        last_four=args.last_four,
        order_id=args.order_id,
        transaction_id=args.transaction_id,
        action_type=args.action_type,
        condition=args.condition,
        transaction_type=args.transaction_type,
        start_date=args.start_date,
        end_date=args.end_date,
    )
    return {
        "status": "completed" if page_result.get("status") == "completed" else page_result.get("status"),
        "error": page_result.get("error"),
        "merchant": _merchant_summary(merchant),
        "search_window": {"start_date": args.start_date, "end_date": args.end_date},
        "search_lookup": _safe_trace(page_result),
        "candidate_summary": ranked["candidate_summary"],
        "candidates": ranked["candidates"],
    }


def _run_investigate(args: argparse.Namespace, merchant: Any, security_key: str) -> dict[str, Any]:
    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    case_id = _safe_file_stem(args.case_id)

    search_result = _run_search(args, merchant, security_key)
    artifacts: dict[str, Any] = {"search_file": _write_detail_file(str(output_dir / f"{case_id}-search.json"), search_result, args.pretty)}
    candidates = search_result.get("candidates", [])
    selected = candidates[0] if len(candidates) == 1 else None

    if search_result.get("status") != "completed":
        result = {
            "status": search_result.get("status"),
            "error": search_result.get("error"),
            "merchant": search_result.get("merchant"),
            "search_window": search_result.get("search_window"),
            "candidate_summary": search_result.get("candidate_summary"),
            "selected_transaction_id": None,
            "artifacts": artifacts,
            "limitations": ["Search did not complete; no history/dashboard/packet artifacts were generated."],
        }
        artifacts["operator_report_file"] = _write_text_file(str(output_dir / f"{case_id}-operator-report.md"), _render_operator_report(result))
        return result

    if selected is None:
        result = {
            "status": "ambiguous" if candidates else "no_match",
            "error": None,
            "merchant": search_result.get("merchant"),
            "search_window": search_result.get("search_window"),
            "candidate_summary": search_result.get("candidate_summary"),
            "selected_transaction_id": None,
            "candidates": candidates,
            "artifacts": artifacts,
            "limitations": ["No unique selected candidate; explicitly select one result or narrow the search before generating history/dashboard/packet artifacts."],
        }
        artifacts["operator_report_file"] = _write_text_file(str(output_dir / f"{case_id}-operator-report.md"), _render_operator_report(result))
        return result

    history_args = argparse.Namespace(
        transaction_id=selected.get("transaction_id"),
        timeout=args.timeout,
        lookback_days=args.lookback_days,
        lookahead_days=args.lookahead_days,
        match=args.match,
        result_limit=args.result_limit,
        max_pages=args.max_pages,
    )
    history_result = _run_history(history_args, merchant, security_key)
    search_context = _search_context_from_args(args, search_result)
    history_result["search_context"] = search_context
    artifacts["history_file"] = _write_detail_file(str(output_dir / f"{case_id}-history.json"), history_result, args.pretty)
    artifacts["dashboard_file"] = _write_text_file(str(output_dir / f"{case_id}-dashboard.html"), render_dashboard_html(history_result, title=args.title))
    artifacts["packet_file"] = _write_text_file(str(output_dir / f"{case_id}-transaction-packet.md"), render_transaction_packet(history_result, title=args.title))

    result = {
        "status": "completed" if history_result.get("status") == "completed" else history_result.get("status"),
        "error": history_result.get("error"),
        "merchant": search_result.get("merchant"),
        "search_window": search_result.get("search_window"),
        "candidate_summary": search_result.get("candidate_summary"),
        "selected_transaction_id": selected.get("transaction_id"),
        "selected_candidate": selected,
        "search_context": search_context,
        "history_summary": history_result.get("history_summary"),
        "artifacts": artifacts,
        "limitations": history_result.get("history_summary", {}).get("information_limitations", []) if history_result.get("history_summary") else [],
    }
    artifacts["operator_report_file"] = _write_text_file(str(output_dir / f"{case_id}-operator-report.md"), _render_operator_report(result))
    return result


def _search_context_from_args(args: argparse.Namespace, search_result: dict[str, Any]) -> dict[str, str]:
    context: dict[str, str] = {}
    merchant = search_result.get("merchant") if isinstance(search_result.get("merchant"), dict) else {}
    merchant_alias = merchant.get("alias") if isinstance(merchant, dict) else None
    values = {
        "merchant_id": merchant_alias,
        "start_date": getattr(args, "start_date", None),
        "end_date": getattr(args, "end_date", None),
        "amount": getattr(args, "amount", None),
        "order_id": getattr(args, "order_id", None),
        "transaction_id": getattr(args, "transaction_id", None),
        "last_four": getattr(args, "last_four", None),
        "result_limit": getattr(args, "result_limit", None),
    }
    for key, value in values.items():
        if value in (None, "", [], {}):
            continue
        text = str(value).strip()
        if key in {"start_date", "end_date"}:
            text = _html_date_from_gateway_timestamp(text)
        if text:
            context[key] = text[:120]
    return context


def _html_date_from_gateway_timestamp(value: str) -> str:
    if len(value) == 14 and value.isdigit():
        return f"{value[0:4]}-{value[4:6]}-{value[6:8]}"
    return value


def _run_history(args: argparse.Namespace, merchant: Any, security_key: str) -> dict[str, Any]:
    confirmed_params = build_query_params(
        security_key=security_key,
        transaction_id=args.transaction_id,
        result_limit="1",
    )
    confirmed_result = execute_query(merchant.base_url, confirmed_params, timeout=args.timeout, redaction_mode="internal")
    confirmed_result["merchant"] = _merchant_summary(merchant)
    confirmed_transactions = confirmed_result.get("transactions", [])
    if confirmed_result.get("status") != "completed" or not confirmed_transactions:
        return {
            "status": "failed",
            "error": "confirmed transaction lookup did not return a completed transaction record",
            "merchant": _merchant_summary(merchant),
            "confirmed_lookup": _safe_trace(confirmed_result),
            "history_summary": None,
            "transactions": [],
        }
    confirmed = confirmed_transactions[0]
    start_date, end_date = _history_window(confirmed, args.lookback_days, args.lookahead_days)
    history_result = _execute_history_pages(
        merchant.base_url,
        security_key=security_key,
        start_date=start_date,
        end_date=end_date,
        page_size=int(args.result_limit),
        max_pages=args.max_pages,
        timeout=args.timeout,
    )
    candidates = history_result.get("transactions", []) if history_result.get("status") == "completed" else []
    requested_match_keys = [item.strip() for item in args.match.split(",") if item.strip()]
    summary = summarize_customer_history(confirmed, candidates, requested_match_keys=requested_match_keys)
    return {
        "status": "completed" if history_result.get("status") == "completed" else "api_error",
        "error": history_result.get("error"),
        "merchant": _merchant_summary(merchant),
        "history_window": {"start_date": start_date, "end_date": end_date, "lookback_days": args.lookback_days, "lookahead_days": args.lookahead_days},
        "confirmed_lookup": _safe_trace(confirmed_result),
        "history_lookup": _safe_trace(history_result),
        "history_summary": summary,
        "transactions": [confirmed],
        "candidate_transactions": candidates,
    }


def _execute_candidate_pages(
    base_url: str,
    *,
    security_key: str,
    start_date: str | None,
    end_date: str | None,
    page_size: int,
    max_pages: int,
    timeout: int,
    amount: str | None = None,
    last_four: str | None = None,
    order_id: str | None = None,
    transaction_id: str | None = None,
    action_type: str | None = None,
    condition: str | None = None,
    transaction_type: str | None = None,
) -> dict[str, Any]:
    if page_size <= 0:
        raise ValueError("--result-limit must be positive")
    if max_pages <= 0:
        raise ValueError("--max-pages must be positive")
    requested_clues = _candidate_search_clues(
        amount=amount,
        last_four=last_four,
        order_id=order_id,
        transaction_id=transaction_id,
        action_type=action_type,
        condition=condition,
        transaction_type=transaction_type,
    )
    if transaction_id or (order_id and not (amount or last_four)):
        lookup_params = build_query_params(
            security_key=security_key,
            transaction_id=transaction_id,
            order_id=None if transaction_id else order_id,
            start_date=None if transaction_id else start_date,
            end_date=None if transaction_id else end_date,
            result_limit="1",
        )
        lookup_result = execute_query(base_url, lookup_params, timeout=timeout, redaction_mode="internal")
        transactions = lookup_result.get("transactions", [])
        return {
            "status": lookup_result.get("status"),
            "error": lookup_result.get("error"),
            "xml_root": lookup_result.get("xml_root"),
            "trace": lookup_result.get("trace"),
            "page_size": 1,
            "max_pages": 1,
            "requested_clues": requested_clues,
            "page_count": 1,
            "candidate_record_count": len(transactions),
            "pages": [_history_page_trace(0, lookup_result, len(transactions))],
            "transactions": transactions,
        }
    if amount and not (start_date and end_date):
        raise ValueError("amount candidate search requires --start-date and --end-date")
    if not (start_date or end_date):
        raise ValueError("candidate search requires --start-date, --end-date, or an exact identifier")
    transactions: list[dict[str, Any]] = []
    pages: list[dict[str, Any]] = []
    last_result: dict[str, Any] = {"status": "completed", "error": None, "trace": None}
    for page_number in range(max_pages):
        candidate_params = build_query_params(
            security_key=security_key,
            start_date=start_date,
            end_date=end_date,
            result_limit=str(page_size),
            page_number=str(page_number),
            condition=condition,
            action_type=action_type,
            transaction_type=transaction_type,
            require_lookup_key=False,
        )
        page_result = execute_query(base_url, candidate_params, timeout=timeout, redaction_mode="internal")
        last_result = page_result
        page_transactions = page_result.get("transactions", [])
        pages.append(_history_page_trace(page_number, page_result, len(page_transactions)))
        if page_result.get("status") != "completed":
            break
        transactions.extend(page_transactions)
        if len(page_transactions) < page_size:
            break
    return {
        "status": last_result.get("status"),
        "error": last_result.get("error"),
        "xml_root": last_result.get("xml_root"),
        "trace": last_result.get("trace"),
        "page_size": page_size,
        "max_pages": max_pages,
        "requested_clues": requested_clues,
        "page_count": len(pages),
        "candidate_record_count": len(transactions),
        "pages": pages,
        "transactions": transactions,
    }


def _candidate_search_clues(
    *,
    amount: str | None,
    last_four: str | None,
    order_id: str | None,
    transaction_id: str | None,
    action_type: str | None,
    condition: str | None,
    transaction_type: str | None,
) -> list[str]:
    candidates = {
        "amount": amount,
        "last_four": last_four,
        "order_id": order_id,
        "transaction_id": transaction_id,
        "action_type": action_type,
        "condition": condition,
        "transaction_type": transaction_type,
    }
    return [name for name, value in candidates.items() if value]


def _investigate_detail_clues(args: argparse.Namespace) -> list[str]:
    return [name for name in ["amount", "order_id", "transaction_id"] if getattr(args, name, None)]


def _render_operator_report(payload: dict[str, Any]) -> str:
    selected = payload.get("selected_candidate") or {}
    history_summary = payload.get("history_summary") or {}
    artifacts = payload.get("artifacts") or {}
    limitations = payload.get("limitations") or []
    explanations = selected.get("explanations") or []
    lines = [
        "# Transaction Search Operator Report",
        "",
        f"Status: {payload.get('status')}",
        f"Selected transaction ID: {payload.get('selected_transaction_id') or 'none'}",
        f"Payment status: {selected.get('condition') or 'unknown'}",
        f"Why selected: {'; '.join(explanations) if explanations else 'no unique selected candidate'}",
        "",
        "## Search",
        f"Window start: {(payload.get('search_window') or {}).get('start_date')}",
        f"Window end: {(payload.get('search_window') or {}).get('end_date')}",
        f"Candidate summary: {json.dumps(payload.get('candidate_summary') or {}, sort_keys=True)}",
        "",
        "## History",
        f"Confirmed order ID: {history_summary.get('confirmed_order_id') or 'unknown'}",
        f"Prior transaction count: {history_summary.get('prior_transaction_count', 'unknown')}",
        f"Prior settled count: {history_summary.get('prior_settled_count', 'unknown')}",
        f"Failed count: {history_summary.get('failed_count', 'unknown')}",
        f"Refunded or voided count: {history_summary.get('refunded_or_voided_count', 'unknown')}",
        f"Pattern summary: {history_summary.get('pattern_summary') or 'not available'}",
        "",
        "## Artifacts",
        f"Search: {artifacts.get('search_file') or 'not generated'}",
        f"History: {artifacts.get('history_file') or 'not generated'}",
        f"Dashboard: {artifacts.get('dashboard_file') or 'not generated'}",
        f"Transaction packet: {artifacts.get('packet_file') or 'not generated'}",
        f"Operator report: {artifacts.get('operator_report_file') or 'this file'}",
        "",
        "## Limitations",
    ]
    if limitations:
        lines.extend(f"- {item}" for item in limitations)
    else:
        lines.append("- None reported by the tool.")
    lines.extend(["", "Do not replace this report with model-inferred payment facts. Use the dashboard and transaction packet for review."])
    return "\n".join(lines) + "\n"


def _execute_history_pages(
    base_url: str,
    *,
    security_key: str,
    start_date: str,
    end_date: str,
    page_size: int,
    max_pages: int,
    timeout: int,
) -> dict[str, Any]:
    if page_size <= 0:
        raise ValueError("--result-limit must be positive")
    if max_pages <= 0:
        raise ValueError("--max-pages must be positive")

    transactions: list[dict[str, Any]] = []
    pages: list[dict[str, Any]] = []
    last_result: dict[str, Any] = {"status": "completed", "error": None, "trace": None}
    for page_number in range(max_pages):
        history_params = build_query_params(
            security_key=security_key,
            start_date=start_date,
            end_date=end_date,
            result_limit=str(page_size),
            page_number=str(page_number),
            require_lookup_key=False,
        )
        page_result = execute_query(base_url, history_params, timeout=timeout, redaction_mode="internal")
        last_result = page_result
        page_transactions = page_result.get("transactions", [])
        pages.append(_history_page_trace(page_number, page_result, len(page_transactions)))
        if page_result.get("status") != "completed":
            break
        transactions.extend(page_transactions)
        if len(page_transactions) < page_size:
            break
    return {
        "status": last_result.get("status"),
        "error": last_result.get("error"),
        "xml_root": last_result.get("xml_root"),
        "trace": last_result.get("trace"),
        "page_size": page_size,
        "max_pages": max_pages,
        "page_count": len(pages),
        "candidate_record_count": len(transactions),
        "pages": pages,
        "transactions": transactions,
    }


def _history_page_trace(page_number: int, page_result: dict[str, Any], record_count: int) -> dict[str, Any]:
    trace = page_result.get("trace") or {}
    return {
        "page_number": page_number,
        "status": page_result.get("status"),
        "error": page_result.get("error"),
        "http_status": trace.get("http_status"),
        "content_type": trace.get("content_type"),
        "record_count": record_count,
    }


def _params_for_args(args: argparse.Namespace, security_key: str) -> dict[str, str]:
    if args.command == "transaction":
        return build_query_params(
            security_key=security_key,
            transaction_id=args.transaction_id,
            result_limit=args.result_limit,
        )
    if args.command == "order":
        return build_query_params(
            security_key=security_key,
            order_id=args.order_id,
            start_date=args.start_date,
            end_date=args.end_date,
            result_limit=args.result_limit,
        )
    if args.command == "amount":
        return build_query_params(
            security_key=security_key,
            amount=args.amount,
            start_date=args.start_date,
            end_date=args.end_date,
            action_type=args.action_type,
            condition=args.condition,
            transaction_type=args.transaction_type,
            result_limit=args.result_limit,
        )
    raise ValueError(f"Unsupported command: {args.command}")


def _history_window(confirmed: dict[str, Any], lookback_days: int, lookahead_days: int = 0) -> tuple[str, str]:
    if lookback_days <= 0:
        raise ValueError("--lookback-days must be positive")
    if lookahead_days < 0:
        raise ValueError("--lookahead-days cannot be negative")
    anchor = _latest_action_datetime(confirmed) or datetime.now(timezone.utc)
    start = anchor - timedelta(days=lookback_days)
    end = anchor + timedelta(days=lookahead_days)
    return start.strftime("%Y%m%d%H%M%S"), end.strftime("%Y%m%d%H%M%S")


def _latest_action_datetime(txn: dict[str, Any]) -> datetime | None:
    parsed = []
    for action in txn.get("actions", []):
        raw = str(action.get("date", ""))
        if len(raw) != 14 or not raw.isdigit():
            continue
        parsed.append(datetime.strptime(raw, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc))
    return max(parsed) if parsed else None


def _safe_trace(payload: dict[str, Any]) -> dict[str, Any]:
    output = {
        "status": payload.get("status"),
        "error": payload.get("error"),
        "trace": payload.get("trace"),
    }
    for key in ["page_size", "max_pages", "requested_clues", "page_count", "candidate_record_count", "pages"]:
        if key in payload:
            output[key] = payload.get(key)
    return output


def _merchant_summary(merchant: Any) -> dict[str, Any]:
    return {
        "alias": merchant.alias,
        "display_name": merchant.display_name,
        "gateway": merchant.gateway,
    }


def _emit(payload: dict[str, Any], pretty: bool, *, code: int = 0) -> int:
    print(json.dumps(payload, indent=2 if pretty else None, sort_keys=True))
    return code


def _emit_error(message: str, pretty: bool, *, code: int) -> int:
    return _emit({"status": "blocked", "error": message}, pretty, code=code)


def _read_json_file(path: str) -> dict[str, Any]:
    payload = json.loads(Path(path).expanduser().read_text())
    if not isinstance(payload, dict):
        raise ValueError("case file must contain a JSON object")
    return payload


def _write_text_file(path: str, content: str) -> str:
    output_path = Path(path).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content)
    return str(output_path)


def _write_detail_file(path: str, payload: dict[str, Any], pretty: bool) -> str:
    output_path = Path(path).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2 if pretty else None, sort_keys=True) + "\n")
    return str(output_path)


def _safe_file_stem(value: str) -> str:
    stem = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in str(value).strip())
    return stem.strip("-_") or "case"


def _summarize_history_result(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": payload.get("status"),
        "error": payload.get("error"),
        "merchant": payload.get("merchant"),
        "history_window": payload.get("history_window"),
        "confirmed_lookup": payload.get("confirmed_lookup"),
        "history_lookup": payload.get("history_lookup"),
        "history_summary": payload.get("history_summary"),
    }


def _summarize_search_result(payload: dict[str, Any], detail_path: str | None = None) -> dict[str, Any]:
    output = {
        "status": payload.get("status"),
        "error": payload.get("error"),
        "merchant": payload.get("merchant"),
        "search_window": payload.get("search_window"),
        "search_lookup": payload.get("search_lookup"),
        "candidate_summary": payload.get("candidate_summary"),
        "candidates": payload.get("candidates", []),
    }
    if detail_path:
        output["detail_file"] = detail_path
    return output


def _summarize_detail_file_result(payload: dict[str, Any], detail_path: str) -> dict[str, Any]:
    transactions = payload.get("transactions", [])
    output = {
        "status": payload.get("status"),
        "error": payload.get("error"),
        "merchant": payload.get("merchant"),
        "detail_file": detail_path,
        "trace": payload.get("trace"),
        "history_summary": payload.get("history_summary"),
        "transaction_summaries": [
            {
                "transaction_id": txn.get("transaction_id"),
                "order_id": txn.get("order_id"),
                "condition": txn.get("condition"),
                "transaction_type": txn.get("transaction_type"),
                "currency": txn.get("currency"),
                "cc_type": txn.get("cc_type"),
                "action_count": len(txn.get("actions", [])),
                "actions": [
                    {
                        "action_type": action.get("action_type"),
                        "amount": action.get("amount"),
                        "date": action.get("date"),
                        "success": action.get("success"),
                        "response_text": action.get("response_text"),
                        "response_code": action.get("response_code"),
                    }
                    for action in txn.get("actions", [])
                ],
            }
            for txn in transactions
        ],
    }
    if "history_lookup" in payload:
        output["history_lookup"] = payload.get("history_lookup")
    if "history_window" in payload:
        output["history_window"] = payload.get("history_window")
    return output


if __name__ == "__main__":
    raise SystemExit(main())
