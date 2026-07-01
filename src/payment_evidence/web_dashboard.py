from __future__ import annotations

import html
import json
import time
from argparse import Namespace
from types import SimpleNamespace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from . import cli as cli_module
from .access import authorize_merchant
from .audit import AuditAppendError, append_audit_event
from .artifacts import ArtifactStore
from .cloudflare_access import validator_from_jwks_url
from .config import load_configured_aliases, load_merchant_config, resolve_default_merchant_alias
from .identity import CloudflareValidator, extract_identity
from .secret_store import LocalSecretStore, default_secret_store_path
from .secrets import resolve_security_key
from .service_requests import validation_error_response, validate_investigate_request, validate_search_request
from .tenant_registry import TenantRegistry

LOCAL_ARTIFACT_TTL_SECONDS = 100 * 365 * 24 * 60 * 60


def render_human_search_dashboard(
    merchant_aliases: list[str] | None = None,
    *,
    title: str = "Transaction Search",
    identity: dict[str, Any] | None = None,
    tenant_display_name: str | None = None,
    merchant_display_names: dict[str, str] | None = None,
) -> str:
    aliases = merchant_aliases or []
    display_names = merchant_display_names or {}
    options = "".join(
        f'<option value="{_e(alias)}">{_e(display_names.get(alias) or alias)}</option>'
        for alias in aliases
    )
    merchant_control = (
        f'<select name="merchant_id" id="merchant_id"><option value="">Use default merchant</option>{options}</select>'
        if aliases
        else '<input name="merchant_id" id="merchant_id" autocomplete="off" placeholder="Optional merchant ID/alias">'
    )
    safe_identity = _safe_dashboard_identity(identity)
    authorized_merchants = [
        {"alias": alias, "display_name": display_names.get(alias) or alias}
        for alias in aliases
    ]
    dashboard_context = {
        "identity": safe_identity,
        "tenant_display_name": tenant_display_name or safe_identity.get("tenant_id") or "",
        "authorized_merchants": authorized_merchants,
    }
    context_json = json.dumps(dashboard_context, sort_keys=True, separators=(",", ":")).replace("</", "<\\/")
    identity_panel = _render_identity_panel(
        safe_identity,
        tenant_display_name=tenant_display_name,
        authorized_merchants=authorized_merchants,
    )
    setup_required_panel = _render_setup_required_panel() if not aliases else ""
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{_e(title)}</title>
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
    :root {{ color-scheme: light dark; --ink:#142033; --muted:#64748b; --line:#dbe5f0; --panel:#ffffff; --soft:#f6f8fb; --brand:#2458d3; --brand2:#0f766e; --bad:#b91c1c; --body-bg:linear-gradient(180deg,#f8fafc,#eef2f7); --control-bg:#ffffff; --control-text:#142033; --tool-bg:rgba(255,255,255,.14); --tool-line:rgba(255,255,255,.18); --note-bg:#eff6ff; --note-line:#bfdbfe; --note-text:#142033; --results-head-bg:#f8fafc; --pill-bg:#e2e8f0; --pill-text:#142033; }}
    :root[data-theme="dark"] {{ color-scheme: dark; --ink:#f8fafc; --muted:#94a3b8; --line:#263244; --panel:#111827; --soft:#0f172a; --body-bg:linear-gradient(180deg,#070b14,#0f172a); --control-bg:#0b1220; --control-text:#f8fafc; --note-bg:#10213d; --note-line:#1e3a8a; --note-text:#dbeafe; --results-head-bg:#111827; --pill-bg:#172554; --pill-text:#bfdbfe; }}
    :root[data-theme="light"] {{ color-scheme: light; }}
    * {{ box-sizing:border-box; }} body {{ margin:0; font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; color:var(--ink); background:var(--body-bg); }}
    .page-tools {{ width:min(1120px, calc(100% - 32px)); margin:14px auto -10px; display:flex; justify-content:flex-end; align-items:center; gap:10px; flex-wrap:wrap; }}
    .theme-control {{ display:flex; align-items:center; gap:8px; padding:7px 10px; border-radius:12px; background:var(--tool-bg); border:1px solid var(--tool-line); }}
    .theme-control label {{ color:white; font-size:.78rem; font-weight:850; text-transform:uppercase; letter-spacing:.08em; }}
    .theme-control select {{ min-height:34px; border-radius:9px; border:1px solid rgba(255,255,255,.25); background:rgba(2,6,23,.34); color:white; font:inherit; font-weight:750; padding:4px 8px; }}
    .app-nav {{ display:flex; gap:10px; flex-wrap:wrap; }} .nav-button {{ display:inline-flex; align-items:center; justify-content:center; border:0; border-radius:12px; padding:9px 12px; font-weight:850; background:var(--brand); color:white; text-decoration:none; }}
    main {{ width:min(1120px, calc(100% - 32px)); margin:28px auto 54px; }} .hero {{ padding:26px; border-radius:24px; background:linear-gradient(135deg,#0f172a,#1d4ed8 62%,#0f766e); color:white; box-shadow:0 18px 40px rgba(15,23,42,.12); }}
    .hero p {{ max-width:820px; opacity:.88; }} section {{ margin-top:16px; padding:20px; background:var(--panel); border:1px solid var(--line); border-radius:18px; box-shadow:0 8px 22px rgba(15,23,42,.06); }}
    form {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(210px,1fr)); gap:14px; }} label {{ display:grid; gap:6px; color:var(--muted); text-transform:uppercase; letter-spacing:.08em; font-size:.74rem; font-weight:800; }}
    input, select {{ width:100%; padding:10px 12px; border:1px solid var(--line); border-radius:12px; font:inherit; color:var(--control-text); background:var(--control-bg); }} .actions {{ display:flex; align-items:end; gap:10px; flex-wrap:wrap; }}
    button {{ cursor:pointer; border:0; border-radius:12px; padding:11px 14px; font-weight:850; background:var(--brand); color:white; }} button.secondary {{ background:#0f766e; }} .note {{ color:var(--note-text); background:var(--note-bg); border:1px solid var(--note-line); padding:12px 14px; border-radius:14px; }}
    .error {{ color:var(--bad); font-weight:800; }} .status {{ color:var(--muted); font-weight:750; }} .status.is-loading {{ color:var(--brand); }} .status.is-loading::before {{ content:'⏳ '; }} button[disabled] {{ opacity:.62; cursor:wait; }} .table-wrap {{ overflow:auto; border:1px solid var(--line); border-radius:14px; }} table {{ width:100%; min-width:940px; border-collapse:collapse; table-layout:fixed; }} .col-rank {{ width:5%; }} .col-transaction {{ width:15%; }} .col-order {{ width:16%; }} .col-amount {{ width:9%; }} .col-last-four {{ width:7%; }} .col-date {{ width:14%; }} .col-score {{ width:7%; }} .col-reason {{ width:15%; }} .col-action {{ width:12%; }} th,td {{ padding:11px 12px; border-bottom:1px solid var(--line); text-align:left; vertical-align:top; }} td {{ overflow:hidden; text-overflow:ellipsis; overflow-wrap:anywhere; }} .order-cell {{ white-space:normal; }} .resizable-column {{ position:relative; padding-right:18px; }} .column-resize-handle {{ position:absolute; top:0; right:-4px; width:10px; min-height:100%; padding:0; border-radius:0; background:transparent; cursor:col-resize; z-index:1; }} .column-resize-handle:hover,.column-resize-handle:focus-visible {{ background:rgba(36,88,211,.24); outline:2px solid var(--brand); outline-offset:-2px; }} th {{ background:var(--results-head-bg); color:var(--ink); text-transform:uppercase; letter-spacing:.06em; font-size:.75rem; }} tr:last-child td {{ border-bottom:0; }} .pill {{ display:inline-flex; padding:4px 8px; border-radius:999px; background:var(--pill-bg); color:var(--pill-text); font-weight:800; }} .empty {{ padding:14px; color:var(--muted); background:var(--soft); border:1px dashed var(--line); border-radius:12px; }} .artifact-list a {{ display:block; margin:.35rem 0; color:var(--brand); font-weight:800; }} .identity-grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(170px,1fr)); gap:10px; }} .identity-item {{ padding:10px 12px; border:1px solid var(--line); border-radius:12px; background:var(--soft); }} .identity-item strong {{ display:block; color:var(--muted); font-size:.75rem; text-transform:uppercase; letter-spacing:.07em; }}
  </style>
</head>
<body>
<div class="page-tools">
  <nav class="app-nav" aria-label="Primary"><a class="nav-button" href="/">Search</a><a class="nav-button" href="/setup">Merchants</a></nav>
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
  <header class="hero"><h1>{_e(title)}</h1><p>Human-first transaction lookup. Enter merchant/date/clue information; the local server uses the configured gateway credential server-side. Search finds gateway transaction candidates. See detail opens transaction and card details that provide transaction information for client CRM follow-up.</p></header>
  {identity_panel}
  {setup_required_panel}
  <section><h2>Transaction Search</h2><p class="note">Gateway timezone: UTC. Enter calendar dates; the service searches from 00:00:00 through 23:59:59 UTC for the selected days.</p><p class="note">If transaction ID is provided, the system performs an exact transaction lookup first. Amount and last four validate the selected transaction instead of producing lower-probability noise.</p>
    <form data-testid="human-search-form" id="searchForm">
      <label>Merchant ID / alias {merchant_control}</label><label>Start date <input id="start_date" name="start_date" type="date" data-date-window-required="true"></label><label>End date <input id="end_date" name="end_date" type="date" data-date-window-required="true"></label><label>Amount <input name="amount" placeholder="42.50" inputmode="decimal"></label><label>Order ID <input name="order_id" autocomplete="off"></label><label>Transaction ID <input id="transaction_id" name="transaction_id" autocomplete="off" aria-describedby="dateRequirementNote"></label><label>Last four <input name="last_four" maxlength="4" inputmode="numeric"></label><label>Result limit <input name="result_limit" value="100" inputmode="numeric"></label><label>Max pages <input name="max_pages" value="5" inputmode="numeric"></label>
      <div class="actions"><button id="searchButton" type="submit">Search gateway</button><button class="secondary" id="investigateButton" type="button">See detail</button></div>
    </form>
    <p class="status" id="dateRequirementNote">Date window required unless a transaction ID is provided.</p>
  </section>
  <section data-testid="candidate-results"><h2>Transaction results</h2><div id="status" class="status" role="status" aria-live="polite" aria-busy="false">No search run yet.</div><p class="note">Transaction detail pages are retained in local run history on this machine. Use local files responsibly and purge history when you no longer need it.</p><div id="results" style="margin-top:12px"></div></section>
</main>
<script type="application/json" id="dashboardContext">{context_json}</script>
<script>
(function() {{
  var form=document.getElementById('searchForm'), status=document.getElementById('status'), results=document.getElementById('results');
  var searchButton=document.getElementById('searchButton'), investigateButton=document.getElementById('investigateButton');
  var lastSearchPayload=null;
  var selectedCandidateForDetail=null;
  function esc(value) {{ return String(value == null ? '' : value).replace(/[&<>"']/g, function(ch) {{ return {{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[ch]; }}); }}
  function setSearchBusy(isBusy, message) {{ status.setAttribute('aria-busy', String(isBusy)); status.classList.toggle('is-loading', Boolean(isBusy)); if(searchButton) searchButton.disabled=Boolean(isBusy); if(investigateButton) investigateButton.disabled=Boolean(isBusy); if(message) status.textContent=message; }}
  function applyTheme(mode) {{ var darkQuery = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)'); var resolved = mode === 'system' ? (darkQuery && darkQuery.matches ? 'dark' : 'light') : mode; document.documentElement.setAttribute('data-theme-mode', mode); document.documentElement.setAttribute('data-theme', resolved); var select=document.getElementById('themeSelect'); if(select) select.value=mode; }}
  var themeSelect=document.getElementById('themeSelect'); applyTheme(document.documentElement.getAttribute('data-theme-mode') || 'system'); if(themeSelect) {{ themeSelect.addEventListener('change', function() {{ try {{ localStorage.setItem('transactionSearchTheme', themeSelect.value); }} catch(error) {{}} applyTheme(themeSelect.value); }}); }}
  function syncDateRequirements() {{ var transaction_id=document.getElementById('transaction_id'); var dateWindowRequired = !transaction_id || transaction_id.value.trim() === ''; ['start_date','end_date'].forEach(function(id) {{ var input=document.getElementById(id); if(input) {{ input.required = dateWindowRequired; input.setAttribute('aria-required', String(dateWindowRequired)); }} }}); }}
  var transactionIdInput=document.getElementById('transaction_id'); if(transactionIdInput) {{ transactionIdInput.addEventListener('input', syncDateRequirements); }} syncDateRequirements();
  function restoreSearchContext() {{ try {{ var raw=sessionStorage.getItem('transactionSearchContext'); if(!raw) return; var context=JSON.parse(raw); Object.keys(context).forEach(function(key) {{ if(form.elements[key]) form.elements[key].value=context[key]; }}); sessionStorage.removeItem('transactionSearchContext'); syncDateRequirements(); status.textContent='Previous search criteria restored. Adjust the fields and search again.'; }} catch(error) {{ try {{ sessionStorage.removeItem('transactionSearchContext'); }} catch(ignore) {{}} }} }}
  restoreSearchContext();
  function collectForm(overrides) {{ var data={{}}; new FormData(form).forEach(function(value,key) {{ if (String(value).trim() !== '') data[key]=String(value).trim(); }}); if(overrides) {{ Object.keys(overrides).forEach(function(key) {{ if(overrides[key] == null || String(overrides[key]).trim() === '') {{ delete data[key]; }} else {{ data[key]=String(overrides[key]).trim(); }} }}); }} return data; }}
  function artifactLinks(payload) {{ var artifacts=payload.artifacts||{{}}; var id=artifacts.dashboard_artifact_id||artifacts.artifact_id||artifacts.dashboard_id; if(!id) return ''; var href='/api/artifacts/'+encodeURIComponent(String(id)); return '<div class="artifact-list"><h3>Transaction detail</h3><p>The transaction detail is ready. Opening it now.</p><a class="primary-detail-link" href="'+href+'" rel="noopener">Open transaction detail</a></div>'; }}
  function navigateToTransactionDetail(payload) {{ var artifacts=payload.artifacts||{{}}; var id=artifacts.dashboard_artifact_id||artifacts.artifact_id||artifacts.dashboard_id; if(!id) return false; var href='/api/artifacts/'+encodeURIComponent(String(id)); window.location.assign(href); return true; }}
  function candidateOverrides(candidate) {{ return {{transaction_id:candidate.transaction_id||'', order_id:candidate.order_id||''}}; }}
  function formatCandidateDate(value) {{ var text=String(value == null ? '' : value); if(/^[0-9]{{14}}$/.test(text)) return text.slice(0,4)+'-'+text.slice(4,6)+'-'+text.slice(6,8)+' '+text.slice(8,10)+':'+text.slice(10,12)+':'+text.slice(12,14)+' UTC'; return text; }}
  function initColumnResizing(table) {{ if(!table) return; var cols=table.querySelectorAll('col'); Array.prototype.forEach.call(table.querySelectorAll('.column-resize-handle'), function(handle,index) {{ handle.addEventListener('mousedown', function(event) {{ event.preventDefault(); var startX=event.clientX; var startWidth=parseFloat(cols[index] && cols[index].style.width ? cols[index].style.width : (cols[index] && cols[index].getAttribute('data-width')) || '12'); var tableWidth=table.getBoundingClientRect().width || 940; function onMove(moveEvent) {{ var next=Math.max(5, Math.min(35, startWidth + ((moveEvent.clientX-startX)/tableWidth*100))); if(cols[index]) cols[index].style.width=next+'%'; }} function onUp() {{ document.removeEventListener('mousemove', onMove); document.removeEventListener('mouseup', onUp); }} document.addEventListener('mousemove', onMove); document.addEventListener('mouseup', onUp); }}); }}); }}
  function renderCandidates(payload) {{ var candidates=payload.candidates||[]; if(!candidates.length && payload.selected_candidate) candidates=[payload.selected_candidate]; var html=''; if(candidates.length) {{ if(candidates.length > 1) {{ html+='<p class="note">Multiple candidates found. Choose a result before opening detail.</p>'; }} var rows=candidates.map(function(candidate,index) {{ var explanations=(candidate.explanations||[]).join('; '); var disabled=!candidate.transaction_id ? ' disabled aria-disabled="true"' : ''; return '<tr><td>'+(index+1)+'</td><td><strong>'+esc(candidate.transaction_id||'Not available')+'</strong></td><td class=\"order-cell\">'+esc(candidate.order_id||'')+'</td><td>'+esc(candidate.amount||'')+'</td><td>'+esc(candidate.last_four||'')+'</td><td>'+esc(formatCandidateDate(candidate.date))+'</td><td><span class="pill">'+esc(candidate.score||0)+'</span></td><td>'+esc(explanations)+'</td><td><button type="button" class="secondary" data-testid="candidate-generate-button" data-candidate-index="'+index+'"'+disabled+'>See detail</button></td></tr>'; }}).join(''); html+='<div class="table-wrap"><table><colgroup><col class="col-rank" data-width="5" style="width:5%"><col class="col-transaction" data-width="15" style="width:15%"><col class="col-order" data-width="16" style="width:16%"><col class="col-amount" data-width="9" style="width:9%"><col class="col-last-four" data-width="7" style="width:7%"><col class="col-date" data-width="14" style="width:14%"><col class="col-score" data-width="7" style="width:7%"><col class="col-reason" data-width="15" style="width:15%"><col class="col-action" data-width="12" style="width:12%"></colgroup><thead><tr><th class="resizable-column">Rank<button type="button" class="column-resize-handle" aria-label="Resize Rank column"></button></th><th class="resizable-column">Transaction<button type="button" class="column-resize-handle" aria-label="Resize Transaction column"></button></th><th class="resizable-column">Order<button type="button" class="column-resize-handle" aria-label="Resize Order column"></button></th><th class="resizable-column">Amount<button type="button" class="column-resize-handle" aria-label="Resize Amount column"></button></th><th class="resizable-column">Last 4<button type="button" class="column-resize-handle" aria-label="Resize Last 4 column"></button></th><th class="resizable-column">Date<button type="button" class="column-resize-handle" aria-label="Resize Date column"></button></th><th class="resizable-column">Score<button type="button" class="column-resize-handle" aria-label="Resize Score column"></button></th><th class="resizable-column">Why matched<button type="button" class="column-resize-handle" aria-label="Resize Why matched column"></button></th><th class="resizable-column">Action<button type="button" class="column-resize-handle" aria-label="Resize Action column"></button></th></tr></thead><tbody>'+rows+'</tbody></table></div>'; }} else {{ html='<div class="empty">No candidates found. Clear or change every active clue if you are testing a no-match case.</div>'; }} results.innerHTML=html+artifactLinks(payload); initColumnResizing(results.querySelector('table')); Array.prototype.forEach.call(results.querySelectorAll('[data-testid="candidate-generate-button"]'), function(button) {{ button.addEventListener('click', function() {{ var index=Number(button.getAttribute('data-candidate-index')); var candidate=candidates[index]; selectedCandidateForDetail=candidateOverrides(candidate||{{}}); submitTo('/api/investigate','Opening transaction detail for the selected result...',true,selectedCandidateForDetail); }}); }}); }}
  function friendlyError(payload) {{ if(payload.status==='denied') return 'Access denied'; if(payload.error==='credential_resolution_failed'||payload.error==='request_failed') return 'Gateway request failed'; return payload.error||payload.reason||'Request failed'; }}
  function statusSummary(payload) {{ var summary=payload.candidate_summary||{{}}; var timing=payload.timing||{{}}; var timingText=timing.server_ms != null ? ' Server time: '+timing.server_ms+' ms.' : ''; var message='Complete. Status: '+(payload.status||'unknown')+'. Candidates: '+(summary.candidate_count||0)+'. Top score: '+(summary.top_score||0)+'. Ambiguous: '+Boolean(summary.ambiguous)+'.'+timingText; if(summary.ambiguous) message+=' Multiple close matches found. Add a stronger clue before opening detail.'; return message; }}
  function submitTo(endpoint,pendingText,openDetailOnSuccess,overrides) {{ setSearchBusy(true, pendingText); if(!openDetailOnSuccess) {{ selectedCandidateForDetail=null; results.innerHTML=''; }} fetch(endpoint, {{ method:'POST', headers:{{'Content-Type':'application/json'}}, body:JSON.stringify(collectForm(overrides)) }}).then(function(response) {{ return response.json().then(function(payload) {{ return {{ok:response.ok,payload:payload}}; }}); }}).then(function(result) {{ setSearchBusy(false); if(!result.ok || result.payload.status==='error' || result.payload.status==='denied') {{ status.innerHTML='<span class="error">'+esc(friendlyError(result.payload))+'</span>'; return; }} if(!openDetailOnSuccess) {{ lastSearchPayload=result.payload; }} status.textContent=statusSummary(result.payload); renderCandidates(result.payload); if(openDetailOnSuccess) {{ navigateToTransactionDetail(result.payload); }} }}).catch(function(error) {{ setSearchBusy(false); status.innerHTML='<span class="error">Gateway request failed</span>'; }}); }}
  form.addEventListener('submit', function(event) {{ event.preventDefault(); submitTo('/api/search','Searching gateway...',false); }});
  document.getElementById('investigateButton').addEventListener('click', function() {{ var candidates=(lastSearchPayload&&lastSearchPayload.candidates)||[]; if(candidates.length > 1 && !selectedCandidateForDetail) {{ status.innerHTML='<span class="error">Multiple candidates found. Choose a result before opening detail.</span>'; return; }} submitTo('/api/investigate','Opening transaction detail...',true,selectedCandidateForDetail); }});
}}());
</script></body></html>"""



def _render_setup_required_panel() -> str:
    return (
        '<section data-testid="setup-required"><h2>Setup required</h2>'
        '<p class="note">Add your merchant API credentials before running Transaction Search.</p>'
        '<p><a href="/setup">Open setup wizard</a> to add credentials in the browser.</p>'
        '<p>Run:</p><pre><code>payment-search add-merchant</code></pre></section>'
    )


def render_setup_wizard(
    *,
    error: str | None = None,
    values: dict[str, Any] | None = None,
    config_path: str | Path | None = None,
    selected_alias: str | None = None,
) -> str:
    config = _read_setup_config(config_path)
    merchants = config.get("merchants", {}) if isinstance(config.get("merchants"), dict) else {}
    selected = _clean(selected_alias) or _clean((values or {}).get("original_alias")) or _clean((values or {}).get("selected_alias")) or ""
    selected_entry = merchants.get(selected, {}) if selected else {}
    merged_values = {
        "alias": selected or "",
        "display_name": selected_entry.get("display_name") or "",
        "gateway": selected_entry.get("gateway") or "nmi",
        "base_url": selected_entry.get("base_url") or "https://mbcard.transactiongateway.com",
    }
    if values:
        for key in ("alias", "display_name", "gateway", "base_url"):
            value = _clean(values.get(key))
            if value is not None:
                merged_values[key] = value
    error_html = f'<p class="error">{_e(error)}</p>' if error else ""
    options = ''.join(
        f'<option value="{_e(alias)}"{" selected" if alias == selected else ""}>{_e(entry.get("display_name") or alias)}</option>'
        for alias, entry in sorted(merchants.items())
        if isinstance(entry, dict)
    )
    add_selected = " selected" if not selected else ""
    existing = (
        f'<label>Existing merchants <select name="existing_merchant" onchange="window.location=\'/setup\'+(this.value?\'?merchant=\'+encodeURIComponent(this.value):\'\')"><option value=""{add_selected}>Add new merchant</option>{options}</select></label>'
        if merchants
        else '<p class="note">No merchants configured yet. Add the first merchant below.</p>'
    )
    alias = _e(str(merged_values.get("alias") or ""))
    display_name = _e(str(merged_values.get("display_name") or ""))
    gateway = _e(str(merged_values.get("gateway") or "nmi"))
    base_url = _e(str(merged_values.get("base_url") or "https://mbcard.transactiongateway.com"))
    api_required = "" if selected_entry else " required"
    api_help = "Leave blank to keep the existing local secret." if selected_entry else "Required for a new merchant."
    original_alias_input = f'<input type="hidden" name="original_alias" value="{_e(selected)}">' if selected else ""
    alias_readonly = " readonly" if selected else ""
    remove_form = (
        f'<form method="post" action="/setup" data-testid="merchant-delete-form"><input type="hidden" name="action" value="delete"><input type="hidden" name="original_alias" value="{_e(selected)}"><button class="danger" type="submit">Remove merchant</button></form>'
        if selected
        else ""
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Merchant setup</title>
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
:root{{color-scheme:light dark;--body-bg:#f8fafc;--panel:#ffffff;--ink:#142033;--muted:#475569;--line:#dbe5f0;--brand:#2458d3;--danger:#b91c1c;--note-bg:#eff6ff;--note-line:#bfdbfe;--control-bg:#ffffff;--control-text:#142033;}}
:root[data-theme="dark"]{{color-scheme:dark;--body-bg:#0f172a;--panel:#111827;--ink:#f8fafc;--muted:#94a3b8;--line:#263244;--brand:#3b82f6;--danger:#dc2626;--note-bg:#10213d;--note-line:#1e3a8a;--control-bg:#0b1220;--control-text:#f8fafc;}}
body{{font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;margin:0;background:var(--body-bg);color:var(--ink);}}
main{{width:min(780px,calc(100% - 32px));margin:36px auto;}}
section{{background:var(--panel);border:1px solid var(--line);border-radius:18px;padding:22px;box-shadow:0 8px 22px rgba(15,23,42,.06);}}
.page-tools{{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-bottom:14px}}.nav-button,.button{{display:inline-flex;align-items:center;justify-content:center;border:0;border-radius:12px;padding:11px 14px;font-weight:850;background:var(--brand);color:white;text-decoration:none;cursor:pointer;}}
.theme-control{{display:flex;align-items:center;gap:8px;margin-left:auto}}.theme-control label{{font-size:.78rem;font-weight:850;text-transform:uppercase;letter-spacing:.08em;color:var(--muted)}}
form{{display:grid;gap:14px;margin-top:14px;}}label{{display:grid;gap:6px;font-weight:800;color:var(--muted);}}input,select{{width:100%;padding:10px 12px;border:1px solid var(--line);border-radius:12px;font:inherit;color:var(--control-text);background:var(--control-bg);}}button{{display:inline-flex;align-items:center;justify-content:center;border:0;border-radius:12px;padding:11px 14px;font-weight:850;background:var(--brand);color:white;text-decoration:none;cursor:pointer;}}button.danger{{background:var(--danger);}}.note{{background:var(--note-bg);border:1px solid var(--note-line);border-radius:14px;padding:12px 14px;}}.error{{color:var(--danger);font-weight:850;}}
</style></head><body><main><div class="page-tools"><a class="nav-button" href="/">Search</a><div class="theme-control"><label for="themeSelect">Theme</label><select id="themeSelect" aria-label="Theme"><option value="system">System</option><option value="light">Light</option><option value="dark">Dark</option></select></div></div><section>
<h1>Merchant setup</h1>
<p class="note">Add or update merchant gateway credentials. The browser wizard writes local config with a <code>local_secret_ref</code>; it does not write the raw API key to config.</p>
{existing}
{error_html}
<form method="post" action="/setup" data-testid="merchant-setup-form">{original_alias_input}
  <label>Merchant alias <input name="alias" value="{alias}" autocomplete="off" required{alias_readonly}></label>
  <label>Merchant display name <input name="display_name" value="{display_name}" autocomplete="organization" required></label>
  <label>Gateway <select name="gateway"><option value="{gateway}">NMI</option></select></label>
  <label>Gateway base URL <input name="base_url" value="{base_url}" autocomplete="off" required></label>
  <label>API/security key <input name="api_key" type="password" autocomplete="off"{api_required}><span>{_e(api_help)}</span></label>
  <div><button type="submit">Save merchant setup</button> <a class="button" href="/">Back to search</a></div>
</form>
{remove_form}
</section></main><script>
(function () {{
  function applyTheme(mode) {{ var darkQuery = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)'); var resolved = mode === 'system' ? (darkQuery && darkQuery.matches ? 'dark' : 'light') : mode; document.documentElement.setAttribute('data-theme-mode', mode); document.documentElement.setAttribute('data-theme', resolved); var select=document.getElementById('themeSelect'); if(select) select.value=mode; }}
  var themeSelect=document.getElementById('themeSelect'); applyTheme(document.documentElement.getAttribute('data-theme-mode') || 'system'); if(themeSelect) {{ themeSelect.addEventListener('change', function() {{ try {{ localStorage.setItem('transactionSearchTheme', themeSelect.value); }} catch(error) {{}} applyTheme(themeSelect.value); }}); }}
}}());
</script></body></html>"""


def render_update_confirmation(*, form: dict[str, Any], merchant_name: str, changes: list[tuple[str, str, str]]) -> str:
    rows = ''.join(f'<li><strong>{_e(label)}</strong> for {_e(merchant_name)}: {_e(old)} → {_e(new)}</li>' for label, old, new in changes)
    hidden = ''.join(f'<input type="hidden" name="{_e(key)}" value="{_e(value)}">' for key, value in form.items() if key != "confirm_update")
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Confirm merchant update</title>
<style>body{{font-family:Inter,system-ui,sans-serif;margin:0;background:#f8fafc;color:#142033}}main{{width:min(760px,calc(100% - 32px));margin:36px auto}}section{{background:white;border:1px solid #dbe5f0;border-radius:18px;padding:22px}}.button,button{{display:inline-flex;align-items:center;justify-content:center;border:0;border-radius:12px;padding:11px 14px;font-weight:850;background:#2458d3;color:white;text-decoration:none;cursor:pointer}}.secondary{{background:#64748b}}</style></head><body><main><section>
<h1>Confirm merchant update</h1><p>You are about to update the following fields for <strong>{_e(merchant_name)}</strong>:</p><ul>{rows}</ul>
<form method="post" action="/setup">{hidden}<input type="hidden" name="confirm_update" value="yes"><button type="submit">Confirm update</button> <a class="button secondary" href="/setup?merchant={_e(str(form.get('alias') or ''))}">Cancel</a></form>
</section></main></body></html>"""

def render_delete_confirmation(*, alias: str, merchant_name: str) -> str:
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Confirm merchant removal</title>
<style>body{{font-family:Inter,system-ui,sans-serif;margin:0;background:#f8fafc;color:#142033}}main{{width:min(760px,calc(100% - 32px));margin:36px auto}}section{{background:white;border:1px solid #dbe5f0;border-radius:18px;padding:22px}}.button,button{{display:inline-flex;align-items:center;justify-content:center;border:0;border-radius:12px;padding:11px 14px;font-weight:850;background:#2458d3;color:white;text-decoration:none;cursor:pointer}}.danger{{background:#b91c1c}}</style></head><body><main><section>
<h1>Confirm merchant removal</h1><p>You are about to remove <strong>{_e(merchant_name)}</strong> ({_e(alias)}) from this local kit. Searches for this merchant will stop working until it is added again.</p>
<form method="post" action="/setup"><input type="hidden" name="action" value="delete"><input type="hidden" name="original_alias" value="{_e(alias)}"><input type="hidden" name="confirm_delete" value="yes"><button class="danger" type="submit">Remove merchant</button> <a class="button" href="/setup?merchant={_e(alias)}">Cancel</a></form>
</section></main></body></html>"""


def delete_browser_setup(form: dict[str, Any], *, config_path: str | Path | None, secret_store_path: str | Path | None = None) -> dict[str, Any]:
    alias = _clean(form.get("original_alias") or form.get("alias"))
    confirmed = _clean(form.get("confirm_delete")) == "yes"
    if not alias:
        return {"status": "error", "error": "Merchant alias is required"}
    config_file = Path(config_path or "~/.payment-search/config.json").expanduser()
    secret_file = Path(secret_store_path).expanduser() if secret_store_path else default_secret_store_path()
    config = cli_module._read_local_config(config_file)
    merchants = config.setdefault("merchants", {})
    existing = merchants.get(alias, {}) if isinstance(merchants.get(alias), dict) else {}
    if not existing:
        return {"status": "error", "error": "Merchant was not found"}
    merchant_name = str(existing.get("display_name") or alias)
    if not confirmed:
        return {"status": "confirm_delete", "merchant_alias": alias, "merchant_name": merchant_name}
    merchants.pop(alias, None)
    if config.get("default_merchant") == alias:
        remaining = sorted(merchants)
        if remaining:
            config["default_merchant"] = remaining[0]
        else:
            config.pop("default_merchant", None)
    config_file.parent.mkdir(parents=True, exist_ok=True)
    config_file.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n")
    try:
        LocalSecretStore(secret_file).remove_secret("merchant", alias, "security_key")
    except Exception:
        pass
    return {"status": "completed", "merchant_alias": alias}


def save_browser_setup(form: dict[str, Any], *, config_path: str | Path | None, secret_store_path: str | Path | None = None) -> dict[str, Any]:
    posted_alias = _clean(form.get("alias"))
    original_alias = _clean(form.get("original_alias"))
    alias = original_alias or posted_alias
    display_name = _clean(form.get("display_name")) or alias
    gateway = _clean(form.get("gateway")) or "nmi"
    base_url = _clean(form.get("base_url")) or "https://mbcard.transactiongateway.com"
    api_key = _clean(form.get("api_key"))
    confirmed = _clean(form.get("confirm_update")) == "yes"
    if not alias:
        return {"status": "error", "error": "Merchant alias is required"}
    if not display_name:
        return {"status": "error", "error": "Merchant display name is required"}
    if gateway != "nmi":
        return {"status": "error", "error": "Gateway must be nmi"}

    config_file = Path(config_path or "~/.payment-search/config.json").expanduser()
    secret_file = Path(secret_store_path).expanduser() if secret_store_path else default_secret_store_path()
    config = cli_module._read_local_config(config_file)
    merchants = config.setdefault("merchants", {})
    existing = merchants.get(alias, {}) if isinstance(merchants.get(alias), dict) else {}
    if existing:
        changes = _merchant_update_changes(existing, display_name=display_name or "", gateway=gateway, base_url=base_url, api_key_present=bool(api_key))
        if changes and not confirmed:
            return {"status": "confirm_update", "merchant_alias": alias, "merchant_name": existing.get("display_name") or alias, "changes": changes}
    secret_ref = str(existing.get("local_secret_ref") or f"merchant/{alias}/security_key")
    if api_key:
        LocalSecretStore(secret_file).set_secret("merchant", alias, "security_key", api_key)
    elif not existing.get("local_secret_ref"):
        return {"status": "error", "error": "API key is required"}
    merchants[alias] = {
        "display_name": display_name,
        "gateway": gateway,
        "base_url": base_url,
        "local_secret_ref": secret_ref,
    }
    config["default_merchant"] = alias
    config_file.parent.mkdir(parents=True, exist_ok=True)
    config_file.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n")
    return {"status": "completed", "merchant_alias": alias}


def _merchant_update_changes(existing: dict[str, Any], *, display_name: str, gateway: str, base_url: str, api_key_present: bool) -> list[tuple[str, str, str]]:
    checks = [
        ("display name", str(existing.get("display_name") or ""), display_name),
        ("Gateway", str(existing.get("gateway") or "nmi"), gateway),
        ("Gateway base URL", str(existing.get("base_url") or ""), base_url),
    ]
    changes = [(label, old, new) for label, old, new in checks if old != new]
    if api_key_present:
        changes.append(("API/security key", "existing local secret", "new local secret"))
    return changes


def _read_setup_config(config_path: str | Path | None) -> dict[str, Any]:
    path = Path(config_path or "~/.payment-search/config.json").expanduser()
    if not path.exists():
        return {"merchants": {}}
    try:
        data = json.loads(path.read_text())
    except Exception:
        return {"merchants": {}}
    return data if isinstance(data, dict) else {"merchants": {}}

def _safe_dashboard_identity(identity: dict[str, Any] | None) -> dict[str, str]:
    if not identity:
        return {}
    safe: dict[str, str] = {}
    for key in ("user_id", "role", "tenant_id", "iso_id"):
        value = identity.get(key)
        if value is not None:
            safe[key] = str(value)
    return safe


def _render_identity_panel(
    identity: dict[str, str],
    *,
    tenant_display_name: str | None,
    authorized_merchants: list[dict[str, str]],
) -> str:
    if not identity:
        return ""
    merchant_names = ", ".join(_e(item["display_name"]) for item in authorized_merchants) or "No authorized merchants"
    tenant_label = tenant_display_name or identity.get("tenant_id") or "Not available"
    iso_label = identity.get("iso_id") or "None"
    return (
        '<section data-testid="identity-panel">'
        '<h2>Signed-in scope</h2>'
        '<div class="identity-grid">'
        f'<div class="identity-item"><strong>User</strong>{_e(identity.get("user_id", ""))}</div>'
        f'<div class="identity-item"><strong>Role</strong>{_e(identity.get("role", ""))}</div>'
        f'<div class="identity-item"><strong>Tenant</strong>{_e(tenant_label)}</div>'
        f'<div class="identity-item"><strong>ISO</strong>{_e(iso_label)}</div>'
        f'<div class="identity-item"><strong>Authorized merchants</strong>{merchant_names}</div>'
        '</div>'
        '</section>'
    )


def run_human_search_request(form: dict[str, Any], *, config_path: str | Path | None, gateway: str, timeout: int, secret_store_path: str | Path | None = None) -> dict[str, Any]:
    validation = validate_search_request(form)
    if not validation.valid:
        return validation_error_response(validation)
    try:
        merchant, security_key = _merchant_and_key(validation.normalized, config_path=config_path, gateway=gateway, secret_store_path=secret_store_path)
    except Exception:
        return {"status": "error", "error": "credential_resolution_failed"}
    try:
        args = _search_args(validation.normalized, timeout=timeout)
        return cli_module._run_search(args, merchant, security_key)
    except Exception:
        return {"status": "error", "error": "request_failed"}


def run_human_investigate_request(
    form: dict[str, Any],
    *,
    config_path: str | Path | None,
    gateway: str,
    timeout: int,
    output_dir: str | Path,
    secret_store_path: str | Path | None = None,
) -> dict[str, Any]:
    validation = validate_investigate_request(form)
    if not validation.valid:
        return validation_error_response(validation)
    try:
        merchant, security_key = _merchant_and_key(validation.normalized, config_path=config_path, gateway=gateway, secret_store_path=secret_store_path)
    except Exception:
        return {"status": "error", "error": "credential_resolution_failed"}
    try:
        args = _search_args(validation.normalized, timeout=timeout)
        args.output_dir = str(output_dir)
        args.case_id = _safe_case_id(form)
        args.title = "Transaction Search Detail"
        args.lookback_days = int(_clean(validation.normalized.get("lookback_days")) or 365)
        args.lookahead_days = int(_clean(validation.normalized.get("lookahead_days")) or 0)
        args.match = _clean(validation.normalized.get("match")) or "customer_id,masked_card,email,billing_zip"
        args.pretty = True
        return cli_module._run_investigate(args, merchant, security_key)
    except Exception:
        return {"status": "error", "error": "request_failed"}


def build_whoami_response(
    headers: dict[str, str],
    *,
    tenant_registry_path: str | Path | None,
    identity_mode: str,
    dev_identity_enabled: bool,
    cloudflare_validator: CloudflareValidator | None = None,
) -> dict[str, Any]:
    if tenant_registry_path is None:
        return {"status": "ok", "identity": {"user_id": "local-operator", "role": "local_operator", "tenant_id": "local", "iso_id": None}, "authorized_merchants": []}
    registry = TenantRegistry(tenant_registry_path)
    extracted = extract_identity(
        headers,
        registry,
        mode=identity_mode,
        dev_enabled=dev_identity_enabled,
        cloudflare_validator=cloudflare_validator,
    )
    if not extracted.allowed or extracted.identity is None:
        return {"status": "denied", "reason": extracted.reason}
    identity = extracted.identity
    return {
        "status": "ok",
        "identity": _safe_identity_payload(identity),
        "authorized_merchants": _authorized_merchant_aliases(identity, registry),
    }


def _safe_identity_payload(identity: Any) -> dict[str, str | None]:
    return {
        "user_id": getattr(identity, "user_id", None),
        "role": getattr(identity, "role", None),
        "tenant_id": getattr(identity, "tenant_id", None),
        "iso_id": getattr(identity, "iso_id", None),
    }

def render_dashboard_for_request(
    headers: dict[str, str],
    *,
    tenant_registry_path: str | Path | None,
    identity_mode: str,
    dev_identity_enabled: bool,
    config_path: str | Path | None = None,
    cloudflare_validator: CloudflareValidator | None = None,
    title: str = "Transaction Search",
) -> str:
    if tenant_registry_path is None:
        aliases, display_names = _configured_merchant_options(config_path)
        return render_human_search_dashboard(aliases, title=title, merchant_display_names=display_names)
    try:
        registry = TenantRegistry(tenant_registry_path)
        extracted = extract_identity(
            headers,
            registry,
            mode=identity_mode,
            dev_enabled=dev_identity_enabled,
            cloudflare_validator=cloudflare_validator,
        )
    except Exception:
        return render_human_search_dashboard(title=title)
    if not extracted.allowed or extracted.identity is None:
        return render_human_search_dashboard(title=title)
    identity = extracted.identity
    aliases = _authorized_merchant_aliases(identity, registry)
    identity_payload = _safe_identity_payload(identity)
    return render_human_search_dashboard(
        aliases,
        title=title,
        identity=identity_payload,
        tenant_display_name=registry.tenant_display_name(identity.tenant_id),
        merchant_display_names={alias: registry.merchant_display_name(alias) for alias in aliases},
    )



def _configured_merchant_options(config_path: str | Path | None) -> tuple[list[str], dict[str, str]]:
    aliases = load_configured_aliases(config_path)
    display_names: dict[str, str] = {}
    if not aliases:
        return aliases, display_names
    try:
        for alias in aliases:
            display_names[alias] = load_merchant_config(config_path, alias).display_name
    except Exception:
        return aliases, {}
    return aliases, display_names


def _authorized_merchant_aliases(identity: Any, registry: TenantRegistry) -> list[str]:
    auth_registry = registry.as_auth_registry()
    return sorted(
        merchant_alias
        for merchant_alias in registry.merchant_ids()
        if authorize_merchant(identity, merchant_alias, auth_registry).allowed
    )


def _headers_dict(headers: Any) -> dict[str, str]:
    return {str(key): str(value) for key, value in headers.items()}

def authorize_service_request(
    headers: dict[str, str],
    payload: dict[str, Any],
    *,
    tenant_registry_path: str | Path | None,
    identity_mode: str,
    dev_identity_enabled: bool,
    cloudflare_validator: CloudflareValidator | None = None,
    config_path: str | Path | None,
) -> dict[str, Any]:
    if tenant_registry_path is None:
        requested_merchant = _clean(payload.get("merchant_id") or payload.get("merchant"))
        merchant_alias = resolve_default_merchant_alias(config_path, requested_merchant)
        if not merchant_alias:
            return {"status": "denied", "reason": "denied: merchant_required", "merchant_alias": requested_merchant}
        try:
            load_merchant_config(config_path, merchant_alias)
        except Exception:
            return {"status": "denied", "reason": "denied: unknown_merchant", "merchant_alias": merchant_alias}
        identity = SimpleNamespace(user_id="local-operator", role="local_operator", tenant_id="local", iso_id=None)
        return {"status": "ok", "identity": identity, "merchant_alias": merchant_alias}
    try:
        registry = TenantRegistry(tenant_registry_path)
    except Exception:
        return {"status": "denied", "reason": "denied: registry_invalid"}
    requested_merchant = _clean(payload.get("merchant_id") or payload.get("merchant"))
    extracted = extract_identity(
        headers,
        registry,
        mode=identity_mode,
        dev_enabled=dev_identity_enabled,
        cloudflare_validator=cloudflare_validator,
    )
    if not extracted.allowed or extracted.identity is None:
        return {"status": "denied", "reason": extracted.reason, "merchant_alias": requested_merchant}
    identity = extracted.identity
    merchant_alias = resolve_default_merchant_alias(config_path, requested_merchant)
    if not merchant_alias:
        return {"status": "denied", "reason": "denied: merchant_required", "identity": identity, "merchant_alias": requested_merchant}
    authorized = authorize_merchant(identity, merchant_alias, registry.as_auth_registry())
    if not authorized.allowed:
        return {"status": "denied", "reason": authorized.reason, "identity": identity, "merchant_alias": merchant_alias}
    return {"status": "ok", "identity": identity, "merchant_alias": merchant_alias}


def append_service_audit(
    audit_path: str | Path | None,
    *,
    action: str,
    status: str,
    reason: str | None = None,
    identity: Any = None,
    merchant_alias: str | None = None,
    error_class: str | None = None,
) -> None:
    if audit_path is None:
        return
    event = {
        "action": action,
        "status": status,
        "reason": reason,
        "identity": _audit_identity(identity),
        "merchant_alias": merchant_alias,
        "error_class": error_class,
    }
    append_audit_event(audit_path, event)


def _audit_identity(identity: Any) -> dict[str, Any] | None:
    if identity is None:
        return None
    return {
        "user_id": getattr(identity, "user_id", None),
        "role": getattr(identity, "role", None),
        "tenant_id": getattr(identity, "tenant_id", None),
        "iso_id": getattr(identity, "iso_id", None),
    }


def resolve_artifact_request(
    headers: dict[str, str],
    artifact_id: str,
    *,
    artifact_root: Path,
    tenant_registry_path: str | Path | None,
    identity_mode: str,
    dev_identity_enabled: bool,
    cloudflare_validator: CloudflareValidator | None = None,
) -> dict[str, Any]:
    if tenant_registry_path is None:
        store = ArtifactStore(artifact_root, ttl_seconds=LOCAL_ARTIFACT_TTL_SECONDS)
        metadata = store.metadata(artifact_id)
        if metadata.get("status") == "not_found":
            return {"status": "not_found"}
        merchant_alias = str(metadata.get("merchant_alias") or "")
        result = store.resolve_for_access(artifact_id, owner_user_id="local-operator", tenant_id="local", merchant_alias=merchant_alias)
        if result.status == "ok":
            return {"status": "ok", "path": result.path, "record": result.record}
        if result.status == "expired":
            return {"status": "expired"}
        if result.status == "not_found":
            return {"status": "not_found"}
        return {"status": "denied"}
    try:
        registry = TenantRegistry(tenant_registry_path)
    except Exception:
        return {"status": "denied"}
    extracted = extract_identity(
        headers,
        registry,
        mode=identity_mode,
        dev_enabled=dev_identity_enabled,
        cloudflare_validator=cloudflare_validator,
    )
    if not extracted.allowed or extracted.identity is None:
        return {"status": "denied"}
    store = ArtifactStore(artifact_root, ttl_seconds=LOCAL_ARTIFACT_TTL_SECONDS if str(getattr(identity, "tenant_id", "")) == "local" else 3600)
    metadata = store.metadata(artifact_id)
    if metadata.get("status") == "not_found":
        return {"status": "not_found"}
    merchant_alias = str(metadata.get("merchant_alias") or "")
    authorized = authorize_merchant(extracted.identity, merchant_alias, registry.as_auth_registry())
    if not authorized.allowed:
        return {"status": "denied"}
    result = store.resolve_for_access(
        artifact_id,
        owner_user_id=extracted.identity.user_id,
        tenant_id=str(extracted.identity.tenant_id or ""),
        merchant_alias=merchant_alias,
    )
    if result.status == "ok":
        return {"status": "ok", "path": result.path, "record": result.record}
    if result.status == "expired":
        return {"status": "expired"}
    if result.status == "not_found":
        return {"status": "not_found"}
    return {"status": "denied"}


SUMMARY_SAFE_TOP_LEVEL_KEYS = {"status", "merchant", "candidate_summary", "candidates"}
SUMMARY_SAFE_CANDIDATE_KEYS = {"transaction_id", "order_id", "amount", "date", "last_four", "condition", "score", "explanations"}
INVESTIGATE_SAFE_TOP_LEVEL_KEYS = {
    "status",
    "selected_transaction_id",
    "selected_candidate",
    "candidate_summary",
    "candidates",
    "match_status",
    "message",
    "error",
}


def sanitize_investigate_response(result: dict[str, Any], authorization: dict[str, Any], *, artifact_root: Path) -> dict[str, Any]:
    status = result.get("status")
    if status == "error":
        response = {"status": "error", "error": str(result.get("error") or "request_failed")}
        if isinstance(result.get("errors"), list):
            response["errors"] = result["errors"]
        return response
    sanitized = {key: value for key, value in result.items() if key in INVESTIGATE_SAFE_TOP_LEVEL_KEYS}
    if "candidates" in sanitized and isinstance(sanitized["candidates"], list):
        sanitized["candidates"] = [_sanitize_candidate(candidate) for candidate in sanitized["candidates"] if isinstance(candidate, dict)]
    if status != "completed":
        return sanitized
    raw_artifacts = result.get("artifacts")
    if not isinstance(raw_artifacts, dict):
        return sanitized
    identity = authorization.get("identity")
    if identity is None:
        return sanitized
    merchant_alias = str(authorization.get("merchant_alias") or "")
    store = ArtifactStore(artifact_root, ttl_seconds=LOCAL_ARTIFACT_TTL_SECONDS if str(getattr(identity, "tenant_id", "")) == "local" else 3600)
    artifact_refs: dict[str, str] = {}
    for source_key, output_key, artifact_type in (
        ("dashboard_file", "dashboard_artifact_id", "dashboard"),
        ("packet_file", "packet_artifact_id", "packet"),
        ("history_file", "history_artifact_id", "history"),
        ("operator_report_file", "operator_report_artifact_id", "operator_report"),
    ):
        source = raw_artifacts.get(source_key)
        if source:
            record = store.put_existing_file(
                source,
                artifact_type=artifact_type,
                owner_user_id=getattr(identity, "user_id", ""),
                tenant_id=str(getattr(identity, "tenant_id", "") or ""),
                merchant_alias=merchant_alias,
                original_name=Path(str(source)).name,
            )
            artifact_refs[output_key] = record.artifact_id
    if artifact_refs:
        sanitized["artifacts"] = artifact_refs
    return sanitized


def sanitize_search_response(result: dict[str, Any]) -> dict[str, Any]:
    if result.get("status") == "error":
        return {"status": "error", "error": str(result.get("error") or "request_failed")}
    sanitized: dict[str, Any] = {}
    for key in SUMMARY_SAFE_TOP_LEVEL_KEYS:
        if key in result:
            sanitized[key] = result[key]
    candidates = result.get("candidates")
    if isinstance(candidates, list):
        sanitized["candidates"] = [_sanitize_candidate(candidate) for candidate in candidates if isinstance(candidate, dict)]
    return sanitized


def _sanitize_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    return {key: candidate[key] for key in SUMMARY_SAFE_CANDIDATE_KEYS if key in candidate}


def _response_code_for_result(result: dict[str, Any]) -> int:
    if result.get("status") == "denied":
        return 403
    if result.get("status") == "error" and result.get("error") == "credential_resolution_failed":
        return 502
    if result.get("status") == "error":
        return 400
    return 200


def _wants_html(headers: dict[str, str]) -> bool:
    return "text/html" in headers.get("Accept", "")


def render_artifact_status_page(status: str) -> str:
    title = "Transaction detail unavailable"
    message = "This transaction detail is unavailable or you do not have access."
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
        f'<title>{_e(title)}</title><style>:root{{color-scheme:light dark;--body-bg:linear-gradient(180deg,#f8fafc,#eef2f7);--panel:#ffffff;--ink:#142033;--muted:#64748b;--line:#dbe5f0;--brand:#2458d3}}'
        ':root[data-theme="dark"]{color-scheme:dark;--body-bg:linear-gradient(180deg,#070b14,#0f172a);--panel:#111827;--ink:#f8fafc;--muted:#94a3b8;--line:#263244;--brand:#3b82f6}'
        'body{margin:0;font-family:Inter,system-ui,sans-serif;background:var(--body-bg);color:var(--ink)}'
        'main{width:min(720px,calc(100% - 32px));margin:48px auto;padding:24px;background:var(--panel);border:1px solid var(--line);border-radius:18px}'
        'p{color:var(--muted)}a{display:inline-flex;margin-top:14px;padding:10px 14px;border-radius:12px;background:var(--brand);color:white;text-decoration:none;font-weight:850}</style></head><body><main>'
        f'<h1>{_e(title)}</h1><p>{_e(message)}</p><p>Local run history is retained on this machine until you purge local artifacts.</p><a data-testid="new-search-link" href="/">New search</a>'
        '</main></body></html>'
    )


def create_human_search_handler(
    *,
    page: str,
    artifact_root: Path,
    config_path: str | Path | None,
    gateway: str,
    timeout: int,
    tenant_registry_path: str | Path | None = None,
    identity_mode: str = "production",
    dev_identity_enabled: bool = False,
    cloudflare_validator: CloudflareValidator | None = None,
    audit_path: str | Path | None = None,
    secret_store_path: str | Path | None = None,
) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "Service"
        sys_version = ""
        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path == "/api/health":
                self._send_json(200, {"status": "ok"})
                return
            if parsed.path == "/api/whoami":
                result = build_whoami_response(
                    _headers_dict(self.headers),
                    tenant_registry_path=tenant_registry_path,
                    identity_mode=identity_mode,
                    dev_identity_enabled=dev_identity_enabled,
                    cloudflare_validator=cloudflare_validator,
                )
                self._send_json(200 if result.get("status") == "ok" else 401, result)
                return
            if parsed.path in ("/", "/index.html"):
                rendered_page = render_dashboard_for_request(
                    _headers_dict(self.headers),
                    tenant_registry_path=tenant_registry_path,
                    identity_mode=identity_mode,
                    dev_identity_enabled=dev_identity_enabled,
                    config_path=config_path,
                    cloudflare_validator=cloudflare_validator,
                )
                self._send_bytes(200, rendered_page.encode("utf-8"), "text/html; charset=utf-8")
                return
            if parsed.path == "/setup":
                selected = (parse_qs(parsed.query).get("merchant") or [""])[-1]
                html_body = render_setup_wizard(config_path=config_path, selected_alias=selected)
                self._send_bytes(200, html_body.encode("utf-8"), "text/html; charset=utf-8")
                return
            if parsed.path.startswith("/api/artifacts/"):
                artifact_id = unquote(parsed.path.removeprefix("/api/artifacts/"))
                result = resolve_artifact_request(
                    _headers_dict(self.headers),
                    artifact_id,
                    artifact_root=artifact_root,
                    tenant_registry_path=tenant_registry_path,
                    identity_mode=identity_mode,
                    dev_identity_enabled=dev_identity_enabled,
                    cloudflare_validator=cloudflare_validator,
                )
                if result.get("status") == "ok":
                    path = result["path"]
                    content_type = "text/html; charset=utf-8" if path.suffix == ".html" else "text/plain; charset=utf-8"
                    self._send_bytes(200, path.read_bytes(), content_type)
                    return
                code = 410 if result.get("status") == "expired" else 403 if result.get("status") == "denied" else 404
                if _wants_html(_headers_dict(self.headers)):
                    html_body = render_artifact_status_page(str(result.get("status") or "unavailable"))
                    self._send_bytes(code, html_body.encode("utf-8"), "text/html; charset=utf-8")
                    return
                self._send_json(code, {"status": result.get("status")})
                return
            if parsed.path.startswith("/artifacts/"):
                self._send_json(404, {"status": "error", "error": "Artifact not found"})
                return
            self._send_json(404, {"status": "error", "error": "Not found"})

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path == "/setup":
                try:
                    payload = self._read_payload()
                except json.JSONDecodeError:
                    html_body = render_setup_wizard(error="Invalid setup request", config_path=config_path)
                    self._send_bytes(400, html_body.encode("utf-8"), "text/html; charset=utf-8")
                    return
                if _clean(payload.get("action")) == "delete":
                    result = delete_browser_setup(payload, config_path=config_path, secret_store_path=secret_store_path)
                    if result.get("status") == "confirm_delete":
                        html_body = render_delete_confirmation(alias=str(result.get("merchant_alias") or payload.get("original_alias") or ""), merchant_name=str(result.get("merchant_name") or payload.get("original_alias") or "merchant"))
                        self._send_bytes(200, html_body.encode("utf-8"), "text/html; charset=utf-8")
                        return
                    if result.get("status") != "completed":
                        html_body = render_setup_wizard(error=str(result.get("error") or "Delete failed"), config_path=config_path)
                        self._send_bytes(400, html_body.encode("utf-8"), "text/html; charset=utf-8")
                        return
                    self._send_redirect("/setup")
                    return
                result = save_browser_setup(payload, config_path=config_path, secret_store_path=secret_store_path)
                if result.get("status") == "confirm_update":
                    html_body = render_update_confirmation(form=payload, merchant_name=str(result.get("merchant_name") or payload.get("alias") or "merchant"), changes=result.get("changes") or [])
                    self._send_bytes(200, html_body.encode("utf-8"), "text/html; charset=utf-8")
                    return
                if result.get("status") != "completed":
                    html_body = render_setup_wizard(error=str(result.get("error") or "Setup failed"), values=payload, config_path=config_path)
                    self._send_bytes(400, html_body.encode("utf-8"), "text/html; charset=utf-8")
                    return
                self._send_redirect("/")
                return
            if parsed.path not in ("/api/search", "/api/investigate"):
                self._send_json(404, {"status": "error", "error": "Not found"})
                return
            try:
                payload = self._read_payload()
            except json.JSONDecodeError:
                self._send_json(400, {"status": "error", "error": "invalid_json"})
                return
            authorization = authorize_service_request(
                _headers_dict(self.headers),
                payload,
                tenant_registry_path=tenant_registry_path,
                identity_mode=identity_mode,
                dev_identity_enabled=dev_identity_enabled,
                cloudflare_validator=cloudflare_validator,
                config_path=config_path,
            )
            if authorization.get("status") != "ok":
                try:
                    append_service_audit(
                        audit_path,
                        action="investigate" if parsed.path == "/api/investigate" else "search",
                        status="denied",
                        reason=str(authorization.get("reason") or "denied"),
                        identity=authorization.get("identity"),
                        merchant_alias=str(authorization.get("merchant_alias") or ""),
                    )
                except AuditAppendError:
                    self._send_json(503, {"status": "error", "error": "audit_unavailable"})
                    return
                self._send_json(403, {"status": "denied", "reason": authorization.get("reason")})
                return
            action = "investigate" if parsed.path == "/api/investigate" else "search"
            try:
                append_service_audit(
                    audit_path,
                    action=action,
                    status="authorized",
                    identity=authorization.get("identity"),
                    merchant_alias=str(authorization.get("merchant_alias") or ""),
                )
            except AuditAppendError:
                self._send_json(503, {"status": "error", "error": "audit_unavailable"})
                return
            request_started = time.perf_counter()
            if parsed.path == "/api/investigate":
                result = sanitize_investigate_response(
                    run_human_investigate_request(payload, config_path=config_path, gateway=gateway, timeout=timeout, output_dir=artifact_root, secret_store_path=secret_store_path),
                    authorization,
                    artifact_root=artifact_root,
                )
            else:
                result = sanitize_search_response(run_human_search_request(payload, config_path=config_path, gateway=gateway, timeout=timeout, secret_store_path=secret_store_path))
            result["timing"] = {"server_ms": max(0, int((time.perf_counter() - request_started) * 1000))}
            try:
                if result.get("status") == "error":
                    append_service_audit(
                        audit_path,
                        action=action,
                        status="failure",
                        identity=authorization.get("identity"),
                        merchant_alias=str(authorization.get("merchant_alias") or ""),
                        error_class=str(result.get("error") or "request_failed"),
                    )
                else:
                    append_service_audit(
                        audit_path,
                        action=action,
                        status="success",
                        identity=authorization.get("identity"),
                        merchant_alias=str(authorization.get("merchant_alias") or ""),
                    )
            except AuditAppendError:
                self._send_json(503, {"status": "error", "error": "audit_unavailable"})
                return
            self._send_json(_response_code_for_result(result), result)

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            return

        def _read_payload(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0") or 0)
            raw = self.rfile.read(length).decode("utf-8") if length else ""
            if "application/json" in self.headers.get("Content-Type", ""):
                return json.loads(raw or "{}")
            parsed = parse_qs(raw)
            return {key: values[-1] for key, values in parsed.items() if values}

        def _send_json(self, code: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            self._send_bytes(code, body, "application/json; charset=utf-8")

        def _send_bytes(self, code: int, body: bytes, content_type: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_redirect(self, location: str) -> None:
            self.send_response(303)
            self.send_header("Location", location)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", "0")
            self.end_headers()

    Handler.cloudflare_validator = cloudflare_validator  # type: ignore[attr-defined]
    return Handler


def serve_human_search_dashboard(
    *,
    config_path: str | Path | None,
    gateway: str,
    host: str,
    port: int,
    timeout: int,
    output_dir: str | Path | None = None,
    tenant_registry_path: str | Path | None = None,
    identity_mode: str = "cloudflare",
    dev_identity_enabled: bool = False,
    cloudflare_issuer: str | None = None,
    cloudflare_audience: str | None = None,
    cloudflare_jwks_url: str | None = None,
    audit_path: str | Path | None = None,
) -> None:
    page = render_human_search_dashboard()
    artifact_root = Path(output_dir or "~/.payment-evidence/web-artifacts").expanduser().resolve()
    artifact_root.mkdir(parents=True, exist_ok=True)
    cloudflare_validator = _build_runtime_cloudflare_validator(
        identity_mode=identity_mode,
        cloudflare_issuer=cloudflare_issuer,
        cloudflare_audience=cloudflare_audience,
        cloudflare_jwks_url=cloudflare_jwks_url,
    )
    handler = create_human_search_handler(
        page=page,
        artifact_root=artifact_root,
        config_path=config_path,
        gateway=gateway,
        timeout=timeout,
        tenant_registry_path=tenant_registry_path,
        identity_mode=identity_mode,
        dev_identity_enabled=dev_identity_enabled,
        cloudflare_validator=cloudflare_validator,
        audit_path=audit_path,
    )
    server = ThreadingHTTPServer((host, port), handler)
    print(f"payment-search browser app listening on http://{host}:{server.server_port}", flush=True)
    server.serve_forever()


def _build_runtime_cloudflare_validator(
    *,
    identity_mode: str,
    cloudflare_issuer: str | None,
    cloudflare_audience: str | None,
    cloudflare_jwks_url: str | None,
) -> CloudflareValidator | None:
    if identity_mode != "cloudflare":
        return None
    if not cloudflare_issuer or not cloudflare_audience or not cloudflare_jwks_url:
        raise ValueError("cloudflare identity mode requires issuer, audience, and jwks url")
    return validator_from_jwks_url(
        issuer=cloudflare_issuer,
        audience=cloudflare_audience,
        jwks_url=cloudflare_jwks_url,
    )

def _merchant_and_key(form: dict[str, Any], *, config_path: str | Path | None, gateway: str, secret_store_path: str | Path | None = None) -> tuple[Any, str]:
    merchant_alias = resolve_default_merchant_alias(config_path, _clean(form.get("merchant_id") or form.get("merchant")))
    if not merchant_alias:
        raise ValueError("Merchant is required unless a default merchant or single configured merchant exists.")
    merchant = load_merchant_config(config_path, merchant_alias)
    if merchant.gateway != gateway:
        raise ValueError(f"Merchant '{merchant.alias}' is configured for gateway '{merchant.gateway}', not '{gateway}'")
    if secret_store_path and merchant.local_secret_ref:
        return merchant, LocalSecretStore(secret_store_path).get_secret_ref(merchant.local_secret_ref)
    return merchant, resolve_security_key(merchant)


def _search_args(form: dict[str, Any], *, timeout: int) -> Namespace:
    return Namespace(
        start_date=_clean(form.get("start_date")),
        end_date=_clean(form.get("end_date")),
        amount=_clean(form.get("amount")),
        last_four=_clean(form.get("last_four")),
        order_id=_clean(form.get("order_id")),
        transaction_id=_clean(form.get("transaction_id")),
        action_type=_clean(form.get("action_type")),
        condition=_clean(form.get("condition")),
        transaction_type=_clean(form.get("transaction_type")),
        result_limit=_clean(form.get("result_limit")) or "100",
        max_pages=int(_clean(form.get("max_pages")) or 5),
        timeout=timeout,
    )


def _safe_case_id(form: dict[str, Any]) -> str:
    for key in ("transaction_id", "order_id"):
        value = _clean(form.get(key))
        if value:
            return "case-" + "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in value)[:80]
    return "case-human-search"


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _e(value: Any) -> str:
    return html.escape(str(value), quote=True)
