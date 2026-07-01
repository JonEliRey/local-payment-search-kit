from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch


TENANT_CONFIG = {
    "version": 1,
    "tenants": {"acme-corp": {"display_name": "Acme Corporation", "isos": ["acme-iso"]}},
    "isos": {"acme-iso": {"display_name": "Acme ISO", "merchants": ["acme-store", "other-store"]}},
    "merchants": {
        "acme-store": {"display_name": "Acme Store", "iso": "acme-iso"},
        "other-store": {"display_name": "Other Store", "iso": "acme-iso"},
    },
    "users": {"merchant-user@acme.example.com": {"role": "merchant_user", "tenant": "acme-corp", "assigned_merchants": ["acme-store"]}},
}


class TenantScopedDashboardHttpTests(unittest.TestCase):
    def test_served_dashboard_uses_request_identity_and_authorized_scope(self):
        with _dashboard_server() as base_url:
            html = _get_text(base_url + "/", headers={"X-Payment-Evidence-Dev-User": "merchant-user@acme.example.com"})

        self.assertIn('data-testid="identity-panel"', html)
        self.assertIn("merchant-user@acme.example.com", html)
        self.assertIn("Acme Corporation", html)
        self.assertIn('value="acme-store"', html)
        self.assertIn("Acme Store", html)
        self.assertNotIn('value="other-store"', html)
        self.assertNotIn("Other Store", html)
        self.assertNotIn("fallback static page", html)
        self.assert_safe(html)

    def test_cloudflare_access_assertion_maps_to_registry_scope(self):
        calls: list[str] = []

        def validator(assertion: str) -> str | None:
            calls.append(assertion)
            return "merchant-user@acme.example.com"

        with _dashboard_server(identity_mode="cloudflare", cloudflare_validator=validator) as base_url:
            html = _get_text(base_url + "/", headers={"Cf-Access-Jwt-Assertion": "validated.jwt"})

        self.assertEqual(calls, ["validated.jwt"])
        self.assertIn("merchant-user@acme.example.com", html)
        self.assertIn('value="acme-store"', html)
        self.assertIn("Acme Store", html)
        self.assertNotIn('value="other-store"', html)
        self.assertNotIn("Other Store", html)
        self.assert_safe(html)

    def test_served_dashboard_without_identity_does_not_render_registry_internals(self):
        with _dashboard_server() as base_url:
            html = _get_text(base_url + "/")

        self.assertIn("Transaction Search", html)
        self.assertNotIn("assigned_merchants", html)
        self.assertNotIn("other-store", html)
        self.assertNotIn("tenant_registry_path", html)
        self.assert_safe(html)

    def test_setup_required_page_links_to_browser_setup_wizard(self):
        with _dashboard_server(tenant_registry=False) as server:
            html = _get_text(server.base_url + "/")

        self.assertIn("Setup required", html)
        self.assertIn('href="/setup"', html)
        self.assertIn("Open setup wizard", html)
        self.assertIn("payment-search add-merchant", html)
        self.assert_safe(html)

    def test_setup_wizard_get_renders_local_merchant_form_without_secret_values(self):
        with _dashboard_server(tenant_registry=False) as server:
            html = _get_text(server.base_url + "/setup")

        self.assertIn("Merchant setup", html)
        self.assertIn('name="alias"', html)
        self.assertIn('name="display_name"', html)
        self.assertIn('name="api_key"', html)
        self.assertIn('type="password"', html)
        self.assertNotIn("value=\"", html.split('name="api_key"', 1)[1].split(">", 1)[0])
        self.assert_safe(html)

    def test_setup_wizard_post_writes_local_secret_ref_and_redirects_to_dashboard(self):
        with _dashboard_server(tenant_registry=False) as server:
            payload = {
                "alias": "merchant-local",
                "display_name": "Merchant Local",
                "gateway": "nmi",
                "base_url": "https://mbcard.transactiongateway.com",
                "api_key": "synthetic-browser-key",
            }
            response = _post_form(server.base_url + "/setup", payload)
            config_text = server.config_path.read_text()
            secret_text = server.secret_store_path.read_text()
            dashboard = _get_text(server.base_url + "/")

        self.assertEqual(response["status"], 303)
        self.assertEqual(response["location"], "/")
        self.assertIn("local_secret_ref", config_text)
        self.assertNotIn("synthetic-browser-key", config_text)
        self.assertIn("synthetic-browser-key", secret_text)
        self.assertIn('value="merchant-local"', dashboard)
        self.assertNotIn("Setup required", dashboard)

    def test_setup_wizard_post_requires_missing_fields_without_writing_secret(self):
        with _dashboard_server(tenant_registry=False) as server:
            response = _post_form(server.base_url + "/setup", {"alias": "missing-key"})
            html = str(response["body"])
            config_path = server.config_path
            secret_store_path = server.secret_store_path

        self.assertEqual(response["status"], 400)
        self.assertIn("API key is required", html)
        self.assertFalse(config_path.exists())
        self.assertFalse(secret_store_path.exists())


    def test_local_mode_search_authorizes_configured_merchant_without_registry(self):
        with _dashboard_server(tenant_registry=False) as server:
            _post_form(
                server.base_url + "/setup",
                {
                    "alias": "suddergoose-llc",
                    "display_name": "Suddergoose LLC",
                    "gateway": "nmi",
                    "base_url": "https://mbcard.transactiongateway.com",
                    "api_key": "synthetic-local-search-key",
                },
            )
            with patch("payment_evidence.cli._run_search") as run_search:
                run_search.return_value = {
                    "status": "completed",
                    "merchant": "suddergoose-llc",
                    "candidate_summary": {"candidate_count": 0, "top_score": 0, "ambiguous": False},
                    "candidates": [],
                }
                response = _post_json(
                    server.base_url + "/api/search",
                    {
                        "merchant_id": "suddergoose-llc",
                        "start_date": "2026-06-01",
                        "end_date": "2026-06-02",
                        "amount": "10.00",
                    },
                )

        self.assertEqual(response["status"], 200)
        self.assertNotEqual(response["json"].get("status"), "denied")
        run_search.assert_called_once()

    def test_local_mode_search_denies_unknown_merchant_before_gateway(self):
        with _dashboard_server(tenant_registry=False) as server:
            _post_form(
                server.base_url + "/setup",
                {
                    "alias": "known-merchant",
                    "display_name": "Known Merchant",
                    "gateway": "nmi",
                    "base_url": "https://mbcard.transactiongateway.com",
                    "api_key": "synthetic-known-key",
                },
            )
            with patch("payment_evidence.cli._run_search") as run_search:
                response = _post_json(
                    server.base_url + "/api/search",
                    {
                        "merchant_id": "unknown-merchant",
                        "start_date": "2026-06-01",
                        "end_date": "2026-06-02",
                        "amount": "10.00",
                    },
                )

        self.assertEqual(response["status"], 403)
        self.assertEqual(response["json"].get("status"), "denied")
        self.assertIn("unknown", response["json"].get("reason", ""))
        run_search.assert_not_called()

    def test_setup_wizard_selects_existing_merchant_and_preserves_key_when_blank(self):
        with _dashboard_server(tenant_registry=False) as server:
            _post_form(
                server.base_url + "/setup",
                {
                    "alias": "suddergoose-llc",
                    "display_name": "Suddergoose LLC",
                    "gateway": "nmi",
                    "base_url": "https://mbcard.transactiongateway.com",
                    "api_key": "original-secret-key",
                },
            )
            setup_html = _get_text(server.base_url + "/setup?merchant=suddergoose-llc")
            response = _post_form(
                server.base_url + "/setup",
                {
                    "alias": "suddergoose-llc",
                    "display_name": "Suddergoose LLC Updated",
                    "gateway": "nmi",
                    "base_url": "https://example-gateway.local",
                    "api_key": "",
                },
            )
            config = json.loads(server.config_path.read_text())
            secret = server.secret_store_path.read_text()

        self.assertIn("Existing merchants", setup_html)
        self.assertIn('value="suddergoose-llc" selected', setup_html)
        self.assertIn('value="Suddergoose LLC"', setup_html)
        self.assertNotIn("original-secret-key", setup_html)
        self.assertEqual(response["status"], 303)
        merchant = config["merchants"]["suddergoose-llc"]
        self.assertEqual(merchant["display_name"], "Suddergoose LLC Updated")
        self.assertEqual(merchant["base_url"], "https://example-gateway.local")
        self.assertIn("original-secret-key", secret)

    def test_search_page_has_merchants_navigation_and_local_retention_copy(self):
        with _dashboard_server(tenant_registry=False) as server:
            html = _get_text(server.base_url + "/")

        self.assertIn('href="/"', html)
        self.assertIn('href="/setup"', html)
        self.assertIn("Merchants", html)
        self.assertIn("local run history", html.lower())
        self.assertNotIn("expire after 1 hour", html.lower())

    def assert_safe(self, html: str) -> None:
        lowered = html.lower()
        for forbidden in ("fake-secret-token", "op" + "://", "/home" + "/nova", "config_path", "tenant_registry_path", "4111111111111111", "cvv"):
            self.assertNotIn(forbidden, lowered)


