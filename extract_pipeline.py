"""EXTRACT stage — assembles cached page text, sends to Gemini, returns schema JSON.

No model knowledge allowed. Every non-null field needs verbatim evidence_snippet.
"""

import json
import hashlib
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

from google import genai

CACHE_DIR = Path(__file__).parent / "cache"
APPS_JSON = Path(__file__).parent / "apps.json"
RESULTS_DIR = Path(__file__).parent

MAX_CONTEXT_CHARS = 24000
MAX_PAGE_CHARS = 6000

SECTION_KEYWORDS = [
    "auth", "oauth", "token", "api-key", "apikey", "credential", "bearer",
    "pricing", "plans", "free", "enterprise", "tier",
    "mcp", "tools", "tool",
    "endpoint", "rest", "graphql", "webhook", "sdk", "cli",
    "rate limit", "scope", "permission",
    "getting-started", "quickstart", "install",
]

PAGE_PRIORITY_KEYWORDS = [
    ("auth", 100), ("token", 100), ("oauth", 100), ("credential", 100),
    ("apikey", 90), ("api-key", 90), ("api_key", 90),
    ("pricing", 80), ("plans", 80),
    ("mcp", 70), ("tools", 70),
    ("getting-started", 50), ("quickstart", 50), ("developer", 40),
    ("reference", 40), ("endpoint", 40), ("api", 30),
]

GEMINI_MODEL = "gemini-3.1-flash-lite"


# ---------------------------------------------------------------------------
# Cache reading (mirrors fetch_pipeline cache_key)
# ---------------------------------------------------------------------------

def cache_key(url: str, method: str = "GET") -> str:
    tag = url if method == "GET" else f"{method}::{url}"
    return hashlib.sha1(tag.encode()).hexdigest()


def load_cached_text(url: str) -> dict | None:
    for method in ("browser", "GET"):
        path = CACHE_DIR / f"{cache_key(url, method)}.json"
        if path.exists():
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            if len(data.get("text", "")) > 0:
                return data
    return None


# ---------------------------------------------------------------------------
# Context assembly
# ---------------------------------------------------------------------------

def page_keyword_score(url: str, text_preview: str) -> int:
    combined = (url + " " + text_preview[:500]).lower()
    best = 0
    for kw, score in PAGE_PRIORITY_KEYWORDS:
        if kw in combined:
            best = max(best, score)
    return best


def smart_truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text

    lines = text.split("\n")
    scored_blocks = []
    block_start = 0

    for i, line in enumerate(lines):
        lower = line.lower()
        score = sum(1 for kw in SECTION_KEYWORDS if kw in lower)
        scored_blocks.append((i, score, line))

    context_radius = 8
    keep = set()
    keep.update(range(0, min(15, len(lines))))

    for i, score, _ in scored_blocks:
        if score > 0:
            for j in range(max(0, i - context_radius),
                           min(len(lines), i + context_radius + 1)):
                keep.add(j)

    kept_lines = []
    last_kept = -2
    for i in sorted(keep):
        if i - last_kept > 1:
            kept_lines.append("\n[...]\n")
        kept_lines.append(lines[i])
        last_kept = i

    result = "\n".join(kept_lines)
    if len(result) > max_chars:
        result = result[:max_chars] + "\n[truncated]"
    return result


def assemble_context(app_result: dict) -> tuple[str, int, int]:
    """Build LLM context from cached pages. Returns (context_text, chars_sent, pages_used)."""
    pages = app_result.get("pages", [])
    if not pages:
        return "", 0, 0

    page_data = []
    for p in pages:
        url = p["url"]
        cached = load_cached_text(url)
        if not cached or not cached.get("text", "").strip():
            continue

        text = cached["text"]
        conf = p.get("source_entity_confidence", "high")
        kw_score = page_keyword_score(url, text)
        offsite = p.get("offsite_from_seed", False)

        conf_rank = 0 if conf == "high" else 1
        page_data.append({
            "url": url,
            "text": text,
            "confidence": conf,
            "offsite": offsite,
            "kw_score": kw_score,
            "sort_key": (conf_rank, -kw_score),
        })

    page_data.sort(key=lambda x: x["sort_key"])

    context_parts = []
    total_chars = 0
    pages_used = 0

    for pd in page_data:
        remaining = MAX_CONTEXT_CHARS - total_chars
        if remaining <= 200:
            break

        page_budget = min(MAX_PAGE_CHARS, remaining)
        text = smart_truncate(pd["text"], page_budget)

        if not text.strip():
            continue

        conf_tag = ""
        if pd["confidence"] == "low":
            conf_tag = " [entity_confidence: LOW — this page may describe the hosting platform, not the app being researched]"
        if pd["offsite"]:
            conf_tag += " [offsite link from seed page]"

        header = f"=== PAGE: {pd['url']}{conf_tag} ==="
        block = f"{header}\n{text}\n"

        context_parts.append(block)
        total_chars += len(block)
        pages_used += 1

    return "\n".join(context_parts), total_chars, pages_used


