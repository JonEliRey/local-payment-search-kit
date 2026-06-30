# Copilot Agent Test

Use this as the VS Code GitHub Copilot agent smoke before sending the kit to an operator.

## Goal

Prove a coding agent can install, test, configure with a fake key, and start Local Payment Search Kit without leaking secrets or making live gateway calls.

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
5. Create a temporary directory.
6. Add a fake merchant using stdin:

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

7. Verify:
   - the CLI output does not contain `synthetic-agent-test-key`;
   - `config.json` does not contain `synthetic-agent-test-key`;
   - `config.json` contains `local_secret_ref`;
   - the secret store can resolve the fake key locally.
8. Start the browser with a temporary `PAYMENT_SEARCH_HOME` and `--port 0`.
9. Read the printed local URL.
10. Fetch the root page and verify it contains `Setup required` if no merchant is configured in that temp home.
11. Stop the local browser process.

## Pass criteria

- Tests pass.
- Help commands work.
- Fake-key setup succeeds.
- Raw fake key is not printed or written to config.
- Browser starts locally.
- Setup-required guidance renders when no merchant config exists.
- No live gateway call is attempted.
