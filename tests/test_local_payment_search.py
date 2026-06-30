from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from payment_evidence import cli
from payment_evidence.secret_store import LocalSecretStore, default_secret_store_path


ROOT = Path(__file__).resolve().parents[1]


class PaymentSearchCliSurfaceTests(unittest.TestCase):
    def test_pyproject_exposes_payment_search_console_script(self):
        text = (ROOT / "pyproject.toml").read_text()
        self.assertIn('payment-search = "payment_evidence.cli:main"', text)

    def test_help_uses_payment_search_product_name(self):
        parser = cli.build_parser()
        self.assertEqual(parser.prog, "payment-search")
        help_text = parser.format_help()
        self.assertIn("deterministic payment search", help_text.lower())
        self.assertIn("add-merchant", help_text)
        self.assertIn("start", help_text)


class PaymentSearchLocalStateTests(unittest.TestCase):
    def test_default_local_state_paths_resolve_under_payment_search_home(self):
        from payment_evidence.local_state import default_artifact_dir, default_config_path, default_state_dir

        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(os.environ, {"PAYMENT_SEARCH_HOME": tmpdir}, clear=False):
            self.assertEqual(default_state_dir(), Path(tmpdir))
            self.assertEqual(default_config_path(), Path(tmpdir) / "config.json")
            self.assertEqual(default_secret_store_path(), Path(tmpdir) / "secrets.json")
            self.assertEqual(default_artifact_dir(), Path(tmpdir) / "artifacts")

    def test_payment_search_secret_store_env_wins_over_legacy_env(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            new_path = Path(tmpdir) / "new-secrets.json"
            old_path = Path(tmpdir) / "old-secrets.json"
            with patch.dict(
                os.environ,
                {
                    "PAYMENT_SEARCH_SECRET_STORE": str(new_path),
                    "PAYMENT_EVIDENCE_SECRET_STORE": str(old_path),
                },
                clear=False,
            ):
                self.assertEqual(default_secret_store_path(), new_path)


class PaymentSearchAddMerchantTests(unittest.TestCase):
    def _run_cli(self, argv: list[str], stdin: str = "") -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with patch.object(sys, "stdin", io.StringIO(stdin)), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = cli.main(argv)
        return code, stdout.getvalue(), stderr.getvalue()

    def test_noninteractive_add_merchant_writes_safe_config_and_redacted_output(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.json"
            secret_path = Path(tmpdir) / "secrets.json"
            sentinel_key = "sentinel-secret-api-key-123"

            code, stdout, stderr = self._run_cli(
                [
                    "--pretty",
                    "add-merchant",
                    "--alias",
                    "merchant-local",
                    "--display-name",
                    "Local Shop",
                    "--gateway",
                    "nmi",
                    "--base-url",
                    "https://mbcard.transactiongateway.com",
                    "--api-key-stdin",
                    "--config-output",
                    str(config_path),
                    "--secret-store",
                    str(secret_path),
                ],
                stdin=sentinel_key,
            )

            self.assertEqual(code, 0, stderr + stdout)
            self.assertNotIn(sentinel_key, stdout)
            self.assertNotIn(sentinel_key, stderr)
            config = json.loads(config_path.read_text())
            self.assertEqual(config["default_merchant"], "merchant-local")
            merchant = config["merchants"]["merchant-local"]
            self.assertEqual(merchant["display_name"], "Local Shop")
            self.assertEqual(merchant["gateway"], "nmi")
            self.assertEqual(merchant["base_url"], "https://mbcard.transactiongateway.com")
            self.assertEqual(merchant["local_secret_ref"], "merchant/merchant-local/security_key")
            self.assertNotIn("api_key", json.dumps(config))
            self.assertNotIn(sentinel_key, json.dumps(config))
            self.assertEqual(LocalSecretStore(secret_path).get_secret_ref("merchant/merchant-local/security_key"), sentinel_key)

    def test_add_merchant_preserves_other_merchants_and_updates_same_alias(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.json"
            secret_path = Path(tmpdir) / "secrets.json"

            first = self._run_cli(
                ["add-merchant", "--alias", "first", "--display-name", "First", "--api-key-stdin", "--config-output", str(config_path), "--secret-store", str(secret_path)],
                stdin="first-key",
            )
            second = self._run_cli(
                ["add-merchant", "--alias", "second", "--display-name", "Second", "--api-key-stdin", "--config-output", str(config_path), "--secret-store", str(secret_path)],
                stdin="second-key",
            )
            update = self._run_cli(
                ["add-merchant", "--alias", "first", "--display-name", "First Updated", "--api-key-stdin", "--config-output", str(config_path), "--secret-store", str(secret_path)],
                stdin="updated-key",
            )

            self.assertEqual(first[0], 0, first[1] + first[2])
            self.assertEqual(second[0], 0, second[1] + second[2])
            self.assertEqual(update[0], 0, update[1] + update[2])
            config = json.loads(config_path.read_text())
            self.assertEqual(sorted(config["merchants"]), ["first", "second"])
            self.assertEqual(config["merchants"]["first"]["display_name"], "First Updated")
            self.assertEqual(LocalSecretStore(secret_path).get_secret_ref("merchant/first/security_key"), "updated-key")

    def test_noninteractive_add_merchant_requires_missing_fields(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            code, stdout, stderr = self._run_cli(
                ["add-merchant", "--alias", "missing-key", "--config-output", str(Path(tmpdir) / "config.json")],
                stdin="",
            )
            self.assertEqual(code, 2)
            self.assertIn("api key", stdout.lower())


class PaymentSearchStartTests(unittest.TestCase):
    def test_dashboard_without_configured_merchants_shows_setup_required_guidance(self):
        from payment_evidence.web_dashboard import render_human_search_dashboard

        html = render_human_search_dashboard([])

        self.assertIn("Setup required", html)
        self.assertIn("Add your merchant API credentials before running Transaction Search", html)
        self.assertIn("payment-search add-merchant", html)

    def test_dashboard_with_configured_merchants_shows_alias_without_setup_required(self):
        from payment_evidence.web_dashboard import render_dashboard_for_request

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.json"
            config_path.write_text(json.dumps({
                "merchants": {
                    "merchant-local": {
                        "display_name": "Local Shop",
                        "gateway": "nmi",
                        "base_url": "https://mbcard.transactiongateway.com",
                        "local_secret_ref": "merchant/merchant-local/security_key",
                    }
                }
            }))

            html = render_dashboard_for_request(
                {},
                tenant_registry_path=None,
                identity_mode="dev",
                dev_identity_enabled=True,
                config_path=config_path,
            )

        self.assertIn('value="merchant-local"', html)
        self.assertIn("Local Shop", html)
        self.assertNotIn("Setup required", html)


class PaymentSearchDocsTests(unittest.TestCase):
    def test_public_docs_teach_payment_search_commands_and_not_legacy_cli(self):
        doc_paths = [ROOT / "README.md", ROOT / "QUICKSTART.md", ROOT / "AGENT_RUNBOOK.md"]
        for path in doc_paths:
            self.assertTrue(path.exists(), f"missing {path.name}")
            text = path.read_text()
            self.assertIn("Payment Search", text)
            self.assertIn("payment-search add-merchant", text)
            self.assertIn("payment-search start", text)
            self.assertNotIn("payment-" + "evidence add-merchant", text)
            self.assertNotIn("payment-" + "evidence start", text)

    def test_setup_and_start_wrappers_are_payment_search_only(self):
        script_paths = [
            ROOT / "scripts/setup-local-kit.sh",
            ROOT / "scripts/setup-local-kit.ps1",
            ROOT / "scripts/start-dashboard.sh",
            ROOT / "scripts/start-dashboard.ps1",
        ]
        for path in script_paths:
            self.assertTrue(path.exists(), f"missing {path}")
            text = path.read_text()
            self.assertIn("payment-search", text)
            self.assertNotIn("payment-evidence", text)


if __name__ == "__main__":
    unittest.main()