# ---------------------------------------------------------------------------
# Extraction prompt and schema
# ---------------------------------------------------------------------------

EVIDENCE_FIELD = {
    "type": "OBJECT",
    "properties": {
        "value": {"type": "STRING"},
        "evidence_url": {"type": "STRING"},
        "evidence_snippet": {"type": "STRING"},
        "confidence": {"type": "STRING", "enum": ["high", "medium", "low"]},
        "entity_risk": {"type": "BOOLEAN"},
    },
    "required": ["value", "evidence_url", "evidence_snippet", "confidence"],
}

EVIDENCE_FIELD_ARRAY_VALUE = {
    "type": "OBJECT",
    "properties": {
        "value": {"type": "ARRAY", "items": {"type": "STRING"}},
        "evidence_url": {"type": "STRING"},
        "evidence_snippet": {"type": "STRING"},
        "confidence": {"type": "STRING", "enum": ["high", "medium", "low"]},
        "entity_risk": {"type": "BOOLEAN"},
    },
    "required": ["value", "evidence_url", "evidence_snippet", "confidence"],
}

API_SURFACE_FIELD = {
    "type": "OBJECT",
    "properties": {
        "surface_types": {"type": "ARRAY", "items": {"type": "STRING"}},
        "breadth": {"type": "STRING"},
        "write_access": {"type": "STRING"},
        "evidence_url": {"type": "STRING"},
        "evidence_snippet": {"type": "STRING"},
        "confidence": {"type": "STRING", "enum": ["high", "medium", "low"]},
        "entity_risk": {"type": "BOOLEAN"},
    },
    "required": ["surface_types", "breadth", "write_access",
                  "evidence_url", "evidence_snippet", "confidence"],
}

RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "app": {"type": "STRING"},
        "id": {"type": "INTEGER"},
        "category": {"type": "STRING"},
        "one_liner": EVIDENCE_FIELD,
        "instance_model": EVIDENCE_FIELD,
        "auth_methods": EVIDENCE_FIELD_ARRAY_VALUE,
        "access_tier": EVIDENCE_FIELD,
        "api_surface": API_SURFACE_FIELD,
        "mcp": EVIDENCE_FIELD,
        "buildability": {"type": "STRING"},
        "primary_blocker": {"type": "STRING"},
        "notes": {"type": "STRING"},
    },
    "required": ["app", "id", "category", "one_liner", "instance_model",
                  "auth_methods", "access_tier", "api_surface", "mcp",
                  "buildability", "primary_blocker", "notes"],
}

SYSTEM_PROMPT = """You are a structured data extractor. You extract factual information ONLY from the provided page text. You must NEVER use your own knowledge about any product, company, or API.

CRITICAL RULES:
1. Every value you return must be supported by text that actually appears in the pages provided.
2. For every non-null field, you MUST include an "evidence_snippet" — a VERBATIM quote copied character-for-character from the page text. It will be verified by exact string matching.
3. If the pages do not contain information for a field, set value to "not_found" and set evidence_url and evidence_snippet to empty strings.
4. The "evidence_url" must be the URL of the specific page the snippet came from (shown in the === PAGE: <url> === headers).
5. Do NOT paraphrase or rephrase evidence. Copy the exact characters from the source text.
6. Keep each evidence_snippet on a SINGLE LINE — replace any newlines in the quoted text with a space.
7. If a page is tagged [entity_confidence: LOW], any field whose evidence comes solely from that page must have entity_risk set to true. Otherwise set entity_risk to false."""


