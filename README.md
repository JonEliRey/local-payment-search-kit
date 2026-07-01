# Local Payment Search Kit

Local Payment Search Kit gives an authorized merchant a local browser app and a deterministic `payment-search` CLI for searching payment gateway transaction records from their own machine.

The human surface is the browser. The agent surface is the CLI/API. Agents should not scrape the browser.

## What this is

- A local Payment Search browser app.
- A browser setup wizard for human merchant credential setup.
- A deterministic `payment-search` CLI for AI-agent operation and setup fallback.
- Local-only config, secrets, and artifacts under `~/.payment-search/` by default.
- A clean, source-available distribution for authorized merchant-side use.

## What this is not

- It is not a hosted portal.
- It is not a public payment gateway proxy.
- It does not include merchant credentials.
- It does not transmit API keys in the project folder.
- It does not replace gateway, PCI, legal, or dispute-process judgment.

## Install

Unix/macOS:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e . pytest
```

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e . pytest
```

Or use the setup wrappers:

```bash
./scripts/setup-local-kit.sh
```

```powershell
.\scripts\setup-local-kit.ps1
```

## Verify before use

```bash
pytest -q
payment-search --help
payment-search add-merchant --help
payment-search start --help
```

## Add a merchant

Human-first path:

```bash
payment-search start
```

Open the local URL. If no merchant is configured, choose **Open setup wizard** or visit `/setup`. The browser setup wizard saves the merchant config locally and stores the raw API key only in the local secret store.

Agent/CLI fallback:

```bash
payment-search add-merchant
```

Agent/non-interactive:

```bash
printf '%s' "$MERCHANT_API_KEY" | payment-search add-merchant \
  --alias merchant-local \
  --display-name "Merchant Local" \
  --gateway nmi \
  --base-url https://mbcard.transactiongateway.com \
  --api-key-stdin
```

The generated config stores `local_secret_ref`, not the raw key. Secrets stay in the local secret store.

## Start the browser

```bash
payment-search start
```

Then open the local URL shown in the terminal and use Transaction Search.

If setup is incomplete, the browser starts anyway and shows setup-required guidance with a link to the browser setup wizard.

## Safety rules

Never commit, paste, or send:

- API keys;
- raw gateway payloads;
- cardholder data;
- generated private reports;
- private transaction details;
- local files under `~/.payment-search/`.

Use fake or synthetic credentials for test-only setup checks. Live gateway calls require valid authorization and an approved purpose.

## License

This project is source-available under Apache License 2.0 with the Commons Clause License Condition v1.0. That means you may inspect, use, modify, and share under the license terms, but you may not sell the software or a product/service whose value derives entirely or substantially from this software without a separate commercial license.

See `LICENSE` and `COMMERCIAL_USE.md`. This is not OSI-approved open source.