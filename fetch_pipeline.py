"""RESOLVE + FETCH pipeline — no extraction, no LLM calls.

Tier 1: HTTP GET + readability
Tier 2: Playwright headless browser (when tier-1 is a JS shell)
Link discovery: up to 5 same-domain + 2 offsite-docs links, entity-scoped
"""

import json
import hashlib
import sys
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse, urljoin

import requests
from readability import Document
from lxml import html

CACHE_DIR = Path(__file__).parent / "cache"
APPS_JSON = Path(__file__).parent / "apps.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

JS_MARKERS = [
    "enable javascript",
    "javascript is required",
    "javascript is disabled",
    "please enable javascript",
    "you need to enable javascript",
    "this page requires javascript",
    "browser does not support javascript",
    "turn on javascript",
    "activate javascript",
]

LINK_KEYWORDS_RANKED = [
    "auth", "oauth", "token", "api-key", "apikey", "credential",
    "pricing", "plans",
    "mcp", "tools", "endpoints", "reference",
    "getting-started", "quickstart", "developer", "api",
]

OFFSITE_DOC_PATTERNS = [
    r"^https?://docs\.",
    r"\.readthedocs\.",
    r"\.github\.io",
    r"\.gitbook\.io",
    r"\.netlify\.app",
]

MAX_SAME_DOMAIN_LINKS = 5
MAX_OFFSITE_DOC_LINKS = 2

NAV_LINK_RATIO = 0.6
NAV_MIN_LINKS = 10
MARKETING_KEYWORDS = [
    "sign up", "get started", "request demo", "book a demo",
    "free trial", "start for free", "trusted by",
]
DEV_KEYWORDS = [
    "api", "endpoint", "authentication", "oauth", "token",
    "rest", "graphql", "sdk", "webhook", "request", "response",
    "curl", "header", "bearer", "scope", "rate limit",
]

GITHUB_REPO_RE = re.compile(
    r"^https?://github\.com/([^/]+)/([^/]+)"
)


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

def cache_key(url: str, method: str = "GET") -> str:
    tag = url if method == "GET" else f"{method}::{url}"
    return hashlib.sha1(tag.encode()).hexdigest()


def load_cache(url: str, method: str = "GET") -> dict | None:
    path = CACHE_DIR / f"{cache_key(url, method)}.json"
    if path.exists():
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        raw_path = CACHE_DIR / f"{cache_key(url, method)}_raw.html"
        if raw_path.exists():
            with open(raw_path, encoding="utf-8") as f:
                data["_raw_html"] = f.read()
        return data
    return None


def save_cache(url: str, final_url: str, text: str, http_status: int,
               method: str = "GET", raw_html: str | None = None):
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


# ---------------------------------------------------------------------------
# Text extraction helpers
# ---------------------------------------------------------------------------

def extract_readable_text(raw_html: str) -> str:
    try:
        doc = Document(raw_html)
        summary_html = doc.summary()
        tree = html.fromstring(summary_html)
        text = tree.text_content()
    except Exception:
        tree = html.fromstring(raw_html)
        text = tree.text_content()
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def extract_innertext(raw_html: str) -> str:
    try:
        tree = html.fromstring(raw_html)
        text = tree.text_content()
    except Exception:
        return ""
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def looks_like_js_shell(text: str) -> bool:
    if len(text) < 500:
        return True
    lower = text.lower()
    return any(m in lower for m in JS_MARKERS)


def domain_of(url: str) -> str:
    return urlparse(url).netloc.lower().lstrip("www.")


def _readability_html(raw_html: str) -> str | None:
    try:
        return Document(raw_html).summary()
    except Exception:
        return None


