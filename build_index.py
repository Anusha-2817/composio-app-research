"""Assemble index.html with embedded results_v2.json."""
import json
from pathlib import Path

with open("results_v2.json", encoding="utf-8") as f:
    data = json.load(f)

def get_val(field):
    if not field:
        return ""
    if isinstance(field, str):
        return field
    if isinstance(field, list):
        return field
    if isinstance(field, dict):
        return field.get("value", "")
    return ""

def first_evidence_url(r):
    for f in ("one_liner", "auth_methods", "access_tier", "api_surface", "mcp"):
        obj = r.get(f)
        if isinstance(obj, dict) and obj.get("evidence_url"):
            return obj["evidence_url"]
    urls = (r.get("_meta") or {}).get("urls_fetched", [])
    return urls[0] if urls else ""

slim = []
for r in data:
    entry = {
        "id": r["id"],
        "app": r["app"],
        "category": r.get("category", ""),
        "status": r.get("status", "ok"),
        "auth": get_val(r.get("auth_methods")),
        "tier": get_val(r.get("access_tier")),
        "mcp": get_val(r.get("mcp")),
        "buildability": r.get("buildability", ""),
        "blocker": r.get("primary_blocker", ""),
        "evidence_url": first_evidence_url(r),
    }
    if r.get("status") == "unresolved":
        entry["notes"] = r.get("notes", "")
    slim.append(entry)

compact_json = json.dumps(slim, ensure_ascii=False, separators=(",", ":"))

HTML_TEMPLATE = r'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>100-app research agent &mdash; results</title>
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{
  --bg:#fff;--bg2:#f7f8fa;--bg3:#eef0f3;--fg:#1a1a1a;--fg2:#555;--fg3:#888;
  --accent:#2563eb;--accent2:#1d4ed8;--warn:#d97706;--err:#dc2626;--ok:#059669;
  --border:#e2e4e8;--radius:6px;--card-shadow:0 1px 3px rgba(0,0,0,.08);
  --mono:'SF Mono','Cascadia Code','Consolas',monospace;
  --sans:system-ui,-apple-system,'Segoe UI',Roboto,sans-serif;
}
@media(prefers-color-scheme:dark){:root{
  --bg:#111;--bg2:#1a1a1a;--bg3:#252525;--fg:#e5e5e5;--fg2:#aaa;--fg3:#777;
  --accent:#60a5fa;--accent2:#93bbfc;--warn:#fbbf24;--err:#f87171;--ok:#34d399;
  --border:#333;--card-shadow:0 1px 3px rgba(0,0,0,.3);
}}
body{font-family:var(--sans);background:var(--bg);color:var(--fg);line-height:1.5;font-size:15px}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
.wrap{max-width:1100px;margin:0 auto;padding:0 20px}
header{padding:32px 0 24px;border-bottom:1px solid var(--border)}
header h1{font-size:22px;font-weight:600;letter-spacing:-.3px}
header p{color:var(--fg2);font-size:14px;margin-top:4px}
section{padding:32px 0;border-bottom:1px solid var(--border)}
section:last-of-type{border-bottom:none}
h2{font-size:17px;font-weight:600;margin-bottom:16px}
h3{font-size:14px;font-weight:600;margin:20px 0 8px;color:var(--fg2)}

/* Stat cards */
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:16px}
.card{background:var(--bg2);border:1px solid var(--border);border-radius:var(--radius);padding:20px;box-shadow:var(--card-shadow)}
.card .num{font-size:32px;font-weight:700;color:var(--accent);line-height:1}
.card .label{font-size:14px;color:var(--fg2);margin-top:6px}

