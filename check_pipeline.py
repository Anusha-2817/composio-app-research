"""CHECK stage — pure string matching, no LLM.

For each evidence field, classify as:
  verified / ok_not_found / FLAG_no_evidence / FLAG_fabricated_snippet
"""

import json
import hashlib
import re
import unicodedata
from pathlib import Path
from collections import Counter

CACHE_DIR = Path(__file__).parent / "cache"
RESULTS_PATH = Path(__file__).parent / "results_v1.json"
REPORT_PATH = Path(__file__).parent / "verification_report.json"

EVIDENCE_FIELDS = [
    "one_liner", "instance_model", "auth_methods",
    "access_tier", "api_surface", "mcp",
]


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


def check_field(field_name: str, field_obj, all_page_texts: dict) -> str:
    if not isinstance(field_obj, dict):
        return "ok_not_found"

    val = get_field_value(field_obj)
    if is_not_found(val):
        return "ok_not_found"

    snippet = field_obj.get("evidence_snippet", "")
    ev_url = field_obj.get("evidence_url", "")

    if not snippet or not snippet.strip():
        return "FLAG_no_evidence"

    norm_snippet = normalize(snippet)
    if not norm_snippet:
        return "FLAG_no_evidence"

    if ev_url and ev_url in all_page_texts:
        page_text = all_page_texts[ev_url]
        if norm_snippet in normalize(page_text):
            return "verified"

    for url, page_text in all_page_texts.items():
        if norm_snippet in normalize(page_text):
            return "verified"

    return "FLAG_fabricated_snippet"


def build_page_text_map(app_result: dict) -> dict[str, str]:
    texts = {}
    urls = app_result.get("_meta", {}).get("urls_fetched", [])
    for url in urls:
        text = load_cached_text(url)
        if text:
            texts[url] = text
    return texts


def main():
    with open(RESULTS_PATH, encoding="utf-8") as f:
        results = json.load(f)

    field_counters = {f: Counter() for f in EVIDENCE_FIELDS}
    per_app = []

    for r in results:
        app_name = r.get("app", "?")
        status = r.get("status", "?")

        app_entry = {
            "app": app_name,
            "id": r.get("id"),
            "status": status,
            "fields": {},
        }

        if status != "ok":
            for f in EVIDENCE_FIELDS:
                app_entry["fields"][f] = "skipped"
            per_app.append(app_entry)
            continue

        page_texts = build_page_text_map(r)

        for field_name in EVIDENCE_FIELDS:
            field_obj = r.get(field_name)
            try:
                verdict = check_field(field_name, field_obj, page_texts)
            except Exception as e:
                verdict = f"error: {e}"

            app_entry["fields"][field_name] = verdict
            field_counters[field_name][verdict] += 1

        per_app.append(app_entry)

    field_summary = {}
    for f in EVIDENCE_FIELDS:
        field_summary[f] = dict(field_counters[f].most_common())

    total = Counter()
    for f in EVIDENCE_FIELDS:
        for verdict, count in field_counters[f].items():
            total[verdict] += count

    report = {
        "total_apps": len(results),
        "total_fields_checked": sum(total.values()),
        "totals": dict(total.most_common()),
        "per_field": field_summary,
        "per_app": per_app,
    }

    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(f"Checked {len(results)} apps, {sum(total.values())} fields")
    print()
    print(f"{'':20} {'verified':>10} {'ok_not_found':>14} {'no_evidence':>13} {'fabricated':>12}")
    print(f"{'':20} {'-'*10} {'-'*14} {'-'*13} {'-'*12}")
    for f in EVIDENCE_FIELDS:
        c = field_counters[f]
        print(f"{f:20} {c.get('verified',0):>10} {c.get('ok_not_found',0):>14} "
              f"{c.get('FLAG_no_evidence',0):>13} {c.get('FLAG_fabricated_snippet',0):>12}")
    print(f"{'-'*70}")
    print(f"{'TOTAL':20} {total.get('verified',0):>10} {total.get('ok_not_found',0):>14} "
          f"{total.get('FLAG_no_evidence',0):>13} {total.get('FLAG_fabricated_snippet',0):>12}")
    print()

    fab_apps = [a for a in per_app if any(v == "FLAG_fabricated_snippet" for v in a["fields"].values())]
    if fab_apps:
        print(f"--- Apps with fabricated snippets ({len(fab_apps)}) ---")
        for a in fab_apps:
            flagged = [f for f, v in a["fields"].items() if v == "FLAG_fabricated_snippet"]
            print(f"  {a['app']:25} {', '.join(flagged)}")

    print(f"\nWritten to {REPORT_PATH.name}")


if __name__ == "__main__":
    main()
