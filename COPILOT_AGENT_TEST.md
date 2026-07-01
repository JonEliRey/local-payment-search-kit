# Copilot Agent Test

Use this as the VS Code GitHub Copilot agent smoke before sending the kit to an operator.

## Goal

Prove a coding agent can install, test, configure through the browser setup wizard with a fake key, and start Local Payment Search Kit without leaking secrets or making live gateway calls.

## Agent prompt

You are validating Local Payment Search Kit from a clean clone. Do not use real credentials. Do not perform live gateway calls. Use only fake test values.

Run these steps and report exact commands and results:

1. Create and activate a local virtual environment.
2. Install the package editable with test dependencies.
3. Run `pytest -q`.
4. Run:
   - `payment-search --help`
   - `payment-search add-merchant --help`
   - `payment-search start --help`
5. Create a temporary `PAYMENT_SEARCH_HOME`.
6. Start the browser with `payment-search start`.
7. Read the printed local URL. The normal default is `http://127.0.0.1:8787`. If this port is occupied in the test environment, stop the old process or rerun with an explicit alternate port such as `--port 8788`.
8. Fetch the root page and verify it contains `Setup required` and a link to `/setup`.
9. Fetch `/setup` and verify the browser setup wizard contains:
   - merchant alias field;
   - display name field;
   - gateway base URL field;
   - password-style API/security key field.
10. Submit the setup wizard with fake values only:

   ```text
   alias: browser-test
   display_name: Browser Test
   gateway: nmi
   base_url: https://mbcard.transactiongateway.com
   api_key: synthetic-browser-test-key
   ```

11. Verify:
   - setup redirects back to `/`;
   - `config.json` does not contain `synthetic-browser-test-key`;
   - `config.json` contains `local_secret_ref`;
   - the local secret store contains the fake key;
   - the root page no longer shows `Setup required`;
   - the root page includes the configured merchant alias.
12. Stop the local browser process.

Optional deterministic CLI fallback check:

```bash
printf '%s' 'synthetic-agent-test-key' | payment-search add-merchant \
  --alias agent-test \
  --display-name "Agent Test" \
  --gateway nmi \
  --base-url https://mbcard.transactiongateway.com \
  --api-key-stdin \
  --config-output "$TMPDIR/config.json" \
  --secret-store "$TMPDIR/secrets.json" \
  --pretty
```

Verify the CLI fallback also writes `local_secret_ref` without writing the raw key to config.

## Pass criteria

- Tests pass.
- Help commands work.
- Browser setup wizard works with fake key.
- Raw fake key is not printed or written to config.
- Browser starts locally.
- Setup-required guidance renders when no merchant config exists.
- Configured merchant appears after wizard submit.
- No live gateway call is attempted.
- Text/JSON artifacts and local config helpers use explicit UTF-8 encoding so Windows default code pages do not break transaction detail generation.
