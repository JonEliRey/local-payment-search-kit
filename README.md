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

Double-click launcher:

- Windows: double-click `START_LOCAL_KIT.bat`
- macOS/Linux desktop: double-click `START_LOCAL_KIT.command` where supported, or run it from Terminal

The launcher lets you choose Install / Update, Start Browser App, or Run Tests without copying commands.

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

By default, the browser app listens on:

```text
http://127.0.0.1:8787
```

Open the local URL shown in the terminal and use Transaction Search. If another local process already owns `8787`, stop that process or start this app with an explicit alternate port:

```bash
payment-search start --port 8788
```

If setup is incomplete, the browser starts anyway and shows setup-required guidance with a link to the browser setup wizard.

## Merchant management

Use the browser setup wizard at `/setup` to add, update, or remove local merchant entries. The setup page defaults to a blank new-merchant form. Selecting an existing merchant pre-fills its values for editing. Existing merchant updates and removals require confirmation before local config or secret-store changes are written.

Transaction search results include row-level **See detail** actions. Detail generation writes local UTF-8 HTML/JSON artifacts under `~/.payment-search/artifacts/` by default. The result page links back to **Search** and **Merchants** for follow-up work.

## Troubleshooting

- **Windows still shows `Gateway request failed` after pulling:** stop the old app process, run Install / Update again, then restart from the updated branch. The Windows artifact writer requires the UTF-8 fix in this branch.
- **Port changes every run:** update to the latest `feat/browser-setup-wizard` branch. `payment-search start` now uses stable local port `8787` by default.
- **Port `8787` is already in use:** stop the old process or pass `--port <other-port>` explicitly.
- **WSL works but Windows fails:** verify Windows has pulled the latest branch and is running its own updated `.venv`; WSL and Windows use separate local state and Python environments.

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