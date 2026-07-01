# Final UAT Notes — Browser Setup Wizard Branch

Date: 2026-07-01
Branch: `feat/browser-setup-wizard`

## Accepted human flow

1. Install or update the local kit.
2. Start the browser app with `payment-search start` or the double-click launcher.
3. Open `http://127.0.0.1:8787` unless an explicit alternate port was requested.
4. Add merchants through `/setup`.
5. Search authorized merchant transactions from the browser.
6. Use row-level **See detail** to generate local transaction detail artifacts.
7. Manage merchant records from `/setup` when entries need correction or removal.

## Final UAT findings resolved

- Windows launcher path exists through `START_LOCAL_KIT.bat`.
- Local browser setup wizard can add merchants without exposing raw API keys in config.
- `/setup` defaults to blank new-merchant values.
- Selecting **Add new merchant** clears existing merchant fields.
- Existing merchant updates require confirmation.
- Existing merchant removal is available and requires confirmation.
- Search page includes merchant navigation.
- Transaction detail/result artifact page includes merchant navigation.
- The merchant setup page does not link redundantly to itself.
- Theme selection carries across search and setup pages.
- Detail requests narrow to the selected transaction ID instead of passing conflicting lookup keys.
- Local browser port is stable by default at `8787`.
- Windows transaction detail artifact generation uses explicit UTF-8 file reads/writes.

## Windows-specific notes

If Windows shows `Gateway request failed` while WSL works, verify all of the following before treating it as a gateway outage:

1. Windows has pulled the latest `feat/browser-setup-wizard` branch.
2. The Windows app process was restarted after pulling.
3. Install / Update was rerun so the Windows `.venv` points at the updated editable checkout.
4. The browser is opening the current process at `http://127.0.0.1:8787` or the explicit port printed by the current command.

Older builds could fail while writing Unicode transaction detail HTML on Windows default code pages such as `cp1252`. Current code writes app-owned local text/JSON files with `encoding="utf-8"` explicitly.

## Agent support notes

Agents should use CLI/API commands, not browser scraping. For troubleshooting, distinguish:

- gateway retrieval failure;
- credential resolution failure;
- local config/secret-store state mismatch;
- stale Windows process or stale Windows `.venv`;
- local artifact rendering or file-write failure.

Do not print real credentials, raw gateway payloads, cardholder data, private transaction details, generated reports, or files under `~/.payment-search/`.

## Verification gate at acceptance

Latest verified local gate:

```bash
pytest -q
python3 -m compileall -q src tests scripts/local-kit-launcher.py
bash -n scripts/setup-local-kit.sh scripts/start-dashboard.sh START_LOCAL_KIT.command
git diff --check
```

Expected result at acceptance: all tests passing, currently 52 tests.
