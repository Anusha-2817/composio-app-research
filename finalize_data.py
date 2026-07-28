"""BATCH A — deterministic data fixes applied in place to results_v5.json.

  1. malformed evidence URLs   withdraw ONLY if the host has no dot at all or
                               the value is not an http(s) URL. Anything else
                               is printed for review and left untouched.
  2. wrong-entity citations    Coda cites superhuman.com -> not_found + entity_risk
  3. gate-severity rule        a sales/partner gate cannot be easy_win + blocker=none
  4. auth hygiene              "none" cannot coexist with concrete auth methods
  5. exports                   results.csv + results.md

A repaired URL is only accepted when the cached page for the repaired URL
actually contains the snippet. Otherwise the citation is withdrawn — a URL
we never fetched is not evidence.
"""

import csv
import json
import re
from pathlib import Path
from urllib.parse import urlparse

from check_pipeline import load_cached_text, normalize

HERE = Path(__file__).parent
PATH = HERE / "results_v5.json"

FIELD_KEYS = ["one_liner", "instance_model", "auth_methods", "access_tier",
              "api_surface", "mcp_product", "mcp_docs", "buildability",
              "primary_blocker"]

GATE_TIERS = ("contact_sales", "partner_gated")

# netloc repairs to attempt, in order. A repair is only accepted when the
# cached page at the repaired URL actually contains the snippet; otherwise
# the URL is reported for review, never silently changed or nulled.
URL_REPAIRS = [(r"^developer-docs\.amazon$", "developer-docs.amazon.com")]

# Print-only: hosts whose last label is not a common TLD. Never mutated.
COMMON_TLDS = {"com", "org", "net", "io", "ai", "dev", "co", "app", "cloud",
               "sh", "me", "gov", "edu", "xyz", "tech", "video", "so", "uk",
               "jp", "de", "fr", "in", "ca", "au", "us", "info", "help", "site"}


def is_url(u):
    return isinstance(u, str) and u.startswith(("http://", "https://"))


def bad_netloc(u):
    if not is_url(u):
        return True
    n = urlparse(u).netloc.split(":")[0]
    return (not n) or ("." not in n)


def try_repair(u):
    p = urlparse(u)
    for pat, rep in URL_REPAIRS:
        if re.match(pat, p.netloc):
            return u.replace(p.netloc, rep, 1)
    return None


