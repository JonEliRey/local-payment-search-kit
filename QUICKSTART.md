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

Do not paste API keys, raw gateway responses, cardholder data, generated reports, or private transaction details into chat or committed files.