def classify_page(text: str, raw_html: str | None = None) -> str:
    if len(text) < 200:
        return "still_empty"

    lower = text.lower()
    dev_hits = sum(1 for kw in DEV_KEYWORDS if kw in lower)
    mkt_hits = sum(1 for kw in MARKETING_KEYWORDS if kw in lower)

    if dev_hits >= 3:
        return "content_ok"

    if raw_html:
        clean_html = _readability_html(raw_html)
        if clean_html:
            try:
                tree = html.fromstring(clean_html)
                links = tree.xpath("//a[@href]")
                body_text = tree.text_content().strip()
                if len(links) >= NAV_MIN_LINKS and len(body_text) > 0:
                    link_text_chars = sum(
                        len((a.text_content() or "").strip()) for a in links
                    )
                    if link_text_chars / max(len(body_text), 1) > NAV_LINK_RATIO:
                        return "nav_page"
            except Exception:
                pass

    if mkt_hits >= 2 and dev_hits < 2:
        return "marketing_page"
    if len(text) >= 500:
        return "content_ok"

    return "still_empty"


# ---------------------------------------------------------------------------
# Entity-scoped link discovery
# ---------------------------------------------------------------------------

def _github_path_prefix(seed_url: str) -> str | None:
    m = GITHUB_REPO_RE.match(seed_url)
    if m:
        return f"/{m.group(1)}/{m.group(2)}"
    return None


def _is_offsite_doc_link(href: str) -> bool:
    for pat in OFFSITE_DOC_PATTERNS:
        if re.search(pat, href):
            return True
    return False


def score_link(href: str, anchor_text: str) -> int:
    combined = (href + " " + anchor_text).lower()
    for i, kw in enumerate(LINK_KEYWORDS_RANKED):
        if kw in combined:
            return len(LINK_KEYWORDS_RANKED) - i
    return 0


def discover_links(raw_html: str, base_url: str,
                   seed_url: str) -> list[dict]:
    """Returns list of {"url": ..., "offsite_from_seed": bool}."""
    try:
        tree = html.fromstring(raw_html)
        tree.make_links_absolute(base_url)
    except Exception:
        return []

    base_domain = domain_of(base_url)
    gh_prefix = _github_path_prefix(seed_url)

    same_domain_scored = []
    offsite_scored = []

    for a in tree.xpath("//a[@href]"):
        href = a.get("href", "")
        if not href.startswith("http"):
            continue
        if href.rstrip("/") == base_url.rstrip("/"):
            continue
        if href.rstrip("/") == seed_url.rstrip("/"):
            continue

        anchor = (a.text_content() or "").strip()
        link_domain = domain_of(href)

        if link_domain == base_domain:
            if gh_prefix:
                path = urlparse(href).path
                if not path.startswith(gh_prefix):
                    continue
            s = score_link(href, anchor)
            if s > 0:
                same_domain_scored.append((s, href))
        else:
            if _is_offsite_doc_link(href) or score_link(href, anchor) > 0:
                s = score_link(href, anchor)
                s = max(s, 1)
                offsite_scored.append((s, href))

    def dedup_top(scored, limit):
        scored.sort(key=lambda x: -x[0])
        seen = set()
        out = []
        for _, href in scored:
            norm = href.rstrip("/").split("#")[0].split("?")[0]
            if norm not in seen:
                seen.add(norm)
                out.append(href)
            if len(out) >= limit:
                break
        return out

    same = dedup_top(same_domain_scored, MAX_SAME_DOMAIN_LINKS)
    offsite = dedup_top(offsite_scored, MAX_OFFSITE_DOC_LINKS)

    result = [{"url": u, "offsite_from_seed": False} for u in same]
    result += [{"url": u, "offsite_from_seed": True} for u in offsite]
    return result


def entity_confidence(url: str, seed_url: str) -> str:
    if domain_of(url) != domain_of(seed_url):
        return "low"
    gh_prefix = _github_path_prefix(seed_url)
    if gh_prefix:
        path = urlparse(url).path
        if not path.startswith(gh_prefix):
            return "low"
    return "high"


# ---------------------------------------------------------------------------
# Playwright headless fetch
# ---------------------------------------------------------------------------

_browser_context = None


def _get_browser():
    global _browser_context
    if _browser_context is None:
        from playwright.sync_api import sync_playwright
        pw = sync_playwright().start()
        browser = pw.chromium.launch(headless=True)
        _browser_context = (pw, browser)
    return _browser_context[1]


