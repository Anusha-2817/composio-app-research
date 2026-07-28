"""Pattern stats for the Composio research set.

Usage:
    python patterns.py                    # uses results_v2.json
    python patterns.py results_v1.json    # compare against v1
"""

import json
import sys
from collections import Counter, defaultdict

path = sys.argv[1] if len(sys.argv) > 1 else "results_v2.json"
rows = json.load(open(path, encoding="utf-8"))
print(f"\n=== {path} — {len(rows)} apps ===\n")


def val(row, field):
    """Fields are sometimes {'value': x} objects, sometimes bare strings."""
    v = row.get(field)
    if isinstance(v, dict):
        v = v.get("value")
    return v


def show(title, counter, total=None):
    total = total or sum(counter.values())
    print(title)
    for k, n in counter.most_common():
        pct = 100 * n / total if total else 0
        bar = "#" * int(pct / 2)
        print(f"  {str(k):26} {n:4}  {pct:5.1f}%  {bar}")
    print()


# ---------- buildability ----------
show("BUILDABILITY", Counter(val(r, "buildability") for r in rows))

# ---------- access tier ----------
show("ACCESS TIER", Counter(val(r, "access_tier") for r in rows))

# ---------- auth methods (a list per app) ----------
auth = Counter()
for r in rows:
    v = val(r, "auth_methods") or []
    if isinstance(v, str):
        v = [v]
    for m in v:
        auth[m] += 1
show("AUTH METHODS (apps may have several)", auth, total=len(rows))

# ---------- instance model ----------
show("INSTANCE MODEL", Counter(val(r, "instance_model") for r in rows))

# ---------- mcp ----------
show("MCP", Counter(val(r, "mcp") for r in rows))

# ---------- primary blocker ----------
show("PRIMARY BLOCKER", Counter(val(r, "primary_blocker") for r in rows))

# ---------- cross-tab: category x access tier ----------
print("CATEGORY x ACCESS TIER")
by_cat = defaultdict(Counter)
for r in rows:
    by_cat[r.get("category", "?")][val(r, "access_tier")] += 1
for cat in sorted(by_cat):
    c = by_cat[cat]
    line = ", ".join(f"{k}={n}" for k, n in c.most_common())
    print(f"  {cat[:34]:36} {line}")
print()

# ---------- cross-tab: category x buildability ----------
print("CATEGORY x BUILDABILITY")
by_cat2 = defaultdict(Counter)
for r in rows:
    by_cat2[r.get("category", "?")][val(r, "buildability")] += 1
for cat in sorted(by_cat2):
    c = by_cat2[cat]
    line = ", ".join(f"{k}={n}" for k, n in c.most_common())
    print(f"  {cat[:34]:36} {line}")
print()

# ---------- cross-tab: category x dominant auth ----------
print("CATEGORY x AUTH METHOD")
by_cat3 = defaultdict(Counter)
for r in rows:
    v = val(r, "auth_methods") or []
    if isinstance(v, str):
        v = [v]
    for m in v:
        by_cat3[r.get("category", "?")][m] += 1
for cat in sorted(by_cat3):
    c = by_cat3[cat]
    line = ", ".join(f"{k}={n}" for k, n in c.most_common(3))
    print(f"  {cat[:34]:36} {line}")
print()

# ---------- not_found rate per field ----------
print("NOT_FOUND RATE PER FIELD")
fields = ["one_liner", "instance_model", "auth_methods",
          "access_tier", "api_surface", "mcp"]
for f in fields:
    n = 0
    for r in rows:
        v = val(r, f)
        if v == "not_found" or v == ["not_found"] or v is None:
            n += 1
    print(f"  {f:20} {n:4} / {len(rows)}  ({100*n/len(rows):.0f}%)")
print()