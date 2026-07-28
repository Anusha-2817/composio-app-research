"""Audit the mcp field's evidence quality in a results file.

Read-only. Answers, for every app currently asserting an MCP server:
  - does it carry an evidence_snippet, and does that snippet pass the
    same CHECK string matcher every other field uses
  - does the cited page actually talk about MCP (soft-404 detection)
  - does the snippet name the app it is filed under (entity risk)
  - does the snippet actually state scope, or is full/read_only unsupported

Usage: python audit_mcp.py [--file results_v3.json] [--baseline results_v2.json]
"""

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlparse

from check_pipeline import check_field, build_page_text_map

HERE = Path(__file__).parent

MCP_MARKERS = ("model context protocol", "mcp server")

# Stems, not whole words: "creat" catches create/creating/created.
WRITE_WORDS = ("creat", "updat", "writ", "delet", "patch", "modif", "insert",
               "execute_", "apply_", "post ", "send", "perform action")
READONLY_PHRASES = ("read-only", "read only", "does not execute", "does not write",
                    "cannot write", "no write")

# App-name tokens that are too generic to prove entity match on their own.
STOPWORDS = {"the", "io", "ai", "app", "com", "inc", "api", "cloud", "business",
             "com)", "(meta)", "meta", "cli", "crm"}


def mcp_value(result):
    m = result.get("mcp", {})
    return m.get("value") if isinstance(m, dict) else m


def name_tokens(app: str) -> list[str]:
    raw = re.split(r"[^\w.]+", app.lower())
    return [t for t in raw if t and t not in STOPWORDS and len(t) > 2]


def entity_named(app: str, snippet: str) -> bool:
    low = snippet.lower()
    toks = name_tokens(app)
    if not toks:
        return True
    return any(t in low for t in toks)


def scope_supported(snippet: str) -> tuple[bool, str]:
    """Does the snippet actually state tool capability?"""
    low = snippet.lower()
    for p in READONLY_PHRASES:
        if p in low:
            return True, "states read-only"
    for w in WRITE_WORDS:
        if w in low:
            return True, f"names write op ({w.strip()})"
    return False, "no capability language"


def host(url: str) -> str:
    return urlparse(url).netloc.lower().removeprefix("www.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default="results_v3.json")
    ap.add_argument("--baseline", default="results_v2.json")
    args = ap.parse_args()

    with open(HERE / args.file, encoding="utf-8") as f:
        data = json.load(f)
    baseline = {}
    bpath = HERE / args.baseline
    if bpath.exists():
        with open(bpath, encoding="utf-8") as f:
            baseline = {r["app"]: r for r in json.load(f)}

    hits = [r for r in data if mcp_value(r) in ("full", "read_only")]

    print(f"Auditing {args.file} — {len(hits)} apps assert an MCP server\n")

    verdicts = defaultdict(list)
    missing_snippet = []
    soft_404 = []
    entity_risk = []
    scope_unsupported = []
    url_owners = defaultdict(list)

    for r in hits:
        app = r["app"]
        m = r["mcp"]
        snippet = (m.get("evidence_snippet") or "").strip()
        ev_url = (m.get("evidence_url") or "").strip()
        val = m.get("value")

        if not snippet:
            missing_snippet.append((app, ev_url))
            continue

        texts = build_page_text_map(r)
        verdicts[check_field("mcp", m, texts)].append(app)

        page = texts.get(ev_url, "")
        if page and not any(k in page.lower() for k in MCP_MARKERS):
            soft_404.append((app, ev_url, len(page)))

        if not entity_named(app, snippet):
            entity_risk.append((app, ev_url, snippet[:90]))

        ok, why = scope_supported(snippet)
        if not ok:
            scope_unsupported.append((app, val, why, snippet[:90]))

        if ev_url:
            url_owners[ev_url].append(app)

    print("1. CHECK verdicts (check_pipeline.check_field)")
    for k, v in sorted(verdicts.items(), key=lambda x: -len(x[1])):
        print(f"     {k:26} {len(v)}")
        if k != "verified":
            for a in v:
                print(f"         {a}")
    if missing_snippet:
        print(f"     {'NO SNIPPET AT ALL':26} {len(missing_snippet)}")
        for a, u in missing_snippet:
            print(f"         {a:24} {u}")
    print()

    print(f"2. Soft-404 suspects — cited page lacks {MCP_MARKERS}: {len(soft_404)}")
    for a, u, n in soft_404:
        print(f"     {a:24} {n:>6} chars  {u}")
    print()

    print(f"3. Entity risk — snippet never names the app: {len(entity_risk)}")
    for a, u, s in entity_risk:
        print(f"     {a:24} {u}")
        print(f"       {s}")
    shared = {u: apps for u, apps in url_owners.items() if len(apps) > 1}
    if shared:
        print(f"   shared evidence pages ({len(shared)}):")
        for u, apps in shared.items():
            print(f"     {u}  <- {', '.join(apps)}")
    cross = [(a, u) for u, apps in url_owners.items() for a in apps
             if name_tokens(a) and not any(t in host(u) for t in name_tokens(a))]
    if cross:
        print(f"   cross-domain evidence ({len(cross)}):")
        for a, u in cross:
            print(f"     {a:24} {u}")
    print()

    print(f"4. Scope unsupported by snippet text: {len(scope_unsupported)}")
    for a, val, why, s in scope_unsupported:
        print(f"     {a:24} asserts {val:>10}  ({why})")
        print(f"       {s}")
    print()

    if baseline:
        print("5. Changed vs baseline")
        rows = []
        for r in data:
            app = r["app"]
            b = baseline.get(app)
            if not b:
                continue
            bv, cv = mcp_value(b), mcp_value(r)
            if bv != cv:
                rows.append((app, bv, cv, (r.get("mcp") or {}).get("evidence_url", "")))
        if rows:
            print(f"     {'app':24} {'from':>12} {'to':>12}  url")
            for a, bv, cv, u in rows:
                print(f"     {a:24} {str(bv):>12} {str(cv):>12}  {u}")
        else:
            print("     (no differences)")
        print(f"     total changed: {len(rows)}")


if __name__ == "__main__":
    main()
