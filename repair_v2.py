"""V2 repair loop — CHECK feeds back into EXTRACT.

1. Reclassify: negative assertions (none, not_applicable) with no snippet → unverifiable_negative
2. Re-extract flagged fields with exact-substring instruction
3. Re-CHECK. If still failing, downgrade to not_found
4. If auth_methods or access_tier is not_found, force buildability/blocker to insufficient_evidence/unknown

Writes results_v2.json. Never touches results_v1.json.
"""

import copy
import json
import hashlib
import os
import re
import sys
import time
from pathlib import Path
from collections import Counter

from dotenv import load_dotenv
load_dotenv()

from google import genai

CACHE_DIR = Path(__file__).parent / "cache"
V1_PATH = Path(__file__).parent / "results_v1.json"
V2_PATH = Path(__file__).parent / "results_v2.json"
REPORT_PATH = Path(__file__).parent / "verification_report_v2.json"

GEMINI_MODEL = "gemini-3.1-flash-lite"

EVIDENCE_FIELDS = [
    "one_liner", "instance_model", "auth_methods",
    "access_tier", "api_surface", "mcp",
]

NEGATIVE_VALUES = {"none", "not_applicable"}


# ---------------------------------------------------------------------------
# Shared helpers (from check_pipeline / extract_pipeline)
# ---------------------------------------------------------------------------

def cache_key(url: str, method: str = "GET") -> str:
    tag = url if method == "GET" else f"{method}::{url}"
    return hashlib.sha1(tag.encode()).hexdigest()


def load_cached_text(url: str) -> str | None:
    for method in ("browser", "GET"):
        path = CACHE_DIR / f"{cache_key(url, method)}.json"
        if path.exists():
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            text = data.get("text", "")
            if text:
                return text
    return None


def normalize(s: str) -> str:
    s = s.lower()
    s = re.sub(r"[^\w\s]", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def get_field_value(field_obj):
    if not isinstance(field_obj, dict):
        return field_obj
    return field_obj.get("value")


def is_not_found(val) -> bool:
    if isinstance(val, str) and val == "not_found":
        return True
    if isinstance(val, list) and val == ["not_found"]:
        return True
    return False


def is_negative_assertion(val) -> bool:
    if isinstance(val, str) and val in NEGATIVE_VALUES:
        return True
    if isinstance(val, list) and len(val) == 1 and val[0] in NEGATIVE_VALUES:
        return True
    return False


def build_page_text_map(app_result: dict) -> dict[str, str]:
    texts = {}
    urls = app_result.get("_meta", {}).get("urls_fetched", [])
    for url in urls:
        text = load_cached_text(url)
        if text:
            texts[url] = text
    return texts


# ---------------------------------------------------------------------------
# CHECK with unverifiable_negative support
# ---------------------------------------------------------------------------

def check_field(field_name: str, field_obj, all_page_texts: dict) -> str:
    if not isinstance(field_obj, dict):
        return "ok_not_found"

    val = get_field_value(field_obj)
    if is_not_found(val):
        return "ok_not_found"

    snippet = field_obj.get("evidence_snippet", "")
    ev_url = field_obj.get("evidence_url", "")

    if not snippet or not snippet.strip():
        if is_negative_assertion(val):
            return "unverifiable_negative"
        return "FLAG_no_evidence"

    norm_snippet = normalize(snippet)
    if not norm_snippet:
        if is_negative_assertion(val):
            return "unverifiable_negative"
        return "FLAG_no_evidence"

    if ev_url and ev_url in all_page_texts:
        page_text = all_page_texts[ev_url]
        if norm_snippet in normalize(page_text):
            return "verified"

    for url, page_text in all_page_texts.items():
        if norm_snippet in normalize(page_text):
            return "verified"

    return "FLAG_fabricated_snippet"


# ---------------------------------------------------------------------------
# Per-field re-extraction
# ---------------------------------------------------------------------------

REPAIR_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "value": {"type": "STRING"},
        "evidence_url": {"type": "STRING"},
        "evidence_snippet": {"type": "STRING"},
        "confidence": {"type": "STRING", "enum": ["high", "medium", "low"]},
    },
    "required": ["value", "evidence_url", "evidence_snippet", "confidence"],
}

