import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from payment_evidence.api_app import _api_search_args, _search_form
from payment_evidence.api_models import SearchRequest
from payment_evidence.candidate_search import rank_candidate_transactions
from payment_evidence.parser import parse_query_response
from payment_evidence.redaction import redact_transaction, redact_transactions
from payment_evidence.config import load_merchant_config, resolve_default_merchant_alias
from payment_evidence.query import build_query_params
from payment_evidence.service_requests import validate_search_request

SAMPLE_XML = b'''<?xml version="1.0" encoding="UTF-8"?>
<nm_response>
  <transaction>
    <transaction_id>12345</transaction_id>
    <transaction_type>cc</transaction_type>
    <condition>complete</condition>
    <order_id>ORDER-1</order_id>
    <first_name>Jane</first_name>
    <last_name>Customer</last_name>
    <email>jane@example.com</email>
    <cc_number>4xxxxxxxxxxx1111</cc_number>
    <cc_bin>411111</cc_bin>
    <cc_type>visa</cc_type>
    <currency>USD</currency>
    <action>
      <amount>42.50</amount>
      <action_type>sale</action_type>
      <date>20260612010000</date>
      <success>1</success>
      <response_text>SUCCESS</response_text>
      <response_code>100</response_code>
    </action>
    <action>
      <amount>42.50</amount>
      <action_type>settle</action_type>
      <date>20260612030000</date>
      <success>1</success>
      <processor_batch_id>ABC</processor_batch_id>
    </action>
  </transaction>
</nm_response>'''

ERROR_XML = b'''<?xml version="1.0" encoding="UTF-8"?>
<nm_response><error_response>Specified API key not found REFID:1</error_response></nm_response>'''


class ParserTests(unittest.TestCase):
    def test_parse_query_response_preserves_transactions_and_actions(self):
        result = parse_query_response(SAMPLE_XML)
        self.assertEqual(result["xml_root"], "nm_response")
        self.assertEqual(result["error"], None)
        self.assertEqual(len(result["transactions"]), 1)
        txn = result["transactions"][0]
        self.assertEqual(txn["transaction_id"], "12345")
        self.assertEqual(txn["order_id"], "ORDER-1")
        self.assertEqual(len(txn["actions"]), 2)
        self.assertEqual(txn["actions"][0]["action_type"], "sale")
        self.assertEqual(txn["actions"][1]["action_type"], "settle")

    def test_parse_query_response_returns_error_without_throwing_on_api_error(self):
        result = parse_query_response(ERROR_XML)
        self.assertEqual(result["xml_root"], "nm_response")
        self.assertEqual(result["transactions"], [])
        self.assertIn("Specified API key", result["error"])

    def test_redact_transaction_removes_pii_and_payment_identifiers_but_keeps_investigation_fields(self):
        txn = parse_query_response(SAMPLE_XML)["transactions"][0]
        redacted = redact_transaction(txn)
        serialized = json.dumps(redacted)
        self.assertNotIn("Jane", serialized)
        self.assertNotIn("jane@example.com", serialized)
        self.assertNotIn("4xxxxxxxxxxx1111", serialized)
        self.assertNotIn("411111", serialized)
        self.assertEqual(redacted["transaction_id"], "12345")
        self.assertEqual(redacted["condition"], "complete")
        self.assertEqual(redacted["actions"][0]["amount"], "42.50")
        self.assertEqual(redacted["cc_type"], "visa")

    def test_internal_redaction_preserves_cardholder_and_safe_masked_card_fields(self):
        txn = parse_query_response(SAMPLE_XML)["transactions"][0]
        redacted = redact_transaction(txn, mode="internal")
        serialized = json.dumps(redacted)
        self.assertIn("Jane", serialized)
        self.assertIn("jane@example.com", serialized)
        self.assertIn("4xxxxxxxxxxx1111", serialized)
        self.assertEqual(redacted["cc_type"], "visa")
        self.assertNotIn("411111", serialized)

    def test_internal_redaction_never_exposes_unmasked_card_number(self):
        txn = parse_query_response(SAMPLE_XML)["transactions"][0]
        txn["cc_number"] = "4111111111111111"
        redacted = redact_transaction(txn, mode="internal")
        serialized = json.dumps(redacted)
        self.assertNotIn("4111111111111111", serialized)
        self.assertTrue(redacted["cc_number_redacted"])

    def test_redact_transactions_supports_summary_and_internal_modes(self):
        txn = parse_query_response(SAMPLE_XML)["transactions"][0]
        summary = redact_transactions([txn], mode="summary")
        internal = redact_transactions([txn], mode="internal")
        self.assertNotIn("email", summary[0])
        self.assertIn("email", internal[0])