def fetch_with_browser(url: str, tier1_chars: int = 0) -> dict:
    cached = load_cache(url, method="browser")
    if cached:
        return {**cached, "_cache_hit": True,
                "_raw_innertext_len": None, "_readability_len": None}

    browser = _get_browser()
    page = browser.new_page()
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(3000)

        raw_innertext_len = page.evaluate("document.body ? document.body.innerText.length : 0")
        raw_innertext = page.evaluate("document.body ? document.body.innerText : ''")

        resp_status = 200
        final_url = page.url
        raw_html = page.content()

        readability_text = extract_readable_text(raw_html)
        readability_len = len(readability_text)

        readability_is_tiny = looks_like_js_shell(readability_text)
        raw_has_more = raw_innertext_len > readability_len * 2

        if readability_is_tiny and raw_has_more:
            text = re.sub(r"\n{3,}", "\n\n", raw_innertext).strip()
        else:
            text = readability_text

        entry = save_cache(url, final_url, text, resp_status,
                           method="browser", raw_html=raw_html)
        entry["_cache_hit"] = False
        entry["_raw_html"] = raw_html
        entry["_raw_innertext_len"] = raw_innertext_len
        entry["_readability_len"] = readability_len
        return entry

    except Exception as e:
        entry = save_cache(url, url, "", 0, method="browser")
        entry["_cache_hit"] = False
        entry["_error"] = str(e)
        entry["_raw_innertext_len"] = 0
        entry["_readability_len"] = 0
        return entry
    finally:
        page.close()


def close_browser():
    global _browser_context
    if _browser_context:
        pw, browser = _browser_context
        browser.close()
        pw.stop()
        _browser_context = None


# ---------------------------------------------------------------------------
# Tier-1 HTTP fetch
# ---------------------------------------------------------------------------

def fetch_tier1(url: str) -> dict:
    cached = load_cache(url, method="GET")
    if cached:
        return {**cached, "_cache_hit": True}

    try:
        resp = requests.get(url, headers=HEADERS, timeout=30, allow_redirects=True)
        text = extract_readable_text(resp.text)
        entry = save_cache(url, resp.url, text, resp.status_code,
                           method="GET", raw_html=resp.text)
        entry["_cache_hit"] = False
        entry["_raw_html"] = resp.text
        return entry
    except Exception as e:
        return {
            "url": url, "final_url": url, "text": "", "http_status": 0,
            "method": "GET", "_cache_hit": False, "_raw_html": None,
            "_error": str(e),
        }


# ---------------------------------------------------------------------------
# Main per-app pipeline
# ---------------------------------------------------------------------------

def normalize_hint(hint: str) -> str | None:
    if hint is None:
        return None
    url = hint.split("(")[0].strip()
    url = url.split(" ")[0].strip()
    if not url.startswith("http"):
        url = "https://" + url
    return url


def _do_tier2(url: str, tier1_chars: int, seed_url: str) -> dict:
    """Run browser fetch, build the tier-2 diagnostics dict."""
    t2 = fetch_with_browser(url, tier1_chars=tier1_chars)
    t2_text = t2.get("text", "")
    t2_raw = t2.get("_raw_html")

    diag = {
        "tier2_chars": len(t2_text),
        "tier2_cache_hit": t2.get("_cache_hit", False),
        "tier2_raw_innertext_len": t2.get("_raw_innertext_len"),
        "tier2_readability_len": t2.get("_readability_len"),
        "final_url": t2.get("final_url", url),
        "_t2_text": t2_text,
        "_t2_raw": t2_raw,
    }

    if not looks_like_js_shell(t2_text):
        cls = classify_page(t2_text, t2_raw)
        diag["classification"] = "js_shell_resolved" if cls == "content_ok" else cls
    else:
        diag["classification"] = classify_page(t2_text, t2_raw)

    return diag


