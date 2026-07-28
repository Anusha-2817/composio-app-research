# Composio Take-Home: 100-App Research Agent

## Goal

Research 100 apps and capture, per app: category, one-line description,
auth method(s), access tier, API surface, MCP availability, buildability
verdict, primary blocker, and evidence.

Accuracy is the graded metric, not coverage. A confident wrong answer is
worse than an honest "not_found". "This app is gated behind sales, here
is the evidence" is a CORRECT finding, not a failure.

## Hard rule — the most important thing in this file

NO FIELD MAY COME FROM MODEL MEMORY.

Every field is extracted only from text actually fetched from a URL.
If the fetched text does not say it, the value is "not_found".
Every non-null field requires an evidence_snippet copied VERBATIM from
the fetched page — it will be string-matched against the source, so
paraphrasing fails the check.

Fabricated evidence is the primary failure mode this pipeline is
engineered against. Do not add fallbacks that let the model answer from
general knowledge when a page is unclear. A high not_found rate is a
finding, not a bug.

## Schema

```json
{
  "app": "DealCloud",
  "id": 10,
  "category": "CRM and Sales",
  "status": "ok | unresolved | error",

  "one_liner": {
    "value": "string",
    "evidence_url": "string",
    "evidence_snippet": "verbatim from page",
    "confidence": "high | medium | low"
  },

  "instance_model": "global | per_tenant | self_hosted | not_applicable | not_found",

  "auth_methods": {
    "value": [
      "oauth2_authcode",
      "oauth2_client_credentials",
      "api_key",
      "basic",
      "token",
      "none",
      "not_applicable"
    ],
    "evidence_url": "string",
    "evidence_snippet": "verbatim from page",
    "confidence": "high | medium | low"
  },

  "access_tier": {
    "value": "open_no_auth | self_serve_free | self_serve_paid | customer_admin_gated | partner_gated | contact_sales | not_found",
    "evidence_url": "string",
    "evidence_snippet": "verbatim from page",
    "confidence": "high | medium | low"
  },

  "api_surface": {
    "surface_types": [
      "rest",
      "graphql",
      "cli",
      "library",
      "webhook",
      "soap",
      "none"
    ],
    "breadth": "narrow | moderate | broad | not_found",
    "write_access": "read_only | read_write | not_found",
    "evidence_url": "string",
    "evidence_snippet": "verbatim from page",
    "confidence": "high | medium | low"
  },

  "mcp": {
    "value": "none | read_only | full | not_found",
    "evidence_url": "string",
    "evidence_snippet": "verbatim from page",
    "confidence": "high | medium | low"
  },

  "buildability": "easy_win | buildable_with_caveats | needs_outreach | blocked | wrong_shape",

  "primary_blocker": "none | admin_enablement | paid_plan | partner_approval | app_review | no_public_docs | per_tenant_schema | third_party_rate_limits | not_a_service",

  "notes": "free text for anything the enums cannot express",

  "_meta": {
    "urls_fetched": ["..."],
    "pages_needing_browser": ["..."],
    "llm_calls": 0,
    "cost_usd": 0.0,
    "latency_s": 0.0
  }
}
```

### Enum notes (each traces to a real app, do not simplify away)

- `customer_admin_gated` — you must already be a paying customer AND an
  admin inside your own org must enable API access. Distinct from
  contact_sales. (DealCloud)
- `per_tenant` instance_model — no single API base URL; each customer
  gets their own subdomain or self-hosted instance. (DealCloud, Twenty)
- `mcp: read_only` — an MCP server exists but exposes no write tools.
  Changes the buildability answer. (DealCloud)
- `wrong_shape` — the entry is not a hosted service at all. A CLI or
  library, callable via sandboxed execution rather than an API.
  (Sherlock, Mermaid CLI)
- `third_party_rate_limits` — blocker is not auth; the tool hits
  third-party sites unauthenticated and expects to be blocked. (Sherlock)
- `per_tenant_schema` — data model is customer-customizable, so one
  fixed toolkit cannot be shipped. (DealCloud Schema Contracts)

## Pipeline

### 1. RESOLVE

- `apps.json` has name + hint URL for most apps. Use the hint as the seed.
- No hint → one web search: `"<app> API documentation"`, take the top
  developer-domain result.
- Nothing resolves → `status: unresolved`. This is a valid outcome, not
  an error. Do not invent a URL.

### 2. FETCH

- HTTP GET + readability text extraction.
- Cache every fetch to `cache/{sha1(url)}.json` as
  `{url, fetched_at, text, method}`. NEVER fetch the same URL twice.
- If extracted text < 500 chars OR contains "enable JavaScript" or
  similar → mark `needs_browser` and retry headless.
- If fields are still `not_found` after the seed page, follow up to 3
  same-domain links (prefer paths containing auth, token, apikey,
  pricing, mcp, getting-started). Auth and pricing usually live on
  different pages than the seed.

### 3. EXTRACT

- The LLM sees ONLY the fetched text. It must never use its own knowledge.
- Returns schema JSON. `not_found` is a first-class allowed answer.
- Every non-null field needs a verbatim `evidence_snippet` and the
  `evidence_url` of the page it came from. Evidence is PER FIELD — auth
  and access_tier commonly cite different URLs.

### 4. CHECK

NO LLM. Pure string matching.

```
for each field:
  value == "not_found"                          -> ok_not_found
  no evidence_snippet                           -> FLAG_no_evidence
  normalize(snippet) not in normalize(page_text)-> FLAG_fabricated_snippet
  else                                          -> verified
```

normalize = lowercase, collapse whitespace, strip punctuation.
Emit `verification_report.json` with flag counts per field and per app.

## Engineering requirements

- Concurrency ~10, with rate-limit handling.
- One app failing must never crash the run. Catch, log, `status: error`,
  continue.
- Write `results_v1.json` and DO NOT overwrite it. The v1 → v2 accuracy
  comparison is a graded deliverable and cannot be reconstructed later.
- Log LLM cost and latency per app.
- Single-app runnable trigger: `python research.py --app "Linear"`.

## Verification plan (the graded part)

- `gold/gold_set.json` — 20 apps researched BY HAND, no AI, 2 per
  category, deliberately mixing easy and obscure.
- `score.py` compares results against gold and reports accuracy
  PER FIELD, never blended, plus a diff table of every mismatch.
- Expected weak field: `access_tier` — it is pricing/partner-program
  information, not API-docs information, so a docs-only fetcher
  structurally cannot know it. Diagnosing this is the point.
- v2 = targeted fixes based on the v1 diff. Rescore. Report the delta.

## Out of scope for this run

- Per-capability access tiers. Some apps gate individual features
  separately (DealCloud Publications requires a separate account-manager
  enablement). We capture app-level tier only and flag the simplification
  on the page.
