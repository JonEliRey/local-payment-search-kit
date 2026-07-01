# Agent Instructions

## Mission

This repository is the clean public working copy of Local Payment Search Kit.

The project provides:

- a local browser app for authorized human transaction search;
- a deterministic `payment-search` CLI for AI agents and setup support;
- local-only credential/config/artifact storage under `~/.payment-search/` by default.

## Operating boundaries

- Do not add real API keys, gateway credentials, customer data, cardholder data, raw gateway payloads, generated private reports, or private transaction details to the repo.
- Do not edit, print, or commit files under `~/.payment-search/`.
- Use fake or synthetic values for tests and documentation.
- Live gateway calls require valid authorization and explicit human approval.
- The browser is the human surface. Agents should use the CLI/API, not scrape the browser.
- Keep public docs generic. Do not add private client names, internal worktree paths, private UAT notes, or historical project strategy notes.

## Standard setup

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e . pytest
pytest -q
```

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e . pytest
pytest -q
```

## Required verification before claiming work is complete

Run:

```bash
pytest -q
python3 -m compileall -q src tests scripts/local-kit-launcher.py
bash -n scripts/setup-local-kit.sh scripts/start-dashboard.sh START_LOCAL_KIT.command
payment-search --help
payment-search add-merchant --help
payment-search start --help
```

For setup-flow changes, also run a fake-key smoke that proves the raw key is not written to config or printed to output.

For transaction-detail/artifact changes, include Windows-safe UTF-8 coverage. App-owned text and JSON reads/writes must pass `encoding="utf-8"` explicitly.

## CLI contract

Teach and use:

```bash
payment-search add-merchant
payment-search start
payment-search merchants --pretty
payment-search search --start-date YYYYMMDD000000 --end-date YYYYMMDD235959 --merchant <alias> --pretty
payment-search transaction --transaction-id <id> --merchant <alias> --pretty
```

Do not teach legacy command names in user-facing documentation.

## Licensing

This project is source-available under Apache License 2.0 with the Commons Clause License Condition v1.0. Do not describe it as OSI-approved open source.