def main():
    data = json.loads(PATH.read_text(encoding="utf-8"))
    log = {"url_repaired": [], "url_withdrawn": [], "url_suspect": [],
           "entity": [], "gate": [], "auth": []}

    for r in data:
        app = r["app"]

        # ---- 1. malformed evidence URLs --------------------------------
        for k in list(r.keys()):
            v = r.get(k)
            if not isinstance(v, dict):
                continue
            u = v.get("evidence_url") or ""
            if not u:
                continue

            snippet = v.get("evidence_snippet") or ""

            # Strictly malformed: not an http(s) URL, or host with no dot.
            # This is the ONLY condition that withdraws a citation.
            if bad_netloc(u):
                why = "not an http(s) URL" if not is_url(u) else "host has no dot"
                log["url_withdrawn"].append((app, k, u, why))
                v["value"] = "not_found"
                v["evidence_url"] = ""
                v["evidence_snippet"] = ""
                v["confidence"] = "low"
                v["withdrawn_evidence_url"] = u
                v["withdrawn_reason"] = why
                continue

            # Well-formed. Only a cache-verified repair may change it.
            fixed = try_repair(u)
            if fixed:
                text = load_cached_text(fixed) or ""
                if snippet and normalize(snippet) in normalize(text):
                    v["evidence_url"] = fixed
                    log["url_repaired"].append((app, k, u, fixed))
                else:
                    log["url_suspect"].append(
                        (app, k, u, "repair candidate, but snippet not found in "
                                    "cached page for repaired URL — left unchanged"))
                continue

            host = urlparse(u).netloc.split(":")[0].lower()
            if host.rsplit(".", 1)[-1] not in COMMON_TLDS:
                log["url_suspect"].append((app, k, u, "unusual TLD — left unchanged"))

        # malformed seed URL on an unresolved record
        meta = r.get("_meta") or {}
        seeds = meta.get("urls_fetched") or []
        if seeds and any(bad_netloc(s) for s in seeds):
            bad = [s for s in seeds if bad_netloc(s)]
            meta["urls_fetched"] = [s for s in seeds if not bad_netloc(s)]
            meta["withdrawn_seed_urls"] = bad
            r["_meta"] = meta
            for b in bad:
                log["url_withdrawn"].append((app, "_meta.seed", b, "malformed seed"))

        # ---- 2. wrong-entity citations ---------------------------------
        for k in list(r.keys()):
            v = r.get(k)
            if not isinstance(v, dict):
                continue
            u = v.get("evidence_url") or ""
            if not u:
                continue
            host = urlparse(u).netloc.lower()
            if app == "Coda" and "superhuman.com" in host:
                log["entity"].append((app, k, u))
                v["value"] = "not_found"
                v["entity_risk"] = True
                v["withdrawn_evidence_url"] = u
                v["withdrawn_reason"] = "evidence is for a different product (Superhuman)"
                v["evidence_url"] = ""
                v["evidence_snippet"] = ""
                v["confidence"] = "low"

        # ---- 3. gate-severity rule -------------------------------------
        tier = (r.get("access_tier") or {}).get("value")
        if tier in GATE_TIERS:
            b, blk = r.get("buildability"), r.get("primary_blocker")
            if b == "easy_win" and blk in ("none", None):
                r["buildability"] = "buildable_with_caveats"
                r["primary_blocker"] = tier
                log["gate"].append((app, tier, b, blk,
                                    "buildable_with_caveats", tier))

        # ---- 4. auth hygiene -------------------------------------------
        am = r.get("auth_methods")
        if isinstance(am, dict) and isinstance(am.get("value"), list):
            vals = am["value"]
            if "none" in vals and len(vals) > 1:
                am["value"] = [x for x in vals if x != "none"]
                log["auth"].append((app, vals, am["value"]))

    PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                    encoding="utf-8")

    # ---- 5. exports ----------------------------------------------------
    def gv(o):
        if isinstance(o, dict):
            v = o.get("value")
        else:
            v = o
        return ", ".join(map(str, v)) if isinstance(v, list) else ("" if v is None else str(v))

    def ev(r):
        for k in FIELD_KEYS:
            o = r.get(k)
            if isinstance(o, dict) and o.get("evidence_url"):
                return o["evidence_url"]
        return ""

    cols = ["id", "app", "category", "status", "one_liner", "instance_model",
            "auth_methods", "access_tier", "surface_types", "breadth",
            "write_access", "mcp_product", "mcp_scope", "mcp_docs",
            "buildability", "primary_blocker", "evidence_url"]

    rows = []
    for r in data:
        surf = r.get("api_surface") or {}
        prod = r.get("mcp_product") or {}
        rows.append({
            "id": r.get("id", ""), "app": r.get("app", ""),
            "category": r.get("category", ""), "status": r.get("status", ""),
            "one_liner": gv(r.get("one_liner")),
            "instance_model": gv(r.get("instance_model")),
            "auth_methods": gv(r.get("auth_methods")),
            "access_tier": gv(r.get("access_tier")),
            "surface_types": gv(surf.get("surface_types")) if isinstance(surf, dict) else "",
            "breadth": gv(surf.get("breadth")) if isinstance(surf, dict) else "",
            "write_access": gv(surf.get("write_access")) if isinstance(surf, dict) else "",
            "mcp_product": gv(prod),
            "mcp_scope": prod.get("scope", "") if isinstance(prod, dict) else "",
            "mcp_docs": "true" if (r.get("mcp_docs") or {}).get("value") is True else "",
            "buildability": gv(r.get("buildability")),
            "primary_blocker": gv(r.get("primary_blocker")),
            "evidence_url": ev(r),
        })

    with open(HERE / "results.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)

    def esc(s):
        return str(s).replace("|", "\\|").replace("\n", " ")

    md = ["# 100-app API capability results", "",
          f"{len(rows)} apps. Source: `results_v5.json`. "
          "`mcp_product` is the product API MCP server; `mcp_docs` marks apps "
          "where the only MCP evidence describes a documentation-search server.",
          ""]
    md.append("| " + " | ".join(cols) + " |")
    md.append("|" + "|".join("---" for _ in cols) + "|")
    for row in rows:
        md.append("| " + " | ".join(esc(row[c]) for c in cols) + " |")
    (HERE / "results.md").write_text("\n".join(md) + "\n", encoding="utf-8")

    # ---- report --------------------------------------------------------
    def sec(t, items):
        print(f"\n{t} ({len(items)})")
        if not items:
            print("  none")
        return items

    print("=" * 72)
    for a, k, u, fx in sec("URL REPAIRED", log["url_repaired"]):
        print(f"  {a:24} {k:14} {u}\n  {'':24} {'':14} -> {fx}")
    for a, k, u, why in sec("URL WITHDRAWN -> not_found", log["url_withdrawn"]):
        print(f"  {a:24} {k:14} [{why}] {u}")
    for a, k, u, why in sec("URL SUSPECT — NOT CHANGED, please eyeball", log["url_suspect"]):
        print(f"  {a:24} {k:14} {u}\n  {'':24} {'':14} {why}")
    for a, k, u in sec("ENTITY MISMATCH -> not_found + entity_risk", log["entity"]):
        print(f"  {a:24} {k:14} {u}")
    for a, t, ob, obl, nb, nbl in sec("GATE-SEVERITY RULE", log["gate"]):
        print(f"  {a:24} tier={t}")
        print(f"  {'':24} buildability {ob} -> {nb}")
        print(f"  {'':24} blocker      {obl} -> {nbl}")
    for a, o, n in sec("AUTH HYGIENE", log["auth"]):
        print(f"  {a:24} {o} -> {n}")

    from collections import Counter
    bc = Counter(r.get("buildability") for r in data if r.get("status") == "ok")
    print("\nBUILDABILITY COUNTS (after)")
    for k, v in bc.most_common():
        print(f"  {str(k):26} {v:>3}")
    print(f"  {'-'*26} ---\n  {'total ok':26} {sum(bc.values()):>3}")
    print(f"\nWrote results_v5.json, results.csv, results.md")


if __name__ == "__main__":
    main()
