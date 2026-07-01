# Quickstart — Local Payment Search Kit

Use this path for a local, authorized Transaction Search setup.

## 1. Install or update the local kit

Easiest path:

- Windows: double-click `START_LOCAL_KIT.bat`
- macOS/Linux desktop: double-click `START_LOCAL_KIT.command` where supported, or run it from Terminal

Choose **Install / Update** in the launcher.

Command-line path:

Unix/macOS:

```bash
./scripts/setup-local-kit.sh
```

Windows PowerShell:

```powershell
.\scripts\setup-local-kit.ps1
```

## 2. Start the browser app

In the launcher, choose **Start Browser App**.

Command-line path:

```bash
payment-search start
```

The stable default URL is:

```text
http://127.0.0.1:8787
```

If that port is already in use, stop the old app process or start with an explicit alternate port, for example `payment-search start --port 8788`.

Or use wrappers:

Unix/macOS:

```bash
./scripts/start-dashboard.sh
```

Windows PowerShell:

```powershell
.\scripts\start-dashboard.ps1
```

## 3. Add merchant credentials

Human-first browser path:

1. Open the local URL printed by `payment-search start`.
2. If no merchant is configured, click **Open setup wizard** or go to `/setup`.
3. Enter merchant alias, display name, gateway URL, and API/security key.
4. Submit the form and return to Transaction Search.

To manage merchants later, open `/setup` again. The page starts with blank values for a new merchant. Selecting an existing merchant pre-fills that merchant for editing. Updates and removals require confirmation.

The API key is stored locally under `~/.payment-search/secrets.json`. The generated runtime config at `~/.payment-search/config.json` stores only a `local_secret_ref`, not the raw key.

CLI fallback:

```bash
payment-search add-merchant
```

An AI agent may use the deterministic form:

```bash
printf '%s' "$MERCHANT_API_KEY" | payment-search add-merchant \
  --alias merchant-local \
  --display-name "Merchant Local" \
  --gateway nmi \
  --base-url https://mbcard.transactiongateway.com \
  --api-key-stdin
```

If no merchant has been configured yet, the browser shows a link to `/setup` plus CLI fallback guidance:

```text
Setup required
Add your merchant API credentials before running Transaction Search.
Open setup wizard
Run:
payment-search add-merchant
```

## 4. Run the first authorized smoke

After valid credentials are configured, open the local browser URL, choose the merchant, enter a bounded date window expected to contain transactions, and run Transaction Search.

Use **See detail** on the intended row to generate the local transaction detail artifact. On Windows, make sure you are on the latest branch before testing detail generation; older builds could fail while writing Unicode HTML artifacts and show `Gateway request failed`.

Do not paste API keys, raw gateway responses, cardholder data, generated reports, or private transaction details into chat or committed files.