def build_user_prompt(app_name: str, app_id: int, category: str,
                      context_text: str) -> str:
    return f"""Extract structured information about "{app_name}" (id: {app_id}, category: {category}) from the page text below.

Field guidance:
- one_liner: one sentence describing what this app does, from the page text.
- instance_model value must be one of: global, per_tenant, self_hosted, not_applicable, not_found. "per_tenant" = each customer gets their own subdomain/instance. "global" = one shared API base URL.
- auth_methods value is a list. Include ALL methods mentioned. Use these terms: oauth2_authcode, oauth2_client_credentials (= client_id + client_secret flow), api_key (= a single key), basic, token (= bearer token), none, not_applicable.
- access_tier value must be one of: open_no_auth, self_serve_free, self_serve_paid, customer_admin_gated (= must be existing customer + admin enables API), partner_gated, contact_sales, not_found.
- api_surface.surface_types list from: rest, graphql, cli, library, webhook, soap, none.
- api_surface.breadth: narrow, moderate, broad (= covers most product features), not_found.
- api_surface.write_access: read_only, read_write, not_found.
- mcp value: Look for explicit mention of MCP (Model Context Protocol). "read_only" = MCP exists but no write tools. "full" = MCP with read+write. "none" = MCP not mentioned. "not_found" = unclear.
- buildability must be one of: easy_win (= public docs, standard auth, broad API), buildable_with_caveats, needs_outreach, blocked (= no public API/docs), wrong_shape (= not a hosted service, it's a CLI/library).
- primary_blocker must be one of: none, admin_enablement, paid_plan, partner_approval, app_review, no_public_docs, per_tenant_schema, third_party_rate_limits, not_a_service.
- notes: free text for anything the enums cannot express.

For fields where the pages provide NO relevant information, set value to "not_found" and evidence_url and evidence_snippet to empty strings.

--- BEGIN PAGE TEXT ---
{context_text}
--- END PAGE TEXT ---"""


# ---------------------------------------------------------------------------
# LLM call
# ---------------------------------------------------------------------------

def call_gemini(system: str, user: str) -> tuple[dict, float]:
    """Returns (parsed_dict, latency_seconds)."""
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    t0 = time.time()
    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=user,
        config=genai.types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json",
            response_schema=RESPONSE_SCHEMA,
            temperature=0.0,
            max_output_tokens=4096,
        ),
    )
    latency = time.time() - t0
    parsed = json.loads(response.text)
    return parsed, latency


# ---------------------------------------------------------------------------
# Entity-risk tagging
# ---------------------------------------------------------------------------

def tag_entity_risk(result: dict, pages: list[dict]) -> dict:
    low_conf_urls = set()
    for p in pages:
        if p.get("source_entity_confidence") == "low":
            low_conf_urls.add(p["url"])

    if not low_conf_urls:
        return result

    for field_name in ("one_liner", "instance_model", "auth_methods",
                       "access_tier", "api_surface", "mcp"):
        field = result.get(field_name)
        if not isinstance(field, dict):
            continue
        ev_url = field.get("evidence_url")
        if ev_url and ev_url in low_conf_urls:
            field["entity_risk"] = True

    return result


# ---------------------------------------------------------------------------
# Per-app extraction
# ---------------------------------------------------------------------------