class ConfigTests(unittest.TestCase):
    def test_load_merchant_config_supports_multiple_merchants_without_secret_values(self):
        config = {
            "base_url": "https://mbcard.transactiongateway.com",
            "op_item": "Example Merchant API Keys",
            "op_vault": "Operations",
            "merchants": {
                "alpha": {"field": "alpha_api_key", "display_name": "Alpha"},
                "beta": {"field": "beta_api_key", "display_name": "Beta"},
            },
        }
        with tempfile.NamedTemporaryFile("w", delete=False) as f:
            json.dump(config, f)
            path = f.name
        try:
            merchant = load_merchant_config(path, "beta")
        finally:
            os.unlink(path)
        self.assertEqual(merchant.alias, "beta")
        self.assertEqual(merchant.display_name, "Beta")
        self.assertEqual(merchant.op_item, "Example Merchant API Keys")
        self.assertEqual(merchant.op_field, "beta_api_key")
        self.assertFalse(hasattr(merchant, "secret_value"))

    def test_resolve_default_merchant_alias_supports_env_config_and_single_merchant(self):
        config = {
            "default_merchant": "beta",
            "merchants": {
                "alpha": {"field": "alpha_api_key", "display_name": "Alpha"},
                "beta": {"field": "beta_api_key", "display_name": "Beta"},
            },
        }
        with tempfile.NamedTemporaryFile("w", delete=False) as f:
            json.dump(config, f)
            path = f.name
        try:
            self.assertEqual(resolve_default_merchant_alias(path, None), "beta")
            with patch.dict(os.environ, {"PAYMENT_EVIDENCE_MERCHANT": "alpha"}, clear=False):
                self.assertEqual(resolve_default_merchant_alias(path, None), "alpha")
            self.assertEqual(resolve_default_merchant_alias(path, "alpha"), "alpha")
        finally:
            os.unlink(path)

        single = {"merchants": {"only": {"field": "only_api_key"}}}
        with tempfile.NamedTemporaryFile("w", delete=False) as f:
            json.dump(single, f)
            single_path = f.name
        try:
            self.assertEqual(resolve_default_merchant_alias(single_path, None), "only")
        finally:
            os.unlink(single_path)


