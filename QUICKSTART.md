# Quickstart — Local Payment Search Kit

Use this path for a local, authorized Transaction Search setup.

## 1. Install or update the local kit

Unix/macOS:

```bash
./scripts/setup-local-kit.sh
```

Windows PowerShell:

```powershell
.\scripts\setup-local-kit.ps1
```

## 2. Add merchant credentials

Run the guided setup:

```bash
payment-search add-merchant
```

The command asks for:

- merchant alias;
- merchant display name;
- gateway, normally `nmi`;
- gateway base URL;
- merchant API/security key.

The API key is stored locally under `~/.payment-search/secrets.json`. The generated runtime config at `~/.payment-search/config.json` stores only a `local_secret_ref`, not the raw key.

An AI agent may use the deterministic form:

```bash
printf '%s' "$MERCHANT_API_KEY" | payment-search add-merchant \
  --alias merchant-local \
  --display-name "Merchant Local" \
  --gateway nmi \
  --base-url https://mbcard.transactiongateway.com \
  --api-key-stdin
```

## 3. Start the browser app

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

If no merchant has been configured yet, the browser shows:

```text
Setup required
Add your merchant API credentials before running Transaction Search.
Run:
payment-search add-merchant
```

## 4. Run the first authorized smoke

After valid credentials are configured, open the local browser URL, choose the merchant, enter a bounded date window expected to contain transactions, and run Transaction Search.

Do not paste API keys, raw gateway responses, cardholder data, generated reports, or private transaction details into chat or committed files.