"""MCP-targeted third pass — retrieval fix for the mcp field only.

Probes deterministic URL patterns for MCP documentation that link
discovery misses because it's anchored on API reference pages.

Writes results_v3.json. Never touches v1 or v2.
"""

import copy
import json
import hashlib
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse
from collections import Counter

import requests
from readability import Document
from lxml import html as lxml_html

from dotenv import load_dotenv
load_dotenv()

from google import genai

CACHE_DIR = Path(__file__).parent / "cache"
V2_PATH = Path(__file__).parent / "results_v2.json"
V3_PATH = Path(__file__).parent / "results_v3.json"

GEMINI_MODEL = "gemini-3.1-flash-lite"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

MIN_CONTENT_CHARS = 200


# ---------------------------------------------------------------------------
# Helpers
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


def save_cache(url, final_url, text, http_status, method="GET", raw_html=None):
    from datetime import datetime, timezone
    CACHE_DIR.mkdir(exist_ok=True)
    entry = {
        "url": url,
        "final_url": final_url,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "text": text,
        "method": method,
        "http_status": http_status,
    }
    path = CACHE_DIR / f"{cache_key(url, method)}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(entry, f, ensure_ascii=False, indent=2)
    if raw_html is not None:
        raw_path = CACHE_DIR / f"{cache_key(url, method)}_raw.html"
        with open(raw_path, "w", encoding="utf-8") as f:
            f.write(raw_html)
    return entry


def extract_readable_text(raw_html: str) -> str:
    try:
        doc = Document(raw_html)
        summary_html = doc.summary()
        tree = lxml_html.fromstring(summary_html)
        text = tree.text_content()
    except Exception:
        try:
            tree = lxml_html.fromstring(raw_html)
            text = tree.text_content()
        except Exception:
            return ""
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def normalize(s: str) -> str:
    s = s.lower()
    s = re.sub(r"[^\w\s]", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def domain_of(url: str) -> str:
    return urlparse(url).netloc.lower().lstrip("www.")


def base_domain(d: str) -> str:
    """Strip docs./developers./developer./api. prefix to get base domain."""
    for prefix in ("docs.", "developers.", "developer.", "api."):
        if d.startswith(prefix):
            return d[len(prefix):]
    return d


def get_app_domain(result: dict) -> str | None:
    """Extract the app's primary domain from its fetched URLs."""
    urls = result.get("_meta", {}).get("urls_fetched", [])
    if not urls:
        return None

    GENERIC = {"github.com", "docs.github.com", "developer.mozilla.org",
               "claude.ai", "cursor.com"}

    domains = Counter()
    for url in urls:
        d = domain_of(url)
        if d in GENERIC or base_domain(d) in GENERIC:
            continue
        b = base_domain(d)
        domains[b] += 1

    if not domains:
        # All URLs are on generic domains — use first URL's base domain
        if urls:
            return base_domain(domain_of(urls[0]))
        return None

    return domains.most_common(1)[0][0]


def build_mcp_urls(domain: str) -> list[str]:
    """Generate deterministic MCP URL candidates."""
    return [
        f"https://{domain}/mcp",
        f"https://{domain}/docs/mcp",
        f"https://{domain}/developers/mcp",
        f"https://{domain}/api/mcp",
        f"https://docs.{domain}/mcp",
        f"https://developers.{domain}/mcp",
        f"https://{domain}/model-context-protocol",
        f"https://{domain}/docs/model-context-protocol",
    ]


def fetch_url(url: str) -> tuple[str, int, str]:
    """Try HTTP GET. Returns (text, status, final_url).
    Uses cache if available."""
    cached = load_cached_text(url)
    if cached and len(cached) >= MIN_CONTENT_CHARS:
        return cached, 200, url

    try:
        resp = requests.get(url, headers=HEADERS, timeout=15,
                            allow_redirects=True)
        status = resp.status_code
        final_url = resp.url

        if status != 200:
            return "", status, final_url

        raw_html = resp.text
        text = extract_readable_text(raw_html)

        # Cache it
        save_cache(url, final_url, text, status, raw_html=raw_html)

        return text, status, final_url

    except requests.RequestException:
        return "", 0, url


def probe_mcp_urls(domain: str, app_name: str, verbose: bool = False) -> tuple[str | None, str | None]:
    """Try each MCP URL pattern. Returns (url, text) for first hit, or (None, None)."""
    candidates = build_mcp_urls(domain)

    for url in candidates:
        text, status, final_url = fetch_url(url)

        if verbose:
            content_len = len(text) if text else 0
            hit = "HIT" if (status == 200 and content_len >= MIN_CONTENT_CHARS) else "miss"
            print(f"    {hit:4} {status:>3}  {content_len:>6} chars  {url}")

        if status == 200 and len(text) >= MIN_CONTENT_CHARS:
            # Check the page actually mentions MCP or the app
            lower_text = text.lower()
            if "mcp" in lower_text or "model context protocol" in lower_text:
                if verbose:
                    print(f"    >>> MCP content found at {url}")
                return url, text

    return None, None


# ---------------------------------------------------------------------------
# MCP extraction (single field, same rules as extract_pipeline)
# ---------------------------------------------------------------------------

MCP_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "value": {"type": "STRING", "enum": ["none", "read_only", "full", "not_found"]},
        "evidence_url": {"type": "STRING"},
        "evidence_snippet": {"type": "STRING"},
        "confidence": {"type": "STRING", "enum": ["high", "medium", "low"]},
    },
    "required": ["value", "evidence_url", "evidence_snippet", "confidence"],
}


def extract_mcp_field(app_name: str, url: str, text: str) -> tuple[dict, float]:
    """Extract the mcp field from fetched MCP page text."""
    prompt = f"""Extract the MCP (Model Context Protocol) capability for "{app_name}" from the page text below.

Classify as:
- "full" if the app provides an MCP server with read and write operations
- "read_only" if the MCP server is explicitly read-only
- "none" if MCP is explicitly mentioned as not supported
- "not_found" if you cannot determine MCP capability from the text

The evidence_snippet MUST be an exact character-for-character copy of a contiguous substring from the page text. It will be verified by automated string matching. Do NOT paraphrase.
The evidence_url must be: {url}

--- BEGIN PAGE TEXT ---
{text[:16000]}
--- END PAGE TEXT ---"""

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    t0 = time.time()
    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt,
        config=genai.types.GenerateContentConfig(
            system_instruction="You are a precise text extraction tool. Copy exact substrings from the provided text. Never paraphrase.",
            response_mime_type="application/json",
            response_schema=MCP_SCHEMA,
            temperature=0.0,
            max_output_tokens=512,
        ),
    )
    latency = time.time() - t0
    parsed = json.loads(response.text)
    return parsed, latency


def check_mcp_field(field_obj: dict, page_text: str, url: str) -> str:
    """CHECK the mcp field against the page text."""
    if not isinstance(field_obj, dict):
        return "ok_not_found"

    val = field_obj.get("value", "")
    if val == "not_found":
        return "ok_not_found"

    snippet = field_obj.get("evidence_snippet", "")
    if not snippet or not snippet.strip():
        if val in ("none",):
            return "unverifiable_negative"
        return "FLAG_no_evidence"

    norm_snippet = normalize(snippet)
    if not norm_snippet:
        if val in ("none",):
            return "unverifiable_negative"
        return "FLAG_no_evidence"

    if norm_snippet in normalize(page_text):
        return "verified"

    return "FLAG_fabricated_snippet"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--apps", type=str, default=None,
                        help="Comma-separated app names to process (default: all)")
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument("--write", action="store_true",
                        help="Write results_v3.json (default: dry run)")
    args = parser.parse_args()

    with open(V2_PATH, encoding="utf-8") as f:
        v2_data = json.load(f)

    v3_data = copy.deepcopy(v2_data)

    if args.apps:
        target_names = {n.strip() for n in args.apps.split(",")}
    else:
        target_names = None

    total_changed = 0
    total_probed = 0
    total_llm_calls = 0
    total_latency = 0.0
    changes = []
    probe_no_hit = []
    scope_conflicts = []
    negative_conflicts = []

    for i, result in enumerate(v3_data):
        app_name = result.get("app", "?")

        if target_names and app_name not in target_names:
            continue

        if result.get("status") != "ok":
            continue

        total_probed += 1
        old_mcp = result.get("mcp", {})
        old_val = old_mcp.get("value") if isinstance(old_mcp, dict) else old_mcp

        domain = get_app_domain(result)
        if not domain:
            if args.verbose:
                print(f"  [{app_name}] no domain found, skipping")
            probe_no_hit.append(app_name)
            continue

        if args.verbose:
            print(f"\n  [{app_name}] domain={domain}, current mcp={old_val}")

        mcp_url, mcp_text = probe_mcp_urls(domain, app_name, verbose=args.verbose)

        if not mcp_url:
            if args.verbose:
                print(f"    no MCP page found")
            probe_no_hit.append(app_name)
            continue

        # Extract mcp field from the found page
        try:
            extracted, latency = extract_mcp_field(app_name, mcp_url, mcp_text)
            total_llm_calls += 1
            total_latency += latency
        except Exception as e:
            if args.verbose:
                print(f"    extraction error: {e}")
            probe_no_hit.append(app_name)
            continue

        new_val = extracted.get("value", "not_found")

        # CHECK the result
        verdict = check_mcp_field(extracted, mcp_text, mcp_url)

        if args.verbose:
            snippet_preview = (extracted.get("evidence_snippet", "") or "")[:80]
            print(f"    extracted: {new_val}, verdict: {verdict}")
            print(f"    snippet: \"{snippet_preview}...\"")

        # Only update if we got a verified positive result
        if verdict == "verified" and new_val in ("full", "read_only"):
            old_is_positive = old_val in ("full", "read_only")
            if old_is_positive and old_val != new_val:
                old_snippet = old_mcp.get("evidence_snippet", "") if isinstance(old_mcp, dict) else ""
                scope_conflicts.append({
                    "app": app_name,
                    "v2": old_val,
                    "v2_snippet": old_snippet,
                    "probe": new_val,
                    "probe_snippet": extracted.get("evidence_snippet", ""),
                    "url": mcp_url,
                })
                if args.verbose:
                    print(f"    SCOPE CONFLICT: v2={old_val}, probe={new_val} — skipped, needs manual resolution")
            else:
                if old_val != new_val:
                    total_changed += 1
                    changes.append({
                        "app": app_name,
                        "old": old_val,
                        "new": new_val,
                        "url": mcp_url,
                        "verdict": verdict,
                    })
                result["mcp"] = extracted

                meta = result.get("_meta", {})
                urls = meta.get("urls_fetched", [])
                if mcp_url not in urls:
                    urls.append(mcp_url)
                    meta["urls_fetched"] = urls
                    result["_meta"] = meta

                if args.verbose:
                    print(f"    UPDATED: {old_val} -> {new_val}")
        elif verdict == "verified" and new_val == "none":
            if old_val in ("full", "read_only"):
                old_snippet = old_mcp.get("evidence_snippet", "") if isinstance(old_mcp, dict) else ""
                negative_conflicts.append({
                    "app": app_name,
                    "v2": old_val,
                    "v2_snippet": old_snippet,
                    "probe": "none",
                    "probe_snippet": extracted.get("evidence_snippet", ""),
                    "url": mcp_url,
                })
                if args.verbose:
                    print(f"    NEGATIVE CONFLICT: v2={old_val}, probe=none — skipped")
            else:
                result["mcp"] = extracted
                if args.verbose:
                    print(f"    verified none (kept)")
        else:
            probe_no_hit.append(app_name)
            if args.verbose:
                print(f"    not updated (verdict={verdict}, value={new_val})")

    # Update version tag
    for r in v3_data:
        if r.get("_meta"):
            r["_meta"]["version"] = "v3"

    # Summary
    print(f"\n{'='*70}")
    print(f"MCP pass: probed {total_probed} apps, {total_changed} changed")
    print(f"LLM calls: {total_llm_calls}, latency: {total_latency:.0f}s")

    mcp_counts = Counter()
    for r in v3_data:
        if r.get("status") != "ok":
            continue
        m = r.get("mcp", {})
        val = m.get("value") if isinstance(m, dict) else m
        mcp_counts[val] += 1

    no_mcp = mcp_counts.get("none", 0) + mcp_counts.get("not_found", 0)
    mcp_with_server = mcp_counts.get("full", 0) + mcp_counts.get("read_only", 0)
    print(f"\nMCP counts: {dict(mcp_counts.most_common())}")
    print(f"Apps with MCP server: {mcp_with_server}")
    print(f"Apps this pass could not help: {len(probe_no_hit)}")
    print(f"Apps with no MCP in final file: {no_mcp}")

    if changes:
        print(f"\nChanges ({len(changes)}):")
        print(f"  {'app':25} {'old':>12} {'new':>12} url")
        print(f"  {'-'*25} {'-'*12} {'-'*12} {'-'*40}")
        for c in changes:
            print(f"  {c['app']:25} {c['old']:>12} {c['new']:>12} {c['url']}")

    if scope_conflicts:
        print(f"\nScope conflicts ({len(scope_conflicts)}) — needs manual resolution:")
        for sc in scope_conflicts:
            print(f"  {sc['app']:25} v2={sc['v2']:>12}  probe={sc['probe']:>12}  {sc['url']}")
            print(f"    v2 snippet:    {sc['v2_snippet'][:100]}")
            print(f"    probe snippet: {sc['probe_snippet'][:100]}")

    if negative_conflicts:
        print(f"\nNegative conflicts ({len(negative_conflicts)}) — probe said 'none' but v2 was positive:")
        for nc in negative_conflicts:
            print(f"  {nc['app']:25} v2={nc['v2']:>12}  probe={nc['probe']:>12}  {nc['url']}")
            print(f"    v2 snippet:    {nc['v2_snippet'][:100]}")
            print(f"    probe snippet: {nc['probe_snippet'][:100]}")

    if probe_no_hit and args.verbose:
        print(f"\nProbe no-hit ({len(probe_no_hit)}): {', '.join(probe_no_hit)}")

    if args.write:
        with open(V3_PATH, "w", encoding="utf-8") as f:
            json.dump(v3_data, f, ensure_ascii=False, indent=2)
        print(f"\nWritten: {V3_PATH.name}")
    else:
        print(f"\nDry run — pass --write to save {V3_PATH.name}")


if __name__ == "__main__":
    main()