class QueryTests(unittest.TestCase):
    def test_build_query_params_requires_bounded_amount_lookup_window(self):
        with self.assertRaises(ValueError):
            build_query_params(security_key="secret", amount="42.50")

    def test_build_query_params_builds_transaction_lookup_without_dates(self):
        params = build_query_params(security_key="secret", transaction_id="12345")
        self.assertEqual(params["transaction_id"], "12345")
        self.assertEqual(params["result_limit"], "10")
        self.assertNotIn("start_date", params)

    def test_build_query_params_builds_order_lookup_without_dates(self):
        params = build_query_params(security_key="secret", order_id="ORDER-123")
        self.assertEqual(params["order_id"], "ORDER-123")
        self.assertNotIn("start_date", params)
        self.assertNotIn("end_date", params)

    def test_build_query_params_allows_one_sided_date_bounds_for_non_amount_search(self):
        start_only = build_query_params(security_key="secret", order_id="ORDER-123", start_date="20260601000000")
        end_only = build_query_params(security_key="secret", order_id="ORDER-123", end_date="20260630235959")

        self.assertEqual(start_only["start_date"], "20260601000000")
        self.assertNotIn("end_date", start_only)
        self.assertEqual(end_only["end_date"], "20260630235959")
        self.assertNotIn("start_date", end_only)

    def test_build_query_params_builds_amount_lookup_with_window(self):
        params = build_query_params(
            security_key="secret",
            amount="42.50",
            start_date="20260612000000",
            end_date="20260613000000",
        )
        self.assertEqual(params["action_type"], "sale")
        self.assertEqual(params["amount"], "42.50")
        self.assertEqual(params["start_date"], "20260612000000")
        self.assertEqual(params["end_date"], "20260613000000")

    def test_build_query_params_normalizes_thousands_separator_amount(self):
        params = build_query_params(
            security_key="secret",
            amount="9,329.31",
            start_date="20260510000000",
            end_date="20260510235959",
        )

        self.assertEqual(params["amount"], "9329.31")


class CandidateSearchTests(unittest.TestCase):
    def test_rank_candidate_transactions_matches_thousands_separator_amount(self):
        txn = parse_query_response(SAMPLE_XML)["transactions"][0]
        txn["actions"][0]["amount"] = "9329.31"
        txn["actions"][1]["amount"] = "9329.31"
        txn["actions"][0]["date"] = "20260510230813"
        txn["cc_number"] = "3xxxxxxxxxx7007"

        ranked = rank_candidate_transactions(
            [txn],
            amount="9,329.31",
            last_four="7007",
            start_date="20260510000000",
            end_date="20260510235959",
        )

        self.assertEqual(ranked["candidate_summary"]["candidate_count"], 1)
        self.assertEqual(ranked["candidates"][0]["amount"], "9329.31")


class ServiceRequestValidationTests(unittest.TestCase):
    def test_validate_search_request_normalizes_thousands_separator_amount(self):
        result = validate_search_request(
            {
                "start_date": "2026-05-10",
                "end_date": "2026-05-10",
                "amount": "9,329.31",
                "last_four": "7007",
            }
        )

        self.assertTrue(result.valid, result.errors)
        self.assertEqual(result.normalized["amount"], "9329.31")

    def test_validate_search_request_defaults_to_full_page_budget(self):
        result = validate_search_request({"start_date": "2026-04-01", "end_date": "2026-05-10", "last_four": "7007"})

        self.assertTrue(result.valid, result.errors)
        self.assertEqual(result.normalized["max_pages"], 25)

    def test_fastapi_search_request_defaults_to_full_page_budget(self):
        request = SearchRequest(start_date="2026-04-01", end_date="2026-05-10", last_four="7007")
        form = _search_form(request)
        args = _api_search_args(form, timeout=20)

        self.assertEqual(form["max_pages"], 25)
        self.assertEqual(args.max_pages, 25)


class CliTests(unittest.TestCase):
    def test_cli_help_is_available(self):
        completed = subprocess.run(
            [sys.executable, "-m", "payment_evidence.cli", "--help"],
            cwd=Path(__file__).resolve().parents[1],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(completed.returncode, 0)
        self.assertIn("transaction", completed.stdout)
        self.assertIn("amount", completed.stdout)
        self.assertIn("search", completed.stdout)
    def test_cli_blocks_internal_redaction_to_stdout(self):
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "payment_evidence.cli",
                "--merchant",
                "missing",
                "--redaction",
                "internal",
                "transaction",
                "--transaction-id",
                "12345",
            ],
            cwd=Path(__file__).resolve().parents[1],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(completed.returncode, 2)
        self.assertIn("internal redaction requires --detail-output", completed.stdout)


if __name__ == "__main__":
    unittest.main()
