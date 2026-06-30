# Agent Runbook — Local Payment Search Kit

The AI agent should use the deterministic Payment Search CLI/API. Do not scrape the browser.

## Allowed local commands

Use these for normal support:

```bash
payment-search add-merchant
payment-search start
payment-search merchants --pretty
payment-search search --start-date YYYYMMDD000000 --end-date YYYYMMDD235959 --merchant <alias> --pretty
payment-search transaction --transaction-id <id> --merchant <alias> --pretty
```

For non-interactive setup, read the API key from stdin or an environment variable. Never echo the key:

```bash
printf '%s' "$MERCHANT_API_KEY" | payment-search add-merchant \
  --alias merchant-local \
  --display-name "Merchant Local" \
  --gateway nmi \
  --base-url https://mbcard.transactiongateway.com \
  --api-key-stdin
```

## Operating rules

- Treat `~/.payment-search/config.json` as local runtime config.
- Treat `~/.payment-search/secrets.json` as local secret storage.
- The generated config should contain `local_secret_ref`, not raw keys.
- Do not print API keys, raw gateway payloads, full card data, generated private reports, or private transaction details into chat.
- Do not invent payment facts. Report only what the CLI/API returns.
- Live gateway calls require valid merchant authorization and an approved purpose.
- If the browser shows setup-required guidance, run or guide `payment-search add-merchant`.

## Human/browser flow

Humans use:

```bash
payment-search start
```

Then they open the local URL and use Transaction Search / Transaction Detail.

The CLI remains the agent-facing control surface; the browser remains the human-facing surface.