def process_app(app: dict) -> dict:
    name = app["name"]
    hint = app.get("hint")
    result = {
        "id": app["id"],
        "name": name,
        "category": app["category"],
        "status": "ok",
        "seed_url": None,
        "redirect_offsite": False,
        "pages": [],
    }

    if hint is None:
        result["status"] = "unresolved"
        return result

    seed_url = normalize_hint(hint)
    result["seed_url"] = seed_url

    try:
        # --- Tier 1 ---
        t1 = fetch_tier1(seed_url)
        seed_domain = domain_of(seed_url)
        final_domain = domain_of(t1.get("final_url", seed_url))
        result["redirect_offsite"] = seed_domain != final_domain

        t1_text = t1.get("text", "")
        t1_js_shell = looks_like_js_shell(t1_text)

        page_entry = {
            "url": seed_url,
            "final_url": t1.get("final_url", seed_url),
            "role": "seed",
            "tier1_chars": len(t1_text),
            "tier1_cache_hit": t1.get("_cache_hit", False),
            "tier1_http_status": t1.get("http_status"),
            "tier1_js_shell": t1_js_shell,
            "tier2_chars": None,
            "tier2_cache_hit": None,
            "tier2_raw_innertext_len": None,
            "tier2_readability_len": None,
            "classification": None,
            "source_entity_confidence": entity_confidence(seed_url, seed_url),
        }

        raw_html_for_links = t1.get("_raw_html")

        # --- Tier 2 (browser retry) ---
        if t1_js_shell:
            diag = _do_tier2(seed_url, len(t1_text), seed_url)
            page_entry["tier2_chars"] = diag["tier2_chars"]
            page_entry["tier2_cache_hit"] = diag["tier2_cache_hit"]
            page_entry["tier2_raw_innertext_len"] = diag["tier2_raw_innertext_len"]
            page_entry["tier2_readability_len"] = diag["tier2_readability_len"]
            page_entry["final_url"] = diag["final_url"]
            page_entry["classification"] = diag["classification"]

            if diag["tier2_chars"] > len(t1_text) and diag.get("_t2_raw"):
                raw_html_for_links = diag["_t2_raw"]
        else:
            page_entry["classification"] = classify_page(
                t1_text, t1.get("_raw_html")
            )

        result["pages"].append(page_entry)

        # --- Link discovery ---
        if raw_html_for_links:
            base = page_entry["final_url"] or seed_url
            link_entries = discover_links(raw_html_for_links, base,
                                          seed_url=seed_url)

            for le in link_entries:
                lp = _fetch_link(le["url"], seed_url,
                                 offsite=le["offsite_from_seed"])
                result["pages"].append(lp)

    except Exception as e:
        result["status"] = "error"
        result["error"] = str(e)

    return result


def _fetch_link(url: str, seed_url: str, offsite: bool = False) -> dict:
    entry = {
        "url": url,
        "final_url": url,
        "role": "discovered",
        "offsite_from_seed": offsite,
        "tier1_chars": 0,
        "tier1_cache_hit": False,
        "tier1_http_status": None,
        "tier1_js_shell": False,
        "tier2_chars": None,
        "tier2_cache_hit": None,
        "tier2_raw_innertext_len": None,
        "tier2_readability_len": None,
        "classification": None,
        "source_entity_confidence": entity_confidence(url, seed_url),
    }
    try:
        t1 = fetch_tier1(url)
        t1_text = t1.get("text", "")
        entry["tier1_chars"] = len(t1_text)
        entry["tier1_cache_hit"] = t1.get("_cache_hit", False)
        entry["tier1_http_status"] = t1.get("http_status")
        entry["final_url"] = t1.get("final_url", url)
        entry["tier1_js_shell"] = looks_like_js_shell(t1_text)

        if entry["tier1_js_shell"]:
            diag = _do_tier2(url, len(t1_text), seed_url)
            entry["tier2_chars"] = diag["tier2_chars"]
            entry["tier2_cache_hit"] = diag["tier2_cache_hit"]
            entry["tier2_raw_innertext_len"] = diag["tier2_raw_innertext_len"]
            entry["tier2_readability_len"] = diag["tier2_readability_len"]
            entry["classification"] = diag["classification"]
        else:
            entry["classification"] = classify_page(
                t1_text, t1.get("_raw_html")
            )
    except Exception as e:
        entry["classification"] = "still_empty"
        entry["_error"] = str(e)

    return entry


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def print_report(results: list[dict]):
    total_tier1 = 0
    total_tier2 = 0
    total_cached = 0
    t2_diags = []

    for r in results:
        print(f"\n{'=' * 78}")
        print(f"  {r['name']}  (id={r['id']}, {r['category']})")
        print(f"  seed: {r['seed_url']}")
        if r["redirect_offsite"]:
            print("  ** REDIRECT OFFSITE **")
        if r["status"] != "ok":
            print(f"  status: {r['status']}  error: {r.get('error','')}")
            continue

        hdr = (f"  {'URL':<55} {'Role':<10} {'T1':<6} {'T2':<6} "
               f"{'Class':<20} {'Conf':<4} {'Cache'}")
        print(hdr)
        print(f"  {'-'*55} {'-'*10} {'-'*6} {'-'*6} "
              f"{'-'*20} {'-'*4} {'-'*8}")

        for p in r["pages"]:
            url_short = p["url"][:53]
            t1c = str(p["tier1_chars"])
            t2c = str(p["tier2_chars"]) if p["tier2_chars"] is not None else "-"
            cls = p["classification"] or "-"
            conf = p.get("source_entity_confidence", "-")[:4]
            role = p["role"]
            if p.get("offsite_from_seed"):
                role = "offsite"
            hits = []
            if p["tier1_cache_hit"]:
                hits.append("T1")
            if p.get("tier2_cache_hit"):
                hits.append("T2")
            cache_str = ",".join(hits) if hits else "miss"

            total_tier1 += 1
            if p["tier2_chars"] is not None:
                total_tier2 += 1
            if p["tier1_cache_hit"]:
                total_cached += 1
            if p.get("tier2_cache_hit"):
                total_cached += 1

            print(f"  {url_short:<55} {role:<10} {t1c:<6} {t2c:<6} "
                  f"{cls:<20} {conf:<4} {cache_str}")

            if p["tier2_raw_innertext_len"] is not None:
                t2_diags.append({
                    "app": r["name"],
                    "url": p["url"],
                    "t1_chars": p["tier1_chars"],
                    "raw_innertext": p["tier2_raw_innertext_len"],
                    "readability": p["tier2_readability_len"],
                    "final": p["tier2_chars"],
                })

    cache_files = list(CACHE_DIR.glob("*.json")) if CACHE_DIR.exists() else []
    print(f"\n{'=' * 78}")
    print(f"TOTALS: {total_tier1} tier-1 fetches, {total_tier2} tier-2 fetches, "
          f"{total_cached} cache hits, {len(cache_files)} files in cache/")

    if t2_diags:
        print(f"\n--- Tier-2 diagnostics: raw innerText vs readability ---")
        print(f"  {'App':<14} {'URL':<48} {'T1':<6} {'Raw':<6} "
              f"{'Rdbl':<6} {'Final':<6} {'Note'}")
        print(f"  {'-'*14} {'-'*48} {'-'*6} {'-'*6} {'-'*6} {'-'*6} {'-'*20}")
        for d in t2_diags:
            note = ""
            if d["readability"] is not None and d["raw_innertext"] is not None:
                if d["readability"] < d["t1_chars"] and d["raw_innertext"] > d["readability"]:
                    note = "fell back to raw"
                elif d["readability"] < d["t1_chars"]:
                    note = "both small"
            print(f"  {d['app']:<14} {d['url'][:46]:<48} "
                  f"{d['t1_chars']:<6} {str(d['raw_innertext']):<6} "
                  f"{str(d['readability']):<6} {d['final']:<6} {note}")


def main():
    with open(APPS_JSON, encoding="utf-8") as f:
        all_apps = json.load(f)

    target_names = None
    if "--apps" in sys.argv:
        idx = sys.argv.index("--apps")
        target_names = [n.strip() for n in " ".join(sys.argv[idx + 1:]).split(",")]

    apps = [a for a in all_apps if a["name"] in target_names] if target_names else all_apps

    results = []
    for app in apps:
        print(f"[{app['name']}] processing...", flush=True)
        r = process_app(app)
        results.append(r)

    print_report(results)
    close_browser()
    return results


if __name__ == "__main__":
    main()