class _dashboard_server:
    def __init__(self, *, identity_mode: str = "dev", cloudflare_validator=None, tenant_registry: bool = True) -> None:
        self.identity_mode = identity_mode
        self.cloudflare_validator = cloudflare_validator
        self.tenant_registry = tenant_registry

    def __enter__(self) -> "_dashboard_server":
        from payment_evidence.web_dashboard import create_human_search_handler

        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.config_path = root / "config.json"
        self.secret_store_path = root / "secrets.json"
        tenant_path = root / "tenants.json"
        if self.tenant_registry:
            tenant_path.write_text(json.dumps(TENANT_CONFIG))
        handler = create_human_search_handler(
            page="<html>fallback static page</html>",
            artifact_root=root / "artifacts",
            config_path=self.config_path,
            gateway="nmi",
            timeout=5,
            tenant_registry_path=tenant_path if self.tenant_registry else None,
            identity_mode=self.identity_mode,
            dev_identity_enabled=True,
            cloudflare_validator=self.cloudflare_validator,
            audit_path=root / "audit.jsonl",
            secret_store_path=self.secret_store_path,
        )
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"
        return self

    def __add__(self, suffix: str) -> str:
        return self.base_url + suffix

    def __exit__(self, *_exc: object) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.tmp.cleanup()


def _get_text(url: str, *, headers: dict[str, str] | None = None) -> str:
    request = urllib.request.Request(url, headers=headers or {}, method="GET")
    with urllib.request.urlopen(request, timeout=5) as response:
        return response.read().decode("utf-8")


def _post_form(url: str, payload: dict[str, str]) -> dict[str, int | str | None]:
    data = urllib.parse.urlencode(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    opener = urllib.request.build_opener(_NoRedirectHandler)
    try:
        with opener.open(request, timeout=5) as response:
            return {
                "status": response.status,
                "location": response.headers.get("Location"),
                "body": response.read().decode("utf-8"),
            }
    except urllib.error.HTTPError as exc:
        return {
            "status": exc.code,
            "location": exc.headers.get("Location"),
            "body": exc.read().decode("utf-8"),
        }


def _post_json(url: str, payload: dict[str, str]) -> dict[str, object]:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return {"status": response.status, "json": json.loads(response.read().decode("utf-8"))}
    except urllib.error.HTTPError as exc:
        return {"status": exc.code, "json": json.loads(exc.read().decode("utf-8"))}


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001, D401
        return None


if __name__ == "__main__":
    unittest.main()
