from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path


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

    def assert_safe(self, html: str) -> None:
        lowered = html.lower()
        for forbidden in ("fake-secret-token", "op" + "://", "/home" + "/nova", "config_path", "tenant_registry_path", "4111111111111111", "cvv"):
            self.assertNotIn(forbidden, lowered)


class _dashboard_server:
    def __init__(self, *, identity_mode: str = "dev", cloudflare_validator=None) -> None:
        self.identity_mode = identity_mode
        self.cloudflare_validator = cloudflare_validator

    def __enter__(self) -> str:
        from payment_evidence.web_dashboard import create_human_search_handler

        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        tenant_path = root / "tenants.json"
        tenant_path.write_text(json.dumps(TENANT_CONFIG))
        handler = create_human_search_handler(
            page="<html>fallback static page</html>",
            artifact_root=root / "artifacts",
            config_path=None,
            gateway="nmi",
            timeout=5,
            tenant_registry_path=tenant_path,
            identity_mode=self.identity_mode,
            dev_identity_enabled=True,
            cloudflare_validator=self.cloudflare_validator,
            audit_path=root / "audit.jsonl",
        )
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return f"http://127.0.0.1:{self.server.server_port}"

    def __exit__(self, *_exc: object) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.tmp.cleanup()


def _get_text(url: str, *, headers: dict[str, str] | None = None) -> str:
    request = urllib.request.Request(url, headers=headers or {}, method="GET")
    with urllib.request.urlopen(request, timeout=5) as response:
        return response.read().decode("utf-8")


if __name__ == "__main__":
    unittest.main()
