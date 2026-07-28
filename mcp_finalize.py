"""Deterministic evidence-quality pass over the mcp field.

No network. Reads results_v3.json + cached page text, writes results_v4.json.

Three corrections, applied in this order (order matters — a withdrawn
citation outranks a scope judgement built on that same citation):

  1. evidence withdrawn  cited page never mentions MCP -> not_found
  2. scope collapse      snippet states no tool capability -> "present"/scope=unverified
  3. entity risk         snippet never names the vendor  -> entity_risk=true

Predicates are imported from audit_mcp so the audit and the fix can never
drift apart.
"""

import json
from collections import Counter
from pathlib import Path

from audit_mcp import (MCP_MARKERS, entity_named, mcp_value, name_tokens,
                       scope_supported)
from check_pipeline import build_page_text_map

HERE = Path(__file__).parent
SRC = HERE / "results_v3.json"
BASE = HERE / "results_v2.json"
DST = HERE / "results_v4.json"

POSITIVE = ("full", "read_only")


def main():
    with open(SRC, encoding="utf-8") as f:
        data = json.load(f)
    with open(BASE, encoding="utf-8") as f:
        v2map = {r["app"]: r for r in json.load(f)}

    before = {r["app"]: mcp_value(r) for r in data}

    evidence_withdrawn = []
    scope_collapsed = []
    entity_risk = []
    kept_scope = []

    for r in data:
        if mcp_value(r) not in POSITIVE:
            continue

        app = r["app"]
        m = r["mcp"]
        snippet = (m.get("evidence_snippet") or "").strip()
        ev_url = (m.get("evidence_url") or "").strip()

        texts = build_page_text_map(r)
        page = texts.get(ev_url, "")

        # ---- 1. evidence withdrawn -------------------------------------
        # Cited page exists in cache but never mentions MCP: the citation
        # never supported the claim. Withdraw it entirely.
        if page and not any(k in page.lower() for k in MCP_MARKERS):
            evidence_withdrawn.append({
                "app": app,
                "was": m.get("value"),
                "url": ev_url,
                "inherited": (v2map.get(app, {}).get("mcp") or {}).get(
                    "evidence_snippet", "") == snippet,
            })
            r["mcp"] = {
                "value": "not_found",
                "evidence_url": "",
                "evidence_snippet": "",
                "confidence": "low",
                "withdrawn_evidence_url": ev_url,
                "withdrawn_reason": "cited page does not mention MCP",
            }
            continue

        # ---- 2. scope collapse -----------------------------------------
        ok, why = scope_supported(snippet)
        if not ok:
            scope_collapsed.append({"app": app, "was": m.get("value"), "url": ev_url})
            m["value"] = "present"
            m["scope"] = "unverified"
            m["scope_note"] = "snippet does not describe tool capability"
        else:
            kept_scope.append({"app": app, "value": m.get("value"), "why": why})

        # ---- 3. entity risk --------------------------------------------
        if not entity_named(app, snippet):
            m["entity_risk"] = True
            entity_risk.append({"app": app, "url": ev_url})

    for r in data:
        if r.get("_meta"):
            r["_meta"]["version"] = "v4"

    # ---- scope_conflicts re-check --------------------------------------
    # Only conflicts where BOTH snippets carry capability language survive.
    CONFLICTS = ["Twilio", "Ahrefs", "Reducto", "Devin", "YouTube Transcript"]
    by_app = {r["app"]: r for r in data}
    live_conflicts = []
    for app in CONFLICTS:
        v2m = (v2map.get(app, {}) or {}).get("mcp") or {}
        cur = (by_app.get(app, {}) or {}).get("mcp") or {}
        v2_ok, v2_why = scope_supported(v2m.get("evidence_snippet", "") or "")
        pr_ok, pr_why = scope_supported(cur.get("evidence_snippet", "") or "")
        v2v, prv = v2m.get("value"), cur.get("value")
        if v2_ok and pr_ok and v2v != prv:
            live_conflicts.append({
                "app": app, "v2": v2v, "probe": prv,
                "v2_snippet": v2m.get("evidence_snippet", ""),
                "probe_snippet": cur.get("evidence_snippet", ""),
            })

    # ---- report --------------------------------------------------------
    counts = Counter()
    for r in data:
        if r.get("status") != "ok":
            continue
        counts[mcp_value(r)] += 1

    print("=" * 72)
    print("FINAL mcp COUNTS")
    for k in ("full", "read_only", "present", "none", "not_found"):
        print(f"  {k:12} {counts.get(k, 0):>3}")
    print(f"  {'-'*12} ---")
    print(f"  {'total ok':12} {sum(counts.values()):>3}")
    asserted = counts.get("full", 0) + counts.get("read_only", 0) + counts.get("present", 0)
    print(f"\n  MCP exists (any scope): {asserted}")
    print(f"  of which scope is evidenced: {counts.get('full',0)+counts.get('read_only',0)}")

    print(f"\nEVIDENCE WITHDRAWN ({len(evidence_withdrawn)}) -> not_found")
    for e in evidence_withdrawn:
        tag = "inherited v2" if e["inherited"] else "v3 probe"
        print(f"  {e['app']:20} was {str(e['was']):>10}  [{tag}]  {e['url']}")

    print(f"\nSCOPE COLLAPSED ({len(scope_collapsed)}) -> present / scope=unverified")
    for e in scope_collapsed:
        print(f"  {e['app']:20} was {str(e['was']):>10}  {e['url']}")

    print(f"\nSCOPE KEPT ({len(kept_scope)}) — snippet describes capability")
    for e in kept_scope:
        print(f"  {e['app']:20} {str(e['value']):>10}  ({e['why']})")

    print(f"\nENTITY RISK ({len(entity_risk)}) -> entity_risk=true")
    for e in entity_risk:
        print(f"  {e['app']:20} {e['url']}")

    print(f"\nSCOPE CONFLICTS STILL LIVE ({len(live_conflicts)})")
    if not live_conflicts:
        print("  none — every conflict had at least one side with no capability")
        print("  language, so scope collapse resolved it.")
    for c in live_conflicts:
        print(f"  {c['app']}: v2={c['v2']} vs probe={c['probe']}")
        print(f"    v2:    {c['v2_snippet'][:110]}")
        print(f"    probe: {c['probe_snippet'][:110]}")

    print(f"\nBEFORE / AFTER (changed only)")
    print(f"  {'app':22} {'before':>10} -> {'after':<10}")
    n = 0
    for r in data:
        a, b4 = r["app"], before[r["app"]]
        af = mcp_value(r)
        if b4 != af:
            print(f"  {a:22} {str(b4):>10} -> {str(af):<10}")
            n += 1
    print(f"  ({n} changed)")

    with open(DST, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"\nWritten: {DST.name}  (v3 untouched)")


if __name__ == "__main__":
    main()
