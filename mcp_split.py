"""Split the mcp field into mcp_docs and mcp_product.

No network. Reads results_v4.json, writes results_v5.json.

Rationale: a documentation-platform MCP server (Mintlify's docs search
server, shipped with the docs site) is not the vendor's product API MCP
server. The old single `mcp` field conflated them, so a verified quote
about the docs server was being read as a claim about the product.

  mcp_docs     evidence of a documentation-search MCP server
  mcp_product  evidence of a product/API MCP server

Where the only evidence is docs boilerplate, mcp_product is "unverified" —
NOT "none". Evidence about the wrong server is not evidence of absence.
"""

import json
from collections import Counter
from pathlib import Path

HERE = Path(__file__).parent
SRC = HERE / "results_v4.json"
DST = HERE / "results_v5.json"

# Mintlify docs-server boilerplate fingerprints.
DOCS_MARKERS = ("submit_feedback", "find a problem with the documentation")

READONLY_PHRASES = ("read-only", "read only")

# Expected from the audit; asserted, not trusted.
EXPECTED_DOCS = {
    "Twenty", "Bright Data", "Discord", "Notion", "Fathom", "Reducto",
    "Devin", "Plain", "Waterfall.io", "Clay",
}


def field(value, url="", snippet="", confidence="medium", **extra):
    d = {
        "value": value,
        "evidence_url": url,
        "evidence_snippet": snippet,
        "confidence": confidence,
    }
    d.update(extra)
    return d


def main():
    with open(SRC, encoding="utf-8") as f:
        data = json.load(f)

    docs_apps = []
    consistency_hits = []

    for r in data:
        m = r.get("mcp")
        if not isinstance(m, dict):
            if r.get("status") == "ok":
                r["mcp_docs"] = field("not_found", confidence="low")
                r["mcp_product"] = field("not_found", confidence="low")
                r.pop("mcp", None)
            continue

        snippet = m.get("evidence_snippet") or ""
        url = m.get("evidence_url") or ""
        low = snippet.lower()

        is_docs = any(k in low for k in DOCS_MARKERS)

        if is_docs:
            # Evidence describes the documentation server. It supports
            # mcp_docs and says nothing about the product API.
            docs_apps.append({"app": r["app"], "was": m.get("value"), "url": url})
            r["mcp_docs"] = field(
                True, url, snippet, m.get("confidence", "medium"),
                **({"entity_risk": True} if m.get("entity_risk") else {}),
            )
            r["mcp_product"] = field(
                "unverified", confidence="low",
                note="only evidence found describes a documentation MCP server, "
                     "not a product API MCP server; absence not established",
            )
        else:
            r["mcp_docs"] = field("not_found", confidence="low")
            product = dict(m)
            product.pop("scope_note", None)

            # ---- consistency rule --------------------------------------
            # A snippet that says read-only cannot support "full".
            if product.get("value") == "full" and any(p in low for p in READONLY_PHRASES):
                consistency_hits.append({
                    "app": r["app"], "from": "full", "to": "read_only",
                    "url": url, "snippet": snippet[:120],
                })
                product["value"] = "read_only"
                product["scope_note"] = "downgraded: snippet states read-only"

            r["mcp_product"] = product

        r.pop("mcp", None)

    for r in data:
        if r.get("_meta"):
            r["_meta"]["version"] = "v5"

    found = {d["app"] for d in docs_apps}
    if found != EXPECTED_DOCS:
        print("WARNING: docs-server set differs from the audit's 10")
        print(f"  unexpected: {sorted(found - EXPECTED_DOCS)}")
        print(f"  missing:    {sorted(EXPECTED_DOCS - found)}\n")

    counts = Counter()
    for r in data:
        if r.get("status") != "ok":
            continue
        counts[(r.get("mcp_product") or {}).get("value")] += 1

    print("=" * 70)
    print("mcp_product FINAL COUNTS")
    for k in ("full", "read_only", "present", "unverified", "none", "not_found"):
        print(f"  {k:12} {counts.get(k, 0):>3}")
    print(f"  {'-'*12} ---")
    print(f"  {'total ok':12} {sum(counts.values()):>3}")
    ev = counts.get("full", 0) + counts.get("read_only", 0)
    print(f"\n  product MCP exists: {ev + counts.get('present', 0)}"
          f"  (scope evidenced: {ev})")
    print(f"  product MCP unknown: {counts.get('unverified', 0) + counts.get('not_found', 0)}")

    print(f"\nDOCS-SERVER APPS ({len(docs_apps)}) — mcp_docs=true, mcp_product=unverified")
    print(f"  {'app':16} {'old mcp':>10}  url")
    for d in sorted(docs_apps, key=lambda x: x["app"]):
        print(f"  {d['app']:16} {str(d['was']):>10}  {d['url']}")

    print(f"\nCONSISTENCY RULE (full + 'read-only' snippet): {len(consistency_hits)} caught")
    if not consistency_hits:
        print("  none beyond the docs-server group, which step 1 already resolved.")
    for c in consistency_hits:
        print(f"  {c['app']:16} {c['from']} -> {c['to']}  {c['url']}")
        print(f"    {c['snippet']}")

    with open(DST, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"\nWritten: {DST.name}  (v4 untouched)")


if __name__ == "__main__":
    main()
