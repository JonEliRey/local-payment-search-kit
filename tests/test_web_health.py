from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch


class HealthEndpointTests(unittest.TestCase):
    def test_api_health_is_minimal_no_store_and_does_not_touch_credentials(self):
        from payment_evidence.web_dashboard import create_human_search_handler

        with tempfile.TemporaryDirectory() as tmp:
            handler = create_human_search_handler(
                page="<html>dashboard</html>",
                artifact_root=Path(tmp),
                config_path=None,
                gateway="nmi",
                timeout=5,
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                url = f"http://127.0.0.1:{server.server_port}/api/health"
                with patch("payment_evidence.web_dashboard.resolve_security_key", side_effect=AssertionError("credential resolver touched")):
                    with urllib.request.urlopen(url, timeout=5) as response:
                        body = response.read().decode("utf-8")
                        payload = json.loads(body)
                        headers = {key.lower(): value for key, value in response.headers.items()}
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

        self.assertEqual(payload, {"status": "ok"})
        self.assertEqual(headers.get("cache-control"), "no-store")
        self.assertIn("application/json", headers.get("content-type", ""))
        self.assertNotIn("python", headers.get("server", "").lower())
        self.assertNotIn("basehttp", headers.get("server", "").lower())
        self.assertNotIn("paymentevidence", headers.get("server", "").lower())

        forbidden_keys = {
            "tenant",
            "tenants",
            "merchant",
            "merchants",
            "version",
            "hostname",
            "host",
            "uptime",
            "pid",
            "process",
            "environment",
            "env",
            "config",
            "config_path",
            "path",
        }
        self.assertTrue(forbidden_keys.isdisjoint(payload.keys()))
        serialized = json.dumps(payload).lower()
        for forbidden_text in ("tenant", "merchant", "version", "hostname", "uptime", "config", "environment"):
            self.assertNotIn(forbidden_text, serialized)


if __name__ == "__main__":
    unittest.main()