def extract_app(app_result: dict) -> dict:
    app_name = app_result["name"]
    app_id = app_result["id"]
    category = app_result["category"]

    if app_result.get("status") != "ok":
        return {
            "app": app_name, "id": app_id, "category": category,
            "status": app_result["status"],
            "_meta": {"chars_sent": 0, "pages_used": 0, "llm_calls": 0,
                      "model": None, "cost_usd": 0.0, "latency_s": 0.0,
                      "urls_fetched": [], "pages_needing_browser": []},
        }

    context_text, chars_sent, pages_used = assemble_context(app_result)

    if chars_sent == 0:
        urls_fetched = [p["url"] for p in app_result.get("pages", [])]
        redirect = app_result.get("redirect_offsite", False)
        notes = "no usable cached text"
        if redirect:
            finals = [p.get("final_url", "") for p in app_result.get("pages", [])]
            notes = f"domain redirects offsite ({finals[0] if finals else '?'}); no content rendered"
        return {
            "app": app_name, "id": app_id, "category": category,
            "status": "unresolved", "notes": notes,
            "_meta": {"chars_sent": 0, "pages_used": 0, "llm_calls": 0,
                      "model": None, "cost_usd": 0.0, "latency_s": 0.0,
                      "urls_fetched": urls_fetched,
                      "pages_needing_browser": []},
        }

    user_prompt = build_user_prompt(app_name, app_id, category, context_text)

    try:
        extracted, latency = call_gemini(SYSTEM_PROMPT, user_prompt)
    except Exception as e:
        return {
            "app": app_name, "id": app_id, "category": category,
            "status": "error", "notes": f"LLM call failed: {e}",
            "_meta": {"chars_sent": chars_sent, "pages_used": pages_used,
                      "llm_calls": 1, "model": None, "cost_usd": 0.0,
                      "latency_s": 0.0,
                      "urls_fetched": [p["url"] for p in app_result.get("pages", [])],
                      "pages_needing_browser": []},
        }

    extracted = tag_entity_risk(extracted, app_result.get("pages", []))

    urls_fetched = [p["url"] for p in app_result.get("pages", [])]
    pages_needing_browser = [
        p["url"] for p in app_result.get("pages", [])
        if p.get("tier1_js_shell")
    ]

    extracted["status"] = "ok"
    extracted["_meta"] = {
        "chars_sent": chars_sent,
        "pages_used": pages_used,
        "llm_calls": 1,
        "model": GEMINI_MODEL,
        "cost_usd": 0.0,
        "latency_s": round(latency, 2),
        "urls_fetched": urls_fetched,
        "pages_needing_browser": pages_needing_browser,
    }

    return extracted


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def load_fetch_results(target_names: list[str] | None) -> list[dict]:
    """Re-run fetch_pipeline.process_app to get page metadata, using cache."""
    from fetch_pipeline import process_app

    with open(APPS_JSON, encoding="utf-8") as f:
        all_apps = json.load(f)

    if target_names:
        apps = [a for a in all_apps if a["name"] in target_names]
    else:
        apps = all_apps

    results = []
    for app in apps:
        r = process_app(app)
        results.append(r)
    return results


def main():
    target_names = None
    if "--apps" in sys.argv:
        idx = sys.argv.index("--apps")
        target_names = [n.strip() for n in " ".join(sys.argv[idx + 1:]).split(",")]

    print("Loading fetch results from cache...", flush=True)
    fetch_results = load_fetch_results(target_names)

    all_extracted = []
    for fr in fetch_results:
        print(f"[{fr['name']}] extracting...", flush=True)
        try:
            result = extract_app(fr)
            all_extracted.append(result)
            chars = result.get("_meta", {}).get("chars_sent", 0)
            pages = result.get("_meta", {}).get("pages_used", 0)
            lat = result.get("_meta", {}).get("latency_s", 0)
            print(f"  -> {chars} chars sent, {pages} pages, {lat}s", flush=True)
        except Exception as e:
            print(f"  -> ERROR: {e}", flush=True)
            all_extracted.append({
                "app": fr["name"], "id": fr["id"],
                "category": fr["category"], "status": "error",
                "notes": str(e),
                "_meta": {"chars_sent": 0, "pages_used": 0, "llm_calls": 0,
                          "cost_usd": 0.0, "latency_s": 0.0,
                          "urls_fetched": [], "pages_needing_browser": []},
            })

    out_path = RESULTS_DIR / "results_v1.json"
    if out_path.exists():
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = RESULTS_DIR / f"results_{ts}.json"

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(all_extracted, f, ensure_ascii=False, indent=2)

    print(f"\nResults written to {out_path.name}")
    print(f"\n--- chars_sent per app ---")
    for r in all_extracted:
        meta = r.get("_meta", {})
        print(f"  {r.get('app', '?'):<26} {meta.get('chars_sent',0):>6} chars, "
              f"{meta.get('pages_used',0)} pages, "
              f"{meta.get('latency_s',0):.1f}s")

    if "--show" in sys.argv:
        show_names = set()
        idx = sys.argv.index("--show")
        if idx + 1 < len(sys.argv):
            show_names = {n.strip() for n in " ".join(sys.argv[idx + 1:]).split(",")}

        for r in all_extracted:
            if not show_names or r.get("app") in show_names:
                print(f"\n{'='*70}")
                print(json.dumps(r, indent=2, ensure_ascii=False))

    return all_extracted


if __name__ == "__main__":
    main()
