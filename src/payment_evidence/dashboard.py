from __future__ import annotations

import html
import json
import re
from typing import Any

from .redaction import SUMMARY_SENSITIVE_FIELDS, redact_transaction

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_EXTRA_SUMMARY_SENSITIVE_FIELDS = {
    "customer_id",
    "customerid",
    "billing_zip",
    "billing_postal_code",
    "postal_code",
    "zip",
}
_SENSITIVE_KEYS = SUMMARY_SENSITIVE_FIELDS | _EXTRA_SUMMARY_SENSITIVE_FIELDS


def render_dashboard_html(case_payload: dict[str, Any], *, title: str = "Transaction Detail") -> str:
    """Render a single-file, summary-safe, static HTML transaction detail page."""
    raw_transactions = [txn for txn in case_payload.get("transactions", []) if isinstance(txn, dict)]
    transactions = [redact_transaction(txn, mode="summary") for txn in raw_transactions]
    primary = transactions[0] if transactions else {}
    raw_primary = raw_transactions[0] if raw_transactions else {}
    history = _summary_safe(case_payload.get("history_summary") or {})
    merchant = _summary_safe(case_payload.get("merchant") or {})
    trace = _summary_safe(case_payload.get("trace") or {})
    search_context = _safe_search_context(case_payload.get("search_context") or {})
    search_context_json = json.dumps(search_context, sort_keys=True, separators=(",", ":")).replace("</", "<\\/")
    adjust_search_link = (
        '<a class="primary-btn" data-testid="adjust-search-link" href="/">Adjust search</a>'
        if search_context
        else ""
    )

    safe_title = _e(title)
    status = _display(case_payload.get("status") or "unknown")
    prior_transactions = _as_list(history.get("prior_transactions"))
    all_actions = _collect_actions(transactions)
    primary_amount = _money(primary, fallback_action=_first_action(primary))
    checklist = _evidence_checklist(transactions, history)

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{safe_title}</title>
  <script>
    (function () {{
      var storageKey = 'transactionSearchTheme';
      var stored = 'system';
      try {{ stored = localStorage.getItem(storageKey) || 'system'; }} catch (error) {{ stored = 'system'; }}
      if (['light', 'dark', 'system'].indexOf(stored) === -1) {{ stored = 'system'; }}
      var darkQuery = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)');
      var resolved = stored === 'system' ? (darkQuery && darkQuery.matches ? 'dark' : 'light') : stored;
      document.documentElement.setAttribute('data-theme-mode', stored);
      document.documentElement.setAttribute('data-theme', resolved);
    }}());
  </script>
  <style>
    :root {{ color-scheme: light dark; --ink:#142033; --muted:#64748b; --line:#dbe5f0; --panel:#ffffff; --soft:#f6f8fb; --table:#ffffff; --table-head:#f8fafc; --body-bg:radial-gradient(circle at top left, #dbeafe 0, transparent 30rem), linear-gradient(180deg, #f8fafc, #eef2f7); --hero-bg:linear-gradient(135deg, #0f172a, #1d4ed8 62%, #0f766e); --summary-bg:#eff6ff; --summary-line:#bfdbfe; --control-bg:#ffffff; --control-text:#142033; --copy-bg:#eff6ff; --copy-hover:#dbeafe; --copy-text:#1d4ed8; --badge-bg:#e2e8f0; --badge-text:#142033; --good-bg:#dcfce7; --good-text:#166534; --bad-bg:#fee2e2; --bad-text:#991b1b; --warn-bg:#fef3c7; --warn-text:#92400e; --no-match-bg:#fff7ed; --no-match-line:#fed7aa; --no-match-text:#9a3412; --mobile-row-line:#edf2f7; --focus:#f59e0b; --brand:#2458d3; --brand2:#0f766e; --warn:#b45309; --bad:#b91c1c; --shadow:0 18px 40px rgba(15,23,42,.10); }}
    :root[data-theme="dark"] {{ color-scheme: dark; --ink:#f8fafc; --muted:#94a3b8; --line:#263244; --panel:#111827; --soft:#0f172a; --table:#0b1220; --table-head:#111827; --body-bg:radial-gradient(circle at top left, #172554 0, transparent 28rem), linear-gradient(180deg, #070b14, #0f172a); --hero-bg:linear-gradient(135deg, #020617, #172554 62%, #0f766e); --summary-bg:#10213d; --summary-line:#1e3a8a; --control-bg:#0b1220; --control-text:#f8fafc; --copy-bg:#172554; --copy-hover:#1e3a8a; --copy-text:#bfdbfe; --badge-bg:#172554; --badge-text:#dbeafe; --good-bg:#052e16; --good-text:#bbf7d0; --bad-bg:#450a0a; --bad-text:#fecaca; --warn-bg:#451a03; --warn-text:#fed7aa; --no-match-bg:#451a03; --no-match-line:#92400e; --no-match-text:#fed7aa; --mobile-row-line:#263244; --focus:#93c5fd; --shadow:0 22px 44px rgba(0,0,0,.34); }}
    :root[data-theme="light"] {{ color-scheme: light; }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; color: var(--ink); background: var(--body-bg); }}
    main {{ width: min(1180px, calc(100% - 32px)); margin: 0 auto 48px; }}
    .page-tools {{ width:min(1180px, calc(100% - 32px)); margin:14px auto -10px; display:flex; justify-content:flex-end; align-items:center; gap:10px; flex-wrap:wrap; }}
    .hero {{ margin: 24px auto 18px; padding: 28px; border-radius: 26px; background: var(--hero-bg); color: white; box-shadow: var(--shadow); }}
    .eyebrow {{ margin: 0 0 8px; text-transform: uppercase; letter-spacing: .14em; font-size: .76rem; opacity: .78; font-weight: 800; }}
    h1 {{ margin: 0; font-size: clamp(1.8rem, 4vw, 3.2rem); line-height: 1.05; }}
    h2 {{ margin: 0 0 14px; font-size: 1.15rem; }}
    h3 {{ margin: 18px 0 10px; font-size: 1rem; }}
    .hero-meta {{ display:flex; gap:10px; flex-wrap:wrap; margin-top:18px; }}
    .pill {{ display:inline-flex; align-items:center; gap:6px; padding:7px 11px; border-radius:999px; background:rgba(255,255,255,.14); border:1px solid rgba(255,255,255,.18); font-weight:750; }}
    .kpi-grid {{ display:grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap:14px; margin: 18px 0; }}
    .kpi-card, section {{ background: var(--panel); border: 1px solid var(--line); border-radius: 20px; box-shadow: 0 9px 24px rgba(15, 23, 42, .06); }}
    .kpi-card {{ padding: 18px; }}
    .kpi-label, .label {{ color: var(--muted); text-transform: uppercase; letter-spacing: .08em; font-size: .72rem; font-weight: 800; }}
    .kpi-value {{ margin-top: 8px; font-size: 1.55rem; line-height: 1; font-weight: 850; }}
    .kpi-note {{ margin-top: 8px; color: var(--muted); font-size: .88rem; }}
    section {{ padding: 20px; margin: 16px 0; }}
    .section-head {{ display:flex; justify-content:space-between; align-items:flex-start; gap:16px; flex-wrap:wrap; margin-bottom:14px; }}
    .facts-grid, .adaptive-card-grid {{ display:grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 20rem), 1fr)); gap: 12px; align-items:stretch; }}
    .fact {{ padding: 13px; border: 1px solid var(--line); border-radius: 14px; background: var(--soft); min-width:0; }}
    .theme-control {{ display:flex; align-items:center; gap:8px; padding:7px 10px; border-radius:12px; background:rgba(255,255,255,.14); border:1px solid rgba(255,255,255,.18); }}
    .theme-control label {{ color:white; font-size:.78rem; font-weight:850; text-transform:uppercase; letter-spacing:.08em; }}
    .theme-control select {{ min-height:34px; border-radius:9px; border:1px solid rgba(255,255,255,.25); background:rgba(2,6,23,.34); color:white; font:inherit; font-weight:750; padding:4px 8px; }}
    :focus-visible {{ outline:3px solid var(--focus); outline-offset:3px; }}
    .value {{ margin-top: 5px; font-weight: 780; overflow-wrap:anywhere; }}
    .muted {{ color: var(--muted); }}
    .summary-card {{ padding: 15px; border-radius: 16px; background: var(--summary-bg); border: 1px solid var(--summary-line); }}
    .transaction-summary {{ border-color: var(--summary-line); }}
    .toolbar {{ display:flex; gap:10px; flex-wrap:wrap; align-items:center; }}
    .primary-btn {{ cursor:pointer; border:1px solid var(--line); background:var(--brand); color:white; border-radius:12px; padding:9px 12px; font-weight:850; text-decoration:none; display:inline-flex; align-items:center; min-height:34px; }}
    .no-match {{ display:none; margin-top:10px; padding:12px; border-radius:12px; background:var(--no-match-bg); border:1px solid var(--no-match-line); color:var(--no-match-text); font-weight:750; }}
    input[type="search"] {{ width:min(360px, 100%); padding: 10px 12px; border:1px solid var(--line); border-radius: 12px; font: inherit; background: var(--control-bg); color: var(--control-text); }}
    .table-wrap {{ overflow:auto; border:1px solid var(--line); border-radius: 16px; background:var(--table); }}
    table {{ width:100%; border-collapse: collapse; min-width: 720px; }}
    th, td {{ padding: 12px 14px; text-align:left; border-bottom:1px solid var(--line); vertical-align:top; }}
    th {{ position:sticky; top:0; background:var(--table-head); color:var(--ink); font-size:.78rem; text-transform:uppercase; letter-spacing:.06em; }}
    tr:last-child td {{ border-bottom:0; }}
    .status {{ display:inline-flex; padding:4px 9px; border-radius:999px; background:var(--badge-bg); color:var(--badge-text); font-weight:750; font-size:.84rem; }}
    .status.good {{ background:var(--good-bg); color:var(--good-text); }} .status.bad {{ background:var(--bad-bg); color:var(--bad-text); }} .status.warn {{ background:var(--warn-bg); color:var(--warn-text); }}
    .copy-btn {{ cursor:pointer; border:1px solid var(--line); background:var(--copy-bg); color:var(--copy-text); border-radius:10px; padding:7px 10px; font-weight:800; }}
    .copy-btn:hover {{ background:var(--copy-hover); }}
    ul.checklist, ul.limitations {{ padding-left:0; list-style:none; display:grid; gap:10px; }}
    ul.checklist li, ul.limitations li {{ padding:11px 12px; border-radius: 14px; background:var(--soft); border:1px solid var(--line); }}
    ul.checklist li::before {{ content:"✓"; color:var(--brand2); font-weight:900; margin-right:8px; }}
    ul.limitations li::before {{ content:"!"; color:var(--warn); font-weight:900; margin-right:8px; }}
    .empty {{ padding:18px; color:var(--muted); background:var(--soft); border:1px dashed var(--line); border-radius:14px; }}
    @media (max-width: 850px) {{ .kpi-grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }} }}
    @media (max-width: 680px) {{
      main {{ width:100%; padding:0 10px 28px; margin:0; }}
      .page-tools {{ width:100%; margin:10px 0 -6px; padding:0 10px; }}
      .hero {{ margin:10px 0 12px; padding:18px; border-radius:18px; }}
      .hero-meta, .toolbar {{ align-items:stretch; }}
      .pill, .primary-btn, input[type="search"] {{ width:100%; justify-content:space-between; }}
      section {{ padding:14px; border-radius:16px; margin:12px 0; }}
      .kpi-grid, .facts-grid, .adaptive-card-grid {{ grid-template-columns:minmax(0, 1fr); gap:10px; }}
      .table-wrap {{ overflow:visible; border:0; background:transparent; }}
      table, thead, tbody, tr, th, td {{ display:block; min-width:0; width:100%; }}
      thead {{ display:none; }}
      tr[data-filter-group] {{ margin:0 0 12px; padding:10px 12px; border:1px solid var(--line); border-radius:14px; background:var(--table); box-shadow:0 4px 12px rgba(15,23,42,.05); }}
      td {{ display:grid; grid-template-columns:minmax(7.5rem, 38%) minmax(0, 1fr); gap:10px; padding:9px 0; border-bottom:1px solid var(--mobile-row-line); overflow-wrap:anywhere; }}
      td:last-child {{ border-bottom:0; }}
      td::before {{ content:attr(data-label); color:var(--muted); text-transform:uppercase; letter-spacing:.06em; font-size:.72rem; font-weight:850; }}
    }}
    @media (max-width: 560px) {{ main {{ width:100%; }} .hero {{ padding:20px; border-radius:20px; }} .kpi-grid {{ grid-template-columns: 1fr; }} }}
    @media print {{ :root, :root[data-theme="dark"], :root[data-theme="light"] {{ color-scheme: light; --ink:#111827; --muted:#475569; --line:#d1d5db; --panel:#ffffff; --soft:#f8fafc; --table:#ffffff; --table-head:#f3f4f6; --body-bg:white; --summary-bg:#eff6ff; --summary-line:#bfdbfe; }} body {{ background:white; }} main {{ width:100%; margin:0; }} .hero, .kpi-card, section {{ box-shadow:none; }} .toolbar, .copy-btn, .theme-control {{ display:none !important; }} th {{ position:static; }} section {{ break-inside:avoid; }} }}
  </style>
</head>
<body>
<div class="page-tools">
  <a class="primary-btn" data-testid="new-search-link" href="/">New search</a>
  {adjust_search_link}
  <a class="primary-btn" data-testid="merchant-management-link" href="/setup">Merchants</a>
  <button class="primary-btn" type="button" onclick="printDashboard()">Print transaction detail</button>
  <div class="theme-control" role="group" aria-label="Theme">
    <label for="themeSelect">Theme</label>
    <select id="themeSelect" aria-label="Theme">
      <option value="system">System</option>
      <option value="light">Light</option>
      <option value="dark">Dark</option>
    </select>
  </div>
</div>
<main>
  <header class="hero">
    <p class="eyebrow">Transaction Detail</p>
    <h1>{safe_title}</h1>
    <div class="hero-meta">
      <span class="pill">Status: {_e(status)}</span>
      <span class="pill">Merchant: {_e(_merchant_name(merchant))}</span>
      <span class="pill">Transaction: {_copyable(primary.get('transaction_id', 'Not available'))}</span>
    </div>
  </header>

  <section class="transaction-summary" data-testid="transaction-summary">
    <div class="section-head"><div><h2>Transaction summary</h2><p class="muted">Plain-English transaction readout for nontechnical users. Sensitive details remain hidden.</p></div></div>
    <div class="summary-card"><strong>{_e(_evidence_summary(primary, history))}</strong></div>
  </section>

  <div class="kpi-grid" aria-label="Case KPIs">
    {_kpi("Case status", status, "Gateway lookup/result state")}
    {_kpi("Primary amount", primary_amount, "Summary-safe transaction amount")}
    {_kpi("Prior matches", history.get("prior_transaction_count", 0), f"Confidence: {_display(history.get('match_confidence', 'unknown'))}")}
    {_kpi("Prior settled", history.get("prior_settled_count", 0), f"Failed/declined: {_display(history.get('failed_count', 0))}")}
    {_kpi("Refunded/voided", history.get("refunded_or_voided_count", 0), "Prior transactions with reversal activity")}
  </div>

  <section data-testid="case-header">
    <div class="section-head"><div><h2>Case header</h2><p class="muted">Summary-safe identifiers for the analyst and client-facing packet.</p></div></div>
    <div class="facts-grid adaptive-card-grid" data-layout="adaptive-card-grid">
      {_fact("Detail title", title)}
      {_fact("Merchant", _merchant_name(merchant))}
      {_fact("Status", status)}
      {_fact("Trace ID", trace.get("correlation_id") or trace.get("request_id") or "Not provided")}
      {_fact("Primary transaction", primary.get("transaction_id") or "Not available", copy=True)}
      {_fact("Order ID", primary.get("order_id") or "Not available", copy=True)}
    </div>
  </section>

  <section data-testid="transaction-facts">
    <div class="section-head"><div><h2>Transaction facts</h2><p class="muted">Sensitive cardholder/contact fields are removed from this detail page.</p></div></div>
    {_payment_facts(primary, raw_primary)}
  </section>

  {_transaction_detail_sections(raw_primary)}

  <section data-testid="action-timeline">
    <div class="section-head">
      <div><h2>Action timeline</h2><p class="muted">Authorize, sale, settlement, refund, void, or decline events returned by the gateway.</p></div>
      <div class="toolbar"><label class="label" for="actionSearch">Filter actions</label><input id="actionSearch" type="search" placeholder="Search action, date, amount..." data-filter-target="actionRows"></div>
    </div>
    {_actions_table(all_actions)}
  </section>

  <section data-testid="history-summary">
    <div class="section-head"><div><h2>Same-customer history</h2><p class="muted">Aggregate pattern summary only; no raw email, billing ZIP, or customer identifier is rendered.</p></div></div>
    <div class="summary-card"><strong>{_e(history.get("pattern_summary") or "No same-customer history summary attached.")}</strong></div>
    <div class="facts-grid adaptive-card-grid" data-layout="adaptive-card-grid" style="margin-top:12px">
      {_fact("Prior transaction count", history.get("prior_transaction_count", 0))}
      {_fact("Prior settled", history.get("prior_settled_count", 0))}
      {_fact("Failed/declined", history.get("failed_count", 0))}
      {_fact("Refunded/voided", history.get("refunded_or_voided_count", 0))}
      {_fact("Total prior amount", _money(history, amount_key="total_prior_amount"))}
      {_fact("Match confidence", history.get("match_confidence") or "unknown")}
      {_fact("Match keys used", _match_keys_label(history.get("match_keys_used")))}
    </div>
  </section>

  <section data-testid="prior-transactions">
    <div class="section-head">
      <div><h2>Prior transactions</h2><p class="muted">Summary-safe prior same-customer transactions from the selected lookback window.</p></div>
      <div class="toolbar"><label class="label" for="priorSearch">Filter prior transactions</label><input id="priorSearch" type="search" placeholder="Search transaction, order, status..." data-filter-target="priorRows"></div>
    </div>
    {_prior_table(prior_transactions)}
  </section>

  <section data-testid="transaction-checklist">
    <div class="section-head"><div><h2>Transaction checklist</h2><p class="muted">What this gateway transaction lookup establishes and what still needs CRM or merchant follow-up.</p></div></div>
    <ul class="checklist">{"".join(f"<li>{_e(item)}</li>" for item in checklist)}</ul>
  </section>

</main>
<script type="application/json" id="searchContext">{search_context_json}</script>
<script>
(function () {{
  function normalized(text) {{ return (text || '').toLowerCase(); }}
  window.filterRows = function filterRows(input) {{
    var targetId = input.getAttribute('data-filter-target');
    var query = normalized(input.value);
    var visible = 0;
    document.querySelectorAll('[data-filter-group="' + targetId + '"]').forEach(function(row) {{
      row.hidden = query && normalized(row.textContent).indexOf(query) === -1;
      if (!row.hidden) {{ visible += 1; }}
    }});
    document.querySelectorAll('[data-empty-for="' + targetId + '"]').forEach(function(empty) {{
      empty.style.display = visible === 0 ? 'block' : 'none';
    }});
  }};
  window.printDashboard = function printDashboard() {{ window.print(); }};
  var adjustSearchLink = document.querySelector('[data-testid="adjust-search-link"]');
  if (adjustSearchLink) {{
    adjustSearchLink.addEventListener('click', function() {{
      var contextNode = document.getElementById('searchContext');
      if (contextNode && contextNode.textContent) {{
        try {{ sessionStorage.setItem('transactionSearchContext', contextNode.textContent); }} catch (error) {{ /* ignore storage failures */ }}
      }}
    }});
  }}
  function applyTheme(mode) {{
    var darkQuery = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)');
    var resolved = mode === 'system' ? (darkQuery && darkQuery.matches ? 'dark' : 'light') : mode;
    document.documentElement.setAttribute('data-theme-mode', mode);
    document.documentElement.setAttribute('data-theme', resolved);
    var select = document.getElementById('themeSelect');
    if (select) {{ select.value = mode; }}
  }}
  var themeSelect = document.getElementById('themeSelect');
  var initialTheme = document.documentElement.getAttribute('data-theme-mode') || 'system';
  applyTheme(initialTheme);
  if (themeSelect) {{
    themeSelect.addEventListener('change', function() {{
      var mode = themeSelect.value;
      try {{ localStorage.setItem('transactionSearchTheme', mode); }} catch (error) {{ /* ignore storage failures */ }}
      applyTheme(mode);
    }});
  }}
  if (window.matchMedia) {{
    var media = window.matchMedia('(prefers-color-scheme: dark)');
    media.addEventListener && media.addEventListener('change', function() {{
      if ((document.documentElement.getAttribute('data-theme-mode') || 'system') === 'system') {{ applyTheme('system'); }}
    }});
  }}
  document.querySelectorAll('input[data-filter-target]').forEach(function(input) {{
    input.addEventListener('input', function() {{ window.filterRows(input); }});
  }});
  document.querySelectorAll('[data-copy-value]').forEach(function(button) {{
    button.addEventListener('click', function() {{
      var value = button.getAttribute('data-copy-value') || '';
      if (navigator.clipboard && navigator.clipboard.writeText) {{
        navigator.clipboard.writeText(value);
      }} else {{
        var fallback = document.createElement('textarea');
        fallback.value = value;
        fallback.setAttribute('readonly', '');
        fallback.style.position = 'fixed';
        fallback.style.left = '-9999px';
        document.body.appendChild(fallback);
        fallback.select();
        try {{ document.execCommand('copy'); }} catch (error) {{ /* ignore copy permission failures */ }}
        document.body.removeChild(fallback);
      }}
      button.textContent = 'Copied';
      window.setTimeout(function() {{ button.textContent = 'Copy'; }}, 1200);
    }});
  }});
}}());
</script>
</body>
</html>
"""


def _safe_search_context(raw_context: dict[str, Any]) -> dict[str, str]:
    allowed = {
        "merchant_id",
        "merchant",
        "start_date",
        "end_date",
        "amount",
        "order_id",
        "transaction_id",
        "last_four",
        "result_limit",
    }
    safe: dict[str, str] = {}
    if not isinstance(raw_context, dict):
        return safe
    for key in allowed:
        value = raw_context.get(key)
        if value in (None, "", [], {}):
            continue
        text = str(value).strip()
        if text:
            safe[key] = text[:120]
    return safe


def _payment_facts(txn: dict[str, Any], raw_txn: dict[str, Any]) -> str:
    if not txn:
        return '<div class="empty">No transaction records present.</div>'
    fields = [
        ("Transaction ID", "transaction_id", True),
        ("Order ID", "order_id", True),
        ("Condition", "condition", False),
        ("Type", "transaction_type", False),
        ("Amount", None, False),
        ("Currency", "currency", False),
        ("Card brand", "cc_type", False),
        ("Masked card", None, False),
        ("Gateway response", "response_text", False),
    ]
    cards = []
    for label, key, copy in fields:
        if label == "Amount":
            value = _money(txn, fallback_action=_first_action(txn))
        elif label == "Masked card":
            value = _safe_card_label(raw_txn)
        else:
            value = txn.get(key or "")
        if value in (None, "", [], {}):
            continue
        cards.append(_fact(label, value, copy=copy))
    return f'<div class="facts-grid adaptive-card-grid" data-layout="adaptive-card-grid">{"".join(cards)}</div>' if cards else '<div class="empty">No summary-safe transaction facts present.</div>'


def _transaction_detail_sections(raw_txn: dict[str, Any]) -> str:
    if not raw_txn:
        return ""
    sections = [
        _detail_section(
            "Transaction information",
            "Gateway transaction-detail fields for CRM follow-up and review.",
            [
                ("Merchant transaction ID", _first_present(raw_txn, "merchant_transaction_id", "merchant_txn_id", "order_id")),
                ("Transaction ID", raw_txn.get("transaction_id")),
                ("Entry method", _first_present(raw_txn, "entry_method", "source", "payment_source")),
                ("Response code", _first_present(raw_txn, "response_code", "response")),
                ("Response text", raw_txn.get("response_text")),
                ("Date", _transaction_date(raw_txn)),
                ("Transaction type", raw_txn.get("transaction_type")),
                ("Status", raw_txn.get("condition") or raw_txn.get("status")),
            ],
        ),
        _detail_section(
            "Credit card information",
            "Masked/safe card details only. Full PAN and CVV values are never rendered.",
            [
                ("CC number", _safe_card_label(raw_txn)),
                ("CC type", raw_txn.get("cc_type")),
                ("CVV status", _first_present(raw_txn, "cvv_response", "cvv_status", "csc_response")),
                ("CC expiration", _format_expiration(raw_txn.get("cc_exp") or raw_txn.get("cc_expiration"))),
                ("AVS status", _first_present(raw_txn, "avs_response", "avs_status")),
                ("Auth code", _first_present(raw_txn, "auth_code", "authorization_code", "authcode")),
            ],
        ),
        _detail_section(
            "Billing information",
            "Billing name and address returned by the gateway for transaction review.",
            [
                ("Name", _full_name(raw_txn, "")),
                ("Company", raw_txn.get("company")),
                ("Address line 1", raw_txn.get("address_1")),
                ("Address line 2", raw_txn.get("address_2")),
                ("City / state / postal", _city_state_postal(raw_txn, prefix="")),
                ("Country", raw_txn.get("country")),
                ("Phone", raw_txn.get("phone")),
                ("Email", raw_txn.get("email")),
            ],
        ),
        _detail_section(
            "Shipping information",
            "Shipping details are shown when the gateway returns them.",
            [
                ("Name", _full_name(raw_txn, "shipping_")),
                ("Company", raw_txn.get("shipping_company")),
                ("Address line 1", raw_txn.get("shipping_address_1")),
                ("Address line 2", raw_txn.get("shipping_address_2")),
                ("City / state / postal", _city_state_postal(raw_txn, prefix="shipping_")),
                ("Country", raw_txn.get("shipping_country")),
                ("Phone", raw_txn.get("shipping_phone")),
                ("Email", raw_txn.get("shipping_email")),
                ("Tracking number", raw_txn.get("tracking_number")),
            ],
        ),
    ]
    return "".join(section for section in sections if section)


def _detail_section(title: str, note: str, fields: list[tuple[str, Any]]) -> str:
    cards = [_fact(label, value) for label, value in fields if value not in (None, "", [], {})]
    if not cards:
        return ""
    test_id = _e(title.lower().replace(" ", "-"), quote=True)
    return f"""
  <section data-testid="{test_id}">
    <div class="section-head"><div><h2>{_e(title)}</h2><p class="muted">{_e(note)}</p></div></div>
    <div class="facts-grid adaptive-card-grid" data-layout="adaptive-card-grid">{"".join(cards)}</div>
  </section>
"""


def _actions_table(actions: list[dict[str, Any]]) -> str:
    if not actions:
        return '<div class="empty">No gateway actions were present in the case file.</div>'
    rows = []
    for action in actions:
        rows.append(
            "<tr data-filter-group=\"actionRows\">"
            f"<td data-label=\"Date\">{_e(_format_date(action.get('date')))}</td>"
            f"<td data-label=\"Action\">{_e(action.get('action_type') or 'Unknown')}</td>"
            f"<td data-label=\"Success\">{_status(action.get('success'))}</td>"
            f"<td data-label=\"Amount\">{_e(_money(action))}</td>"
            f"<td data-label=\"Code\">{_e(action.get('response_code') or '')}</td>"
            f"<td data-label=\"Response\">{_e(action.get('response_text') or '')}</td>"
            "</tr>"
        )
    return '<div class="table-wrap"><table><thead><tr><th>Date</th><th>Action</th><th>Success</th><th>Amount</th><th>Code</th><th>Response</th></tr></thead><tbody>' + "".join(rows) + '</tbody></table></div><div class="no-match" data-empty-for="actionRows">No matching records found.</div>'


def _prior_table(prior_transactions: list[Any]) -> str:
    safe_prior = [_summary_safe(txn) for txn in prior_transactions if isinstance(txn, dict)]
    if not safe_prior:
        return '<div class="empty">No prior transactions were attached for this case.</div>'
    rows = []
    for txn in safe_prior:
        first_action = _as_list(txn.get("actions"))[0] if _as_list(txn.get("actions")) else {}
        first_action_date = first_action.get("date") if isinstance(first_action, dict) else None
        rows.append(
            "<tr data-filter-group=\"priorRows\">"
            f"<td data-label=\"Date\">{_e(_format_date(first_action_date))}</td>"
            f"<td data-label=\"Transaction\">{_copyable(txn.get('transaction_id') or 'Not available')}</td>"
            f"<td data-label=\"Order\">{_e(txn.get('order_id') or '')}</td>"
            f"<td data-label=\"Condition\">{_condition_badge(txn.get('condition'))}</td>"
            f"<td data-label=\"Type\">{_e(txn.get('transaction_type') or '')}</td>"
            f"<td data-label=\"Amount\">{_e(_money(txn, fallback_action=first_action if isinstance(first_action, dict) else None))}</td>"
            f"<td data-label=\"Actions\">{_e(txn.get('action_count') if txn.get('action_count') is not None else len(_as_list(txn.get('actions'))))}</td>"
            f"<td data-label=\"Latest/first action\">{_e(first_action.get('action_type') if isinstance(first_action, dict) else '')}</td>"
            "</tr>"
        )
    return '<div class="table-wrap"><table><thead><tr><th>Date</th><th>Transaction</th><th>Order</th><th>Condition</th><th>Type</th><th>Amount</th><th>Actions</th><th>Latest/first action</th></tr></thead><tbody>' + "".join(rows) + '</tbody></table></div><div class="no-match" data-empty-for="priorRows">No matching records found.</div>'


def _collect_actions(transactions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    for txn in transactions:
        for action in _as_list(txn.get("actions")):
            if isinstance(action, dict):
                safe_action = _summary_safe(action)
                if isinstance(safe_action, dict):
                    if txn.get("currency") and not safe_action.get("currency"):
                        safe_action["currency"] = txn.get("currency")
                    actions.append(safe_action)
    return actions


def _summary_safe(value: Any) -> Any:
    if isinstance(value, list):
        return [_summary_safe(item) for item in value]
    if isinstance(value, dict):
        output: dict[str, Any] = {}
        for key, item in value.items():
            if str(key).lower() in _SENSITIVE_KEYS:
                continue
            safe_item = _summary_safe(item)
            if safe_item in (None, "", [], {}):
                continue
            output[key] = safe_item
        return output
    if isinstance(value, str):
        return _EMAIL_RE.sub("[email redacted]", value)
    return value


def _evidence_summary(primary: dict[str, Any], history: dict[str, Any]) -> str:
    condition = _display(primary.get("condition")).lower()
    amount = _money(primary, fallback_action=_first_action(primary))
    if condition in {"complete", "settled", "approved"}:
        opening = f"Transaction approved and settled for {amount}."
    elif condition in {"failed", "declined", "error"}:
        opening = f"Transaction was not successful; current status is {_display(primary.get('condition')) or 'unknown'}."
    else:
        opening = f"Transaction status is {_display(primary.get('condition')) or 'unknown'} for {amount}."
    prior_count = _display(history.get("prior_transaction_count", 0))
    settled = _display(history.get("prior_settled_count", 0))
    failed = _display(history.get("failed_count", 0))
    refunded = _display(history.get("refunded_or_voided_count", 0))
    return (
        f"{opening} Matching customer history shows {prior_count} prior transaction(s): "
        f"{settled} settled, {failed} failed or declined, and {refunded} refunded or voided. "
        "Use these gateway transaction details as inputs for later CRM documentation gathering and merchant follow-up."
    )


def _evidence_checklist(transactions: list[dict[str, Any]], history: dict[str, Any]) -> list[str]:
    return [
        "Transaction lookup result captured" if transactions else "Transaction lookup result not attached",
        "Authorization/settlement action timeline reviewed" if _collect_actions(transactions) else "No gateway action timeline returned",
        "Summary-safe redaction applied",
        "Same-customer history reviewed" if history else "Same-customer history not attached",
        "Prior transaction table reviewed" if _as_list(history.get("prior_transactions")) else "No prior transaction rows attached",
        "CRM or merchant records remain outside this transaction lookup and must be gathered separately",
    ]


def _fact(label: str, value: Any, *, copy: bool = False) -> str:
    return f'<div class="fact"><div class="label">{_e(label)}</div><div class="value">{_copyable(value) if copy else _e(_display(value))}</div></div>'


def _kpi(label: str, value: Any, note: str) -> str:
    return f'<article class="kpi-card"><div class="kpi-label">{_e(label)}</div><div class="kpi-value">{_e(_display(value))}</div><div class="kpi-note">{_e(note)}</div></article>'


def _copyable(value: Any) -> str:
    display = _display(value)
    if display in {"", "Not available", "Not provided"}:
        return _e(display)
    escaped = _e(display)
    attr = _e(display, quote=True)
    return f'<span>{escaped}</span> <button class="copy-btn" type="button" data-copy-value="{attr}" aria-label="Copy {attr}">Copy</button>'


def _status(success: Any) -> str:
    text = _display(success)
    if str(success) == "1" or str(success).lower() in {"true", "yes", "success", "approved"}:
        return '<span class="status good">Success</span>'
    if str(success) == "0" or str(success).lower() in {"false", "no", "failed", "declined"}:
        return '<span class="status bad">Failed</span>'
    return f'<span class="status warn">{_e(text or "Unknown")}</span>'


def _condition_badge(condition: Any) -> str:
    text = _display(condition or "unknown")
    lowered = text.lower()
    klass = "good" if lowered in {"complete", "settled", "approved"} else "bad" if lowered in {"failed", "declined", "error"} else "warn"
    return f'<span class="status {klass}">{_e(text)}</span>'


def _first_action(txn: dict[str, Any]) -> dict[str, Any] | None:
    for action in _as_list(txn.get("actions")):
        if isinstance(action, dict):
            return action
    return None


def _money(value: dict[str, Any], *, amount_key: str = "amount", fallback_action: dict[str, Any] | None = None) -> str:
    amount = value.get(amount_key)
    if amount in (None, "", [], {}) and fallback_action:
        amount = fallback_action.get(amount_key)
    if amount in (None, "", [], {}):
        # Some NMI payloads only expose amounts at action level; leave blank rather than inventing.
        return "Not provided"
    currency = value.get("currency") or (fallback_action or {}).get("currency")
    return f"{amount} {currency}" if currency else str(amount)


def _format_date(value: Any) -> str:
    raw = str(value or "").strip()
    if len(raw) == 14 and raw.isdigit():
        return f"{raw[0:4]}-{raw[4:6]}-{raw[6:8]} {raw[8:10]}:{raw[10:12]}:{raw[12:14]} UTC"
    return raw or "Not provided"


def _safe_card_label(txn: dict[str, Any]) -> str:
    card_type = _display(txn.get("cc_type") or "Card").title()
    raw = str(txn.get("cc_number") or "").strip()
    if not raw:
        return ""
    digits = "".join(ch for ch in raw if ch.isdigit())
    last4 = digits[-4:] if len(digits) >= 4 else ""
    return f"{card_type} ****{last4}" if last4 else f"{card_type} masked"


def _first_present(txn: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = txn.get(key)
        if value not in (None, "", [], {}):
            return value
    return ""


def _transaction_date(txn: dict[str, Any]) -> str:
    direct = _first_present(txn, "date", "transaction_date", "created_at")
    if direct:
        return _format_date(direct)
    for action in _as_list(txn.get("actions")):
        if isinstance(action, dict) and action.get("date"):
            return _format_date(action.get("date"))
    return ""


def _format_expiration(value: Any) -> str:
    raw = "".join(ch for ch in str(value or "") if ch.isdigit())
    if len(raw) == 4:
        return f"{raw[:2]}/{raw[2:]}"
    if len(raw) == 6:
        return f"{raw[:2]}/{raw[2:]}"
    return str(value or "")


def _full_name(txn: dict[str, Any], prefix: str) -> str:
    first = str(txn.get(f"{prefix}first_name") or "").strip()
    last = str(txn.get(f"{prefix}last_name") or "").strip()
    return " ".join(part for part in [first, last] if part)


def _city_state_postal(txn: dict[str, Any], *, prefix: str) -> str:
    city = str(txn.get(f"{prefix}city") or "").strip()
    state = str(txn.get(f"{prefix}state") or "").strip()
    postal = str(txn.get(f"{prefix}postal_code") or "").strip()
    city_state = ", ".join(part for part in [city, state] if part)
    return " ".join(part for part in [city_state, postal] if part)


def _match_keys_label(value: Any) -> str:
    keys = [str(item).replace("_", " ") for item in _as_list(value) if item]
    return ", ".join(keys) if keys else "Not provided"


def _merchant_name(merchant: Any) -> str:
    if isinstance(merchant, dict):
        return _display(merchant.get("display_name") or merchant.get("alias") or merchant.get("name") or "Not provided")
    return _display(merchant or "Not provided")


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _display(value: Any) -> str:
    if value is True:
        return "true"
    if value is False:
        return "false"
    if value is None:
        return ""
    return str(_summary_safe(value))


def _e(value: Any, *, quote: bool = False) -> str:
    return html.escape(_display(value), quote=quote)