/* Tables */
.table-wrap{overflow-x:auto;-webkit-overflow-scrolling:touch}
table{width:100%;border-collapse:collapse;font-size:13px}
th{text-align:left;font-weight:600;padding:8px 10px;border-bottom:2px solid var(--border);white-space:nowrap;position:sticky;top:0;background:var(--bg)}
td{padding:7px 10px;border-bottom:1px solid var(--border);vertical-align:top}
tr:hover td{background:var(--bg2)}
tr.insuff td{background:color-mix(in srgb, var(--warn) 8%, transparent)}
.mono{font-family:var(--mono);font-size:12px}
.tag{display:inline-block;padding:1px 7px;border-radius:3px;font-size:11px;font-weight:500;white-space:nowrap}
.tag-easy{background:#dcfce7;color:#166534}
.tag-caveats{background:#fef3c7;color:#92400e}
.tag-insuff{background:#fee2e2;color:#991b1b}
.tag-outreach{background:#e0e7ff;color:#3730a3}
.tag-wrong{background:#f3e8ff;color:#6b21a8}
.tag-unresolved{background:var(--bg3);color:var(--fg3)}
@media(prefers-color-scheme:dark){
  .tag-easy{background:#064e3b;color:#6ee7b7}
  .tag-caveats{background:#78350f;color:#fde68a}
  .tag-insuff{background:#7f1d1d;color:#fca5a5}
  .tag-outreach{background:#312e81;color:#a5b4fc}
  .tag-wrong{background:#4c1d95;color:#c4b5fd}
}
.delta{color:var(--ok);font-weight:600}
.delta-neg{color:var(--err)}
.gold-pass{color:var(--ok)}
.gold-fail{color:var(--err)}

/* Filters */
.filters{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:12px}
.filters select{padding:6px 10px;border:1px solid var(--border);border-radius:var(--radius);background:var(--bg);color:var(--fg);font-size:13px}
.filters input{padding:6px 10px;border:1px solid var(--border);border-radius:var(--radius);background:var(--bg);color:var(--fg);font-size:13px;width:200px}

/* Verification bars */
.bar-wrap{display:flex;align-items:center;gap:10px;margin:8px 0}
.bar{height:22px;border-radius:3px;display:flex;overflow:hidden;flex:1;max-width:400px}
.bar span{display:block;height:100%}
.bar .verified{background:var(--ok)}
.bar .ok-nf{background:var(--bg3)}
.bar .unverif{background:var(--warn)}
.bar .flagged{background:var(--err)}
.bar-label{font-size:13px;font-weight:600;min-width:50px}

/* Pipeline */
.pipeline{display:flex;gap:0;flex-wrap:wrap;margin:12px 0}
.stage{flex:1;min-width:200px;padding:16px;background:var(--bg2);border:1px solid var(--border);position:relative}
.stage:first-child{border-radius:var(--radius) 0 0 var(--radius)}
.stage:last-child{border-radius:0 var(--radius) var(--radius) 0}
.stage h3{margin:0 0 6px;color:var(--fg);font-size:14px}
.stage p{font-size:13px;color:var(--fg2);margin:0}
.stage .tag{margin-top:8px}

/* Mismatch table */
.mm-gold{background:color-mix(in srgb, var(--ok) 10%, transparent)}
.mm-agent{background:color-mix(in srgb, var(--err) 10%, transparent)}

/* Footer */
footer{padding:20px 0;text-align:center;color:var(--fg3);font-size:12px;border-top:1px solid var(--border)}

@media(max-width:700px){
  .cards{grid-template-columns:1fr}
  .pipeline{flex-direction:column}
  .stage{border-radius:0!important}
  .stage:first-child{border-radius:var(--radius) var(--radius) 0 0!important}
  .stage:last-child{border-radius:0 0 var(--radius) var(--radius)!important}
  .filters{flex-direction:column}
  .filters input,.filters select{width:100%}
}
</style>
</head>
<body>
<script type="application/json" id="data">%%JSON%%</script>

<div class="wrap">
<header>
  <h1>100-app research agent</h1>
  <p>Automated API capability extraction &mdash; Composio take-home &mdash; results v2</p>
  <p style="margin-top:8px;font-size:13px">
    Raw data: <a href="results_v2.json">results_v2.json</a> &middot;
    <a href="results_v1.json">results_v1.json</a> &middot;
    <a href="verification_report_v2.json">verification_report_v2.json</a> &middot;
    <a href="gold_set.json">gold_set.json</a>
  </p>
</header>

<!-- Section 1: Patterns -->
<section>
<h2>Patterns</h2>
<div class="cards">
  <div class="card">
    <div class="num">35%</div>
    <div class="label">of apps are per-tenant &mdash; no single API base URL. Each customer gets their own instance, so a generic integration must discover the base URL at runtime. This is the largest structural blocker.</div>
  </div>
  <div class="card">
    <div class="num">6 of 100</div>
    <div class="label">are truly sales-gated (contact_sales or partner_gated). 16 are customer-admin-gated, which looks like a wall but is a different problem: the API exists and is documented, but someone inside the customer org has to flip a switch.</div>
  </div>
  <div class="card">
    <div class="num">27 of 100</div>
    <div class="label">already ship an MCP server (22 full read-write, 5 read-only). These apps have opted in to the agent ecosystem &mdash; integration work is minimal.</div>
  </div>
</div>
</section>

<!-- Section 2: Verification -->
<section>
<h2>Verification</h2>

<h3>Evidence verification (string matching, zero LLM calls)</h3>
<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px;margin-bottom:16px">
  <div class="card" style="padding:14px">
    <div style="font-size:13px;color:var(--fg2)">Verified rate</div>
    <div style="font-size:20px;font-weight:700">v1 70.4% <span class="delta">&rarr; v2 74.6%</span></div>
  </div>
  <div class="card" style="padding:14px">
    <div style="font-size:13px;color:var(--fg2)">Fabricated citations caught</div>
    <div style="font-size:20px;font-weight:700;color:var(--err)">34</div>
  </div>
  <div class="card" style="padding:14px">
    <div style="font-size:13px;color:var(--fg2)">Flags resolved</div>
    <div style="font-size:20px;font-weight:700">48 of 48</div>
  </div>
  <div class="card" style="padding:14px">
    <div style="font-size:13px;color:var(--fg2)">Downgraded to not_found</div>
    <div style="font-size:20px;font-weight:700">23</div>
    <div style="font-size:12px;color:var(--fg3)">rather than left unverified</div>
  </div>
</div>

<p style="font-size:13px;color:var(--fg2);margin-bottom:16px">CHECK normalizes both the evidence_snippet and the cached page text (lowercase, strip punctuation, collapse whitespace), then checks whether the snippet appears as a substring. If it doesn't, the field is flagged. The v2 repair loop re-extracts flagged fields with an exact-substring instruction, then re-CHECKs. Fields that fail twice are downgraded to not_found.</p>

<h3>Buildability correction (deterministic, independent of citation repair)</h3>
<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px;margin-bottom:16px">
  <div class="card" style="padding:14px">
    <div style="font-size:13px;color:var(--fg2)">v1 easy_win</div>
    <div style="font-size:20px;font-weight:700">63 <span class="delta-neg">&rarr; 53</span></div>
  </div>
  <div class="card" style="padding:14px">
    <div style="font-size:13px;color:var(--fg2)">Moved to insufficient_evidence</div>
    <div style="font-size:20px;font-weight:700">36</div>
  </div>
</div>
<p style="font-size:13px;color:var(--fg2);margin-bottom:16px">A deterministic post-processing rule: if <code style="font-family:var(--mono);font-size:12px;background:var(--bg3);padding:1px 5px;border-radius:3px">auth_methods</code> or <code style="font-family:var(--mono);font-size:12px;background:var(--bg3);padding:1px 5px;border-radius:3px">access_tier</code> is not_found, buildability cannot be asserted. This moved 36 apps from easy_win to insufficient_evidence. This is independent of the citation repair loop &mdash; one fixed fabricated evidence, this one fixed unsupported inference. The LLM was asserting easy_win for apps where it had no evidence for the fields that determine buildability.</p>

<h3>Gold comparison (7 hand-researched apps)</h3>
<p style="font-size:13px;color:var(--fg2);margin-bottom:12px">The agent reached only <strong>4 of 15</strong> pages a human visited. Most gold evidence URLs were never fetched, limiting what the agent could find.</p>

<div class="table-wrap">
<table id="gold-table">
<thead><tr><th>Field</th><th>Coverage</th><th>Precision</th><th>Detail</th></tr></thead>
<tbody>
<tr><td>instance_model</td><td>100%</td><td>83%</td><td>5/6 correct</td></tr>
<tr><td>auth_methods (exact)</td><td>100%</td><td>0%</td><td>0/6 &mdash; every app has at least one method missing or extra</td></tr>
<tr><td>auth_methods (overlap)</td><td>100%</td><td>44%</td><td>avg Jaccard similarity</td></tr>
<tr><td>access_tier</td><td>86%</td><td>67%</td><td>4/6 correct</td></tr>
<tr><td>api_surface.surface_types</td><td>86%</td><td>33%</td><td>2/6 &mdash; agent misses secondary types (GraphQL, webhook, library)</td></tr>
<tr><td>api_surface.breadth</td><td>83%</td><td>60%</td><td>3/5 correct</td></tr>
<tr><td>api_surface.write_access</td><td>83%</td><td>100%</td><td>5/5 correct</td></tr>
<tr><td>mcp</td><td>100%</td><td>75%</td><td>3/4 &mdash; missed Stripe's MCP server</td></tr>
<tr><td>buildability</td><td>100%</td><td>57%</td><td>4/7 correct</td></tr>
<tr><td>primary_blocker</td><td>100%</td><td>43%</td><td>3/7 correct</td></tr>
</tbody>
</table>
</div>

<h3>Mismatch table (gold vs agent)</h3>
<div class="table-wrap">
<table id="mismatch-table">
<thead><tr><th>App</th><th>Field</th><th class="mm-gold">Gold</th><th class="mm-agent">Agent</th></tr></thead>
<tbody>
<tr><td>DealCloud</td><td>auth_methods</td><td class="mm-gold mono">api_key, oauth2_client_credentials</td><td class="mm-agent mono">api_key, oauth2_authcode, oauth2_client_credentials</td></tr>
<tr><td>DealCloud</td><td>surface_types</td><td class="mm-gold mono">graphql, library, rest, webhook</td><td class="mm-agent mono">library, rest</td></tr>
<tr><td>DealCloud</td><td>breadth</td><td class="mm-gold mono">broad</td><td class="mm-agent mono">moderate</td></tr>
<tr><td>DealCloud</td><td>primary_blocker</td><td class="mm-gold mono">per_tenant_schema</td><td class="mm-agent mono">admin_enablement</td></tr>
<tr><td>Sherlock</td><td>one_liner</td><td class="mm-gold mono">Command-line tool that checks whether a username exists across hundreds of social sites</td><td class="mm-agent mono">Hunt down social media accounts by username across 400+ social networks</td></tr>
<tr><td>Sherlock</td><td>auth_methods</td><td class="mm-gold mono">not_applicable</td><td class="mm-agent mono">none</td></tr>
<tr><td>Sherlock</td><td>surface_types</td><td class="mm-gold mono">cli, library</td><td class="mm-agent mono">cli</td></tr>
<tr><td>Sherlock</td><td>primary_blocker</td><td class="mm-gold mono">third_party_rate_limits</td><td class="mm-agent mono">not_a_service</td></tr>
<tr><td>GitHub</td><td>auth_methods</td><td class="mm-gold mono">api_key, basic, oauth2_authcode, token</td><td class="mm-agent mono">basic, token</td></tr>
<tr><td>GitHub</td><td>surface_types</td><td class="mm-gold mono">graphql, rest, webhook</td><td class="mm-agent mono">rest</td></tr>
<tr><td>Stripe</td><td>auth_methods</td><td class="mm-gold mono">api_key, oauth2_authcode</td><td class="mm-agent mono">api_key</td></tr>
<tr><td>Stripe</td><td>mcp</td><td class="mm-gold mono">full</td><td class="mm-agent mono">none</td></tr>
<tr><td>Twenty</td><td>instance_model</td><td class="mm-gold mono">self_hosted</td><td class="mm-agent mono">per_tenant</td></tr>
<tr><td>Twenty</td><td>auth_methods</td><td class="mm-gold mono">api_key, oauth2_authcode</td><td class="mm-agent mono">oauth2_authcode</td></tr>
<tr><td>Twenty</td><td>access_tier</td><td class="mm-gold mono">self_serve_free</td><td class="mm-agent mono">self_serve_paid</td></tr>
<tr><td>Twenty</td><td>surface_types</td><td class="mm-gold mono">graphql, rest</td><td class="mm-agent mono">graphql, rest, webhook</td></tr>
<tr><td>Twenty</td><td>buildability</td><td class="mm-gold mono">buildable_with_caveats</td><td class="mm-agent mono">easy_win</td></tr>
<tr><td>WhatsApp Business</td><td>auth_methods</td><td class="mm-gold mono">oauth2_authcode, token</td><td class="mm-agent mono">token</td></tr>
<tr><td>WhatsApp Business</td><td>access_tier</td><td class="mm-gold mono">app_review</td><td class="mm-agent mono">customer_admin_gated</td></tr>
<tr><td>WhatsApp Business</td><td>breadth</td><td class="mm-gold mono">moderate</td><td class="mm-agent mono">broad</td></tr>
<tr><td>WhatsApp Business</td><td>buildability</td><td class="mm-gold mono">needs_outreach</td><td class="mm-agent mono">buildable_with_caveats</td></tr>
<tr><td>WhatsApp Business</td><td>primary_blocker</td><td class="mm-gold mono">app_review</td><td class="mm-agent mono">admin_enablement</td></tr>
<tr><td>PitchBook</td><td>buildability</td><td class="mm-gold mono">needs_outreach</td><td class="mm-agent mono">insufficient_evidence</td></tr>
<tr><td>PitchBook</td><td>primary_blocker</td><td class="mm-gold mono">no_public_docs</td><td class="mm-agent mono">unknown</td></tr>
</tbody>
</table>
</div>
</section>

<!-- Section 3: The 100 -->
<section>
<h2>The 100</h2>
<div class="filters">
  <select id="filter-cat"><option value="">All categories</option></select>
  <select id="filter-build"><option value="">All buildability</option></select>
  <input id="filter-search" type="text" placeholder="Search app name...">
</div>
<div class="table-wrap" style="max-height:600px;overflow-y:auto">
<table id="app-table">
<thead><tr>
  <th>#</th><th>App</th><th>Category</th><th>Auth</th><th>Access tier</th>
  <th>MCP</th><th>Buildability</th><th>Blocker</th><th>Evidence</th>
</tr></thead>
<tbody id="app-tbody"></tbody>
</table>
</div>
<p id="row-count" style="font-size:12px;color:var(--fg3);margin-top:6px"></p>
</section>

<!-- Section 4: The agent -->
<section>
<h2>The agent</h2>
<p style="font-size:13px;color:var(--fg2);margin-bottom:12px"><strong>Core rule:</strong> no field may come from model memory. Every value must have a verbatim evidence_snippet from fetched page text, verified by automated string matching.</p>
<div class="pipeline">
  <div class="stage">
    <h3>1. Resolve</h3>
    <p>Map app hints to seed URLs. Expand each with link discovery: up to 5 same-domain + 2 offsite doc links, ranked by keyword (auth, pricing, mcp, api).</p>
  </div>
  <div class="stage">
    <h3>2. Fetch</h3>
    <p>Tier-1: HTTP GET + readability. Tier-2: Playwright headless browser for JS-rendered pages. Entity-scoped link discovery prevents cross-app contamination (e.g. GitHub repo pages vs github.com/pricing).</p>
  </div>
  <div class="stage">
    <h3>3. Extract</h3>
    <p>Gemini 3.1 Flash Lite with native structured output. 24K char context budget. Pages ranked by entity confidence and keyword density. No model fallback chain.</p>
    <span class="tag tag-caveats">LLM</span>
  </div>
  <div class="stage">
    <h3>4. Check</h3>
    <p>Pure string matching. Normalize text (lowercase, strip punctuation, collapse whitespace). If snippet is not a substring of source page text, flag it. Zero LLM calls.</p>
    <span class="tag tag-easy">no LLM</span>
  </div>
</div>
<p style="font-size:13px;color:var(--fg2);margin-top:12px"><strong>Where humans were needed:</strong> choosing the 100-app list, writing the schema and enum values, debugging the fetcher (readability regressions, entity scoping), fixing rate-limit errors, pinning the Gemini model after 404s and JSON failures, and writing the 7-app gold set.</p>
</section>

<!-- Section 5: Proof -->
<section>
<h2>Proof</h2>
<p style="font-size:14px;margin-bottom:16px">Every result is reproducible from the public repo. A reviewer can re-run the full pipeline or spot-check a single app.</p>

<h3>Repository</h3>
<p style="font-size:14px;margin-bottom:16px"><a href="https://github.com/Anusha-2817/composio-app-research.git">github.com/Anusha-2817/composio-app-research</a></p>

<h3>Setup</h3>
<div style="background:var(--bg2);border:1px solid var(--border);border-radius:var(--radius);padding:14px;margin-bottom:16px">
<pre style="font-family:var(--mono);font-size:13px;line-height:1.6;white-space:pre-wrap;color:var(--fg)"><code>pip install -r requirements.txt
playwright install chromium
# add GEMINI_API_KEY to .env</code></pre>
</div>

<h3>Full run</h3>
<div style="background:var(--bg2);border:1px solid var(--border);border-radius:var(--radius);padding:14px;margin-bottom:16px">
<pre style="font-family:var(--mono);font-size:13px;line-height:1.6;white-space:pre-wrap;color:var(--fg)"><code>python fetch_pipeline.py
python extract_pipeline.py
python check_pipeline.py
python repair_v2.py
python score.py</code></pre>
</div>

<h3>Single-app spot check</h3>
<p style="font-size:13px;color:var(--fg2);margin-bottom:8px">A reviewer can verify one app in seconds without re-running the full pipeline:</p>
<div style="background:var(--bg2);border:1px solid var(--border);border-radius:var(--radius);padding:14px">
<pre style="font-family:var(--mono);font-size:13px;line-height:1.6;white-space:pre-wrap;color:var(--fg)"><code>python extract_pipeline.py --apps "DealCloud" --show "DealCloud"</code></pre>
</div>
</section>

<!-- Section 6: Limitations -->
<section>
<h2>Limitations, named</h2>
<div style="font-size:14px;line-height:1.7">
<p><strong>Fetcher blind spots.</strong> The link-discovery stage reaches API reference docs reliably but misses pricing pages, MCP documentation, and partner/integration pages. The agent fetched only 4 of 15 pages a human visited for the same 7 apps. Stripe's MCP server page was never fetched, producing a false negative on a flagship feature.</p>

<p style="margin-top:12px"><strong>Support/Helpdesk: systematic failure.</strong> 8 of 10 apps in this category returned insufficient_evidence. Zendesk, Intercom, Freshdesk, Front, LiveAgent, Help Scout, Gorgias, and Gladly all have well-documented APIs, but the fetcher landed on marketing pages and support portals instead of developer docs. This is a category-level blind spot, not an app-level one.</p>

<p style="margin-top:12px"><strong>Name collision.</strong> Otter.ai (meeting transcription) vs tryotter.com (restaurant delivery) &mdash; the agent fetched the wrong domain. This fooled both the agent and the human reviewer during early iterations. Ambiguous app names with no hint URL are a structural weakness of keyword-based resolution.</p>

<p style="margin-top:12px"><strong>Negative findings are unverifiable.</strong> When the agent says "no MCP server," there is no page text to quote. 44 of 99 ok apps have mcp=none with no evidence snippet. These were reclassified as unverifiable_negative rather than flagged, but they remain structurally untestable. The agent cannot prove absence.</p>

<p style="margin-top:12px"><strong>Thin gold set.</strong> 7 of 100 apps were hand-researched. This sample is too small for statistical confidence in per-field precision numbers. The gold set over-represents apps with good documentation (Stripe, GitHub) and under-represents the long tail of poorly-documented tools.</p>
</div>
</section>

<footer>
  Built by a research agent. Verified by string matching. Audited against 7 hand-researched apps.
</footer>
</div>

<script>
(function(){
  const data = JSON.parse(document.getElementById('data').textContent);

  function val(field) {
    if (!field) return '';
    if (typeof field === 'string') return field;
    if (Array.isArray(field)) return field.join(', ');
    return '';
  }

  function buildTag(b) {
    const cls = {
      'easy_win':'tag-easy','buildable_with_caveats':'tag-caveats',
      'insufficient_evidence':'tag-insuff','needs_outreach':'tag-outreach',
      'wrong_shape':'tag-wrong','unresolved':'tag-unresolved'
    };
    return '<span class="tag '+(cls[b]||'')+'">'+(b||'?').replace(/_/g,' ')+'</span>';
  }

  // Populate filters
  const cats = [...new Set(data.map(r=>r.category).filter(Boolean))].sort();
  const builds = [...new Set(data.map(r=>r.buildability).filter(Boolean))].sort();
  const catSel = document.getElementById('filter-cat');
  const buildSel = document.getElementById('filter-build');
  cats.forEach(c=>{const o=document.createElement('option');o.value=c;o.textContent=c;catSel.appendChild(o)});
  builds.forEach(b=>{const o=document.createElement('option');o.value=b;o.textContent=b.replace(/_/g,' ');buildSel.appendChild(o)});

  function getEvidenceUrl(r) {
    return r.evidence_url || '';
  }

  function renderTable() {
    const catV = catSel.value;
    const buildV = buildSel.value;
    const searchV = document.getElementById('filter-search').value.toLowerCase();
    const tbody = document.getElementById('app-tbody');
    let html = '';
    let count = 0;
    data.forEach(r => {
      if (r.status === 'unresolved') {
        if (catV && r.category !== catV) return;
        if (buildV) return;
        if (searchV && !r.app.toLowerCase().includes(searchV)) return;
        count++;
        html += '<tr class="insuff"><td>'+r.id+'</td><td>'+r.app+'</td><td>'+r.category+'</td>'
          +'<td colspan="5" style="color:var(--fg3)">unresolved &mdash; '+(r.notes||'')+'</td><td></td></tr>';
        return;
      }
      if (catV && r.category !== catV) return;
      if (buildV && r.buildability !== buildV) return;
      if (searchV && !r.app.toLowerCase().includes(searchV)) return;
      count++;
      const isInsuff = r.buildability === 'insufficient_evidence';
      const evUrl = getEvidenceUrl(r);
      const mcp = val(r.mcp);
      const mpcDisplay = mcp === 'none' ? '<span style="color:var(--fg3)">none</span>'
        : mcp === 'full' ? '<span style="color:var(--ok);font-weight:600">full</span>'
        : mcp === 'read_only' ? '<span style="color:var(--accent)">read_only</span>'
        : mcp;
      html += '<tr'+(isInsuff?' class="insuff"':'')+'>'
        +'<td>'+r.id+'</td>'
        +'<td><strong>'+r.app+'</strong></td>'
        +'<td style="font-size:12px">'+r.category+'</td>'
        +'<td class="mono" style="font-size:12px">'+val(r.auth)+'</td>'
        +'<td class="mono" style="font-size:12px">'+(val(r.tier)||'').replace(/_/g,'_')+'</td>'
        +'<td>'+mpcDisplay+'</td>'
        +'<td>'+buildTag(r.buildability)+'</td>'
        +'<td class="mono" style="font-size:12px">'+(r.blocker||'').replace(/_/g,' ')+'</td>'
        +'<td>'+(evUrl?'<a href="'+evUrl+'" target="_blank" rel="noopener" style="font-size:12px">source</a>':'')+'</td>'
        +'</tr>';
    });
    tbody.innerHTML = html;
    document.getElementById('row-count').textContent = count + ' of ' + data.length + ' apps shown';
  }

  catSel.addEventListener('change', renderTable);
  buildSel.addEventListener('change', renderTable);
  document.getElementById('filter-search').addEventListener('input', renderTable);
  renderTable();
})();
</script>
</body>
</html>'''

# Insert JSON
html = HTML_TEMPLATE.replace("%%JSON%%", compact_json)

with open("index.html", "w", encoding="utf-8") as f:
    f.write(html)

print(f"Written index.html ({len(html):,} bytes)")