REPAIR_SCHEMA_ARRAY = {
    "type": "OBJECT",
    "properties": {
        "value": {"type": "ARRAY", "items": {"type": "STRING"}},
        "evidence_url": {"type": "STRING"},
        "evidence_snippet": {"type": "STRING"},
        "confidence": {"type": "STRING", "enum": ["high", "medium", "low"]},
    },
    "required": ["value", "evidence_url", "evidence_snippet", "confidence"],
}

REPAIR_SCHEMA_SURFACE = {
    "type": "OBJECT",
    "properties": {
        "surface_types": {"type": "ARRAY", "items": {"type": "STRING"}},
        "breadth": {"type": "STRING"},
        "write_access": {"type": "STRING"},
        "evidence_url": {"type": "STRING"},
        "evidence_snippet": {"type": "STRING"},
        "confidence": {"type": "STRING", "enum": ["high", "medium", "low"]},
    },
    "required": ["surface_types", "breadth", "write_access",
                  "evidence_url", "evidence_snippet", "confidence"],
}

FIELD_SCHEMAS = {
    "one_liner": REPAIR_SCHEMA,
    "instance_model": REPAIR_SCHEMA,
    "auth_methods": REPAIR_SCHEMA_ARRAY,
    "access_tier": REPAIR_SCHEMA,
    "api_surface": REPAIR_SCHEMA_SURFACE,
    "mcp": REPAIR_SCHEMA,
}

FIELD_GUIDANCE = {
    "one_liner": "A single sentence describing what this app does.",
    "instance_model": "One of: global, per_tenant, self_hosted, not_applicable, not_found.",
    "auth_methods": "List from: oauth2_authcode, oauth2_client_credentials, api_key, basic, token, none, not_applicable, not_found.",
    "access_tier": "One of: open_no_auth, self_serve_free, self_serve_paid, customer_admin_gated, partner_gated, contact_sales, not_found.",
    "api_surface": "surface_types list from: rest, graphql, cli, library, webhook, soap, none. breadth: narrow/moderate/broad/not_found. write_access: read_only/read_write/not_found.",
    "mcp": "One of: none, read_only, full, not_found. Only if MCP (Model Context Protocol) is explicitly mentioned.",
}


def build_repair_context(page_texts: dict, max_chars: int = 16000) -> str:
    parts = []
    total = 0
    for url, text in page_texts.items():
        block = f"=== PAGE: {url} ===\n{text}\n"
        if total + len(block) > max_chars:
            remaining = max_chars - total
            if remaining > 200:
                block = block[:remaining] + "\n[truncated]\n"
            else:
                break
        parts.append(block)
        total += len(block)
    return "\n".join(parts)


def repair_field(app_name: str, field_name: str, old_value,
                 page_texts: dict) -> dict | None:
    context = build_repair_context(page_texts)
    if not context.strip():
        return None

    guidance = FIELD_GUIDANCE.get(field_name, "")
    old_val_str = json.dumps(old_value) if old_value else "unknown"

    prompt = f"""Re-extract the "{field_name}" field for "{app_name}" from the page text below.

The previous extraction returned a value ({old_val_str}) but the evidence_snippet could not be verified against the source text.

Your task:
1. Find the EXACT substring in the page text that supports the value for "{field_name}".
2. The evidence_snippet MUST be an exact character-for-character copy of a substring from the page text. It will be verified by automated string matching.
3. The evidence_url MUST be the URL shown in the === PAGE: <url> === header of the page containing the snippet.
4. If you cannot find an exact substring that supports the value, set value to "not_found" and evidence_url and evidence_snippet to empty strings.
5. Do NOT paraphrase. Do NOT combine words from different sentences. Copy a contiguous run of characters exactly as they appear.

Field guidance: {guidance}

--- BEGIN PAGE TEXT ---
{context}
--- END PAGE TEXT ---"""

    schema = FIELD_SCHEMAS[field_name]

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    t0 = time.time()
    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt,
        config=genai.types.GenerateContentConfig(
            system_instruction="You are a precise text extraction tool. Copy exact substrings from the provided text. Never paraphrase.",
            response_mime_type="application/json",
            response_schema=schema,
            temperature=0.0,
            max_output_tokens=1024,
        ),
    )
    latency = time.time() - t0
    parsed = json.loads(response.text)
    return parsed, latency


# ---------------------------------------------------------------------------
# Downgrade helpers
# ---------------------------------------------------------------------------

def downgrade_to_not_found(field_obj: dict, field_name: str) -> dict:
    if field_name == "api_surface":
        return {
            "surface_types": ["not_found"],
            "breadth": "not_found",
            "write_access": "not_found",
            "evidence_url": "",
            "evidence_snippet": "",
            "confidence": "low",
        }
    if field_name == "auth_methods":
        return {
            "value": ["not_found"],
            "evidence_url": "",
            "evidence_snippet": "",
            "confidence": "low",
        }
    return {
        "value": "not_found",
        "evidence_url": "",
        "evidence_snippet": "",
        "confidence": "low",
    }


def apply_insufficient_evidence_rules(result: dict) -> dict:
    auth_val = get_field_value(result.get("auth_methods"))
    access_val = get_field_value(result.get("access_tier"))

    auth_nf = is_not_found(auth_val)
    access_nf = is_not_found(access_val)

    if auth_nf or access_nf:
        result["buildability"] = "insufficient_evidence"
        result["primary_blocker"] = "unknown"

    return result


# ---------------------------------------------------------------------------
# Main repair loop
# ---------------------------------------------------------------------------

def main():
    with open(V1_PATH, encoding="utf-8") as f:
        v1_data = json.load(f)

    v2_data = copy.deepcopy(v1_data)

    total_repaired = 0
    total_downgraded = 0
    total_reclassified = 0
    total_llm_calls = 0
    total_latency = 0.0

    v1_verdicts = {f: Counter() for f in EVIDENCE_FIELDS}
    v2_verdicts = {f: Counter() for f in EVIDENCE_FIELDS}
    per_app_report = []

    for i, result in enumerate(v2_data):
        app_name = result.get("app", "?")
        status = result.get("status")

        app_report = {"app": app_name, "id": result.get("id"), "fields": {}}

        if status != "ok":
            for f in EVIDENCE_FIELDS:
                app_report["fields"][f] = {"v1": "skipped", "v2": "skipped", "action": "none"}
            per_app_report.append(app_report)
            continue

        page_texts = build_page_text_map(result)

        for field_name in EVIDENCE_FIELDS:
            field_obj = result.get(field_name)

            v1_verdict = check_field(field_name, field_obj, page_texts)
            v1_verdicts[field_name][v1_verdict] += 1

            action = "none"
            v2_verdict = v1_verdict

            if v1_verdict in ("FLAG_fabricated_snippet", "FLAG_no_evidence"):
                try:
                    repair_result, latency = repair_field(
                        app_name, field_name,
                        get_field_value(field_obj), page_texts
                    )
                    total_llm_calls += 1
                    total_latency += latency

                    if repair_result:
                        v2_verdict_check = check_field(field_name, repair_result, page_texts)

                        if v2_verdict_check == "verified":
                            if field_name == "api_surface":
                                for k in ("surface_types", "breadth", "write_access",
                                           "evidence_url", "evidence_snippet", "confidence"):
                                    if k in repair_result:
                                        result[field_name][k] = repair_result[k]
                            else:
                                result[field_name] = repair_result
                            v2_verdict = "verified"
                            action = "repaired"
                            total_repaired += 1
                        elif v2_verdict_check == "unverifiable_negative":
                            v2_verdict = "unverifiable_negative"
                            action = "reclassified"
                            total_reclassified += 1
                        else:
                            result[field_name] = downgrade_to_not_found(field_obj, field_name)
                            v2_verdict = "ok_not_found"
                            action = "downgraded"
                            total_downgraded += 1
                    else:
                        result[field_name] = downgrade_to_not_found(field_obj, field_name)
                        v2_verdict = "ok_not_found"
                        action = "downgraded"
                        total_downgraded += 1

                except Exception as e:
                    result[field_name] = downgrade_to_not_found(field_obj, field_name)
                    v2_verdict = "ok_not_found"
                    action = f"downgraded (error: {str(e)[:60]})"
                    total_downgraded += 1

            elif v1_verdict == "unverifiable_negative":
                action = "reclassified"
                total_reclassified += 1

            v2_verdicts[field_name][v2_verdict] += 1
            app_report["fields"][field_name] = {
                "v1": v1_verdict, "v2": v2_verdict, "action": action,
            }

        result = apply_insufficient_evidence_rules(result)
        v2_data[i] = result

        per_app_report.append(app_report)

        flagged_count = sum(1 for f in EVIDENCE_FIELDS
                           if app_report["fields"][f]["action"] != "none")
        if flagged_count > 0:
            actions = {f: app_report["fields"][f]["action"]
                       for f in EVIDENCE_FIELDS
                       if app_report["fields"][f]["action"] != "none"}
            print(f"  [{app_name}] {actions}", flush=True)

    # Update _meta for v2
    for r in v2_data:
        if r.get("_meta"):
            r["_meta"]["version"] = "v2"

    with open(V2_PATH, "w", encoding="utf-8") as f:
        json.dump(v2_data, f, ensure_ascii=False, indent=2)

    # Build verification report
    report = {
        "total_apps": len(v2_data),
        "repair_stats": {
            "repaired": total_repaired,
            "downgraded": total_downgraded,
            "reclassified_negative": total_reclassified,
            "llm_calls": total_llm_calls,
            "latency_s": round(total_latency, 1),
        },
        "v1_per_field": {f: dict(v1_verdicts[f].most_common()) for f in EVIDENCE_FIELDS},
        "v2_per_field": {f: dict(v2_verdicts[f].most_common()) for f in EVIDENCE_FIELDS},
        "per_app": per_app_report,
    }

    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    # Print summary
    print(f"\n{'='*75}")
    print(f"V2 repair complete: {total_repaired} repaired, {total_downgraded} downgraded, "
          f"{total_reclassified} reclassified as unverifiable_negative")
    print(f"LLM calls: {total_llm_calls}, latency: {total_latency:.0f}s")
    print()

    print(f"{'field':20} {'v1_verified':>12} {'v2_verified':>12} {'delta':>7} "
          f"{'v1_flagged':>11} {'v2_flagged':>11} {'unverif_neg':>12}")
    print(f"{'-'*20} {'-'*12} {'-'*12} {'-'*7} {'-'*11} {'-'*11} {'-'*12}")

    for f in EVIDENCE_FIELDS:
        v1v = v1_verdicts[f].get("verified", 0)
        v2v = v2_verdicts[f].get("verified", 0)
        delta = v2v - v1v
        v1f = v1_verdicts[f].get("FLAG_fabricated_snippet", 0) + v1_verdicts[f].get("FLAG_no_evidence", 0)
        v2f = v2_verdicts[f].get("FLAG_fabricated_snippet", 0) + v2_verdicts[f].get("FLAG_no_evidence", 0)
        un = v2_verdicts[f].get("unverifiable_negative", 0)
        sign = "+" if delta > 0 else ""
        print(f"{f:20} {v1v:>12} {v2v:>12} {sign}{delta:>6} {v1f:>11} {v2f:>11} {un:>12}")

    v1_total_v = sum(v1_verdicts[f].get("verified", 0) for f in EVIDENCE_FIELDS)
    v2_total_v = sum(v2_verdicts[f].get("verified", 0) for f in EVIDENCE_FIELDS)
    v1_total_f = sum(v1_verdicts[f].get("FLAG_fabricated_snippet", 0) + v1_verdicts[f].get("FLAG_no_evidence", 0) for f in EVIDENCE_FIELDS)
    v2_total_f = sum(v2_verdicts[f].get("FLAG_fabricated_snippet", 0) + v2_verdicts[f].get("FLAG_no_evidence", 0) for f in EVIDENCE_FIELDS)
    v2_total_un = sum(v2_verdicts[f].get("unverifiable_negative", 0) for f in EVIDENCE_FIELDS)
    d = v2_total_v - v1_total_v
    sign = "+" if d > 0 else ""
    print(f"{'-'*75}")
    print(f"{'TOTAL':20} {v1_total_v:>12} {v2_total_v:>12} {sign}{d:>6} "
          f"{v1_total_f:>11} {v2_total_f:>11} {v2_total_un:>12}")

    print(f"\nWritten: {V2_PATH.name}, {REPORT_PATH.name}")


if __name__ == "__main__":
    main()
