"""Score results_v2.json against gold_set.json (7 gold apps only).

Per field: coverage (% of gold apps where the agent gave any answer)
           precision (% correct among those it answered)
For auth_methods: exact-match and partial-overlap reported separately.
Mismatch table: gold vs agent side by side.
Fields whose gold notes say to exclude them are skipped.
"""

import argparse
import json
import re
from pathlib import Path

HERE = Path(__file__).parent
GOLD_PATH = HERE / "gold_set.json"

SCORED_FIELDS = [
    "one_liner",
    "instance_model",
    "auth_methods",
    "access_tier",
    "api_surface",
    "mcp",
    "buildability",
    "primary_blocker",
]

SURFACE_SUBFIELDS = ["surface_types", "breadth", "write_access"]

# MCP is scored as two independent questions. Conflating them lets a
# confident "full" hide the fact that we never established the server
# exists, and lets an honest "we don't know the scope" read as a miss.
#   existence  does a product MCP server exist at all      yes / no
#   scope      if so, what can it do                       full / read_only
# "present" answers existence but abstains on scope. "unverified" and
# "not_found" abstain on both — they are non-answers, not wrong answers.
MCP_EXISTS_YES = ("full", "read_only", "present")
MCP_SCOPES = ("full", "read_only")


def get_value(field_obj):
    if isinstance(field_obj, dict):
        return field_obj.get("value")
    return field_obj


def get_mcp_value(record):
    """mcp_product on v5+, plain mcp on older files."""
    if "mcp_product" in record:
        return get_value(record.get("mcp_product"))
    return get_value(record.get("mcp"))


def mcp_existence(val):
    if val in MCP_EXISTS_YES:
        return "yes"
    if val == "none":
        return "no"
    return None


def mcp_scope(val):
    return val if val in MCP_SCOPES else None


def is_not_found(val):
    if val is None:
        return True
    if isinstance(val, str) and val == "not_found":
        return True
    if isinstance(val, list) and val == ["not_found"]:
        return True
    return False


def parse_exclusions(notes: str) -> set[str]:
    excluded = set()
    for m in re.finditer(r"excluded?\s+from\s+(\w+)\s+scoring", notes, re.I):
        excluded.add(m.group(1).lower())
    return excluded


def fmt_val(val):
    if isinstance(val, list):
        return ", ".join(str(v) for v in sorted(val))
    if val is None:
        return "None"
    return str(val)


def compare_simple(gold_val, agent_val) -> bool:
    if isinstance(gold_val, str) and isinstance(agent_val, str):
        return gold_val.lower().strip() == agent_val.lower().strip()
    return gold_val == agent_val


def compare_auth_methods(gold_val, agent_val) -> tuple[bool, float]:
    """Returns (exact_match, overlap_ratio)."""
    if not isinstance(gold_val, list) or not isinstance(agent_val, list):
        eq = compare_simple(gold_val, agent_val)
        return eq, 1.0 if eq else 0.0
    gold_set = set(v.lower() for v in gold_val)
    agent_set = set(v.lower() for v in agent_val)
    exact = gold_set == agent_set
    if not gold_set:
        return exact, 1.0 if not agent_set else 0.0
    overlap = len(gold_set & agent_set) / len(gold_set | agent_set)
    return exact, overlap


def compare_api_surface(gold_obj, agent_obj) -> dict[str, bool]:
    results = {}
    for sub in SURFACE_SUBFIELDS:
        gv = gold_obj.get(sub) if isinstance(gold_obj, dict) else None
        av = agent_obj.get(sub) if isinstance(agent_obj, dict) else None
        if sub == "surface_types":
            if isinstance(gv, list) and isinstance(av, list):
                results[sub] = set(v.lower() for v in gv) == set(v.lower() for v in av)
            else:
                results[sub] = compare_simple(gv, av)
        else:
            results[sub] = compare_simple(gv, av)
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default="results_v5.json",
                    help="results file to score (default: results_v5.json)")
    args = ap.parse_args()

    with open(GOLD_PATH, encoding="utf-8") as f:
        gold_data = json.load(f)
    with open(HERE / args.file, encoding="utf-8") as f:
        v2_data = json.load(f)

    print(f"Scoring {args.file} against {GOLD_PATH.name}\n")
    v2_by_name = {r["app"]: r for r in v2_data}

    mismatches = []

    field_stats = {}
    for field in SCORED_FIELDS:
        if field == "api_surface":
            for sub in SURFACE_SUBFIELDS:
                key = f"api_surface.{sub}"
                field_stats[key] = {"answered": 0, "correct": 0, "total": 0}
        elif field == "auth_methods":
            field_stats["auth_methods_exact"] = {"answered": 0, "correct": 0, "total": 0}
            field_stats["auth_methods_overlap"] = {"answered": 0, "total_overlap": 0.0, "total": 0}
        elif field == "mcp":
            field_stats["mcp_existence"] = {"answered": 0, "correct": 0, "total": 0}
            field_stats["mcp_scope"] = {"answered": 0, "correct": 0, "total": 0}
        else:
            field_stats[field] = {"answered": 0, "correct": 0, "total": 0}

    for gold in gold_data:
        app_name = gold["app"]
        agent = v2_by_name.get(app_name)
        if not agent:
            continue

        notes = gold.get("notes", "")
        exclusions = parse_exclusions(notes)

        for field in SCORED_FIELDS:
            if field in exclusions:
                continue

            gold_val = get_value(gold.get(field))
            agent_val = get_value(agent.get(field))

            gold_obj = gold.get(field)
            agent_obj = agent.get(field)

            if field == "api_surface":
                for sub in SURFACE_SUBFIELDS:
                    key = f"api_surface.{sub}"
                    gv = gold_obj.get(sub) if isinstance(gold_obj, dict) else None
                    av = agent_obj.get(sub) if isinstance(agent_obj, dict) else None

                    if is_not_found(gv):
                        continue
                    field_stats[key]["total"] += 1

                    if is_not_found(av):
                        continue
                    field_stats[key]["answered"] += 1

                    if sub == "surface_types":
                        match = (set(v.lower() for v in gv) == set(v.lower() for v in av)) if isinstance(gv, list) and isinstance(av, list) else compare_simple(gv, av)
                    else:
                        match = compare_simple(gv, av)

                    if match:
                        field_stats[key]["correct"] += 1
                    else:
                        mismatches.append({
                            "app": app_name, "field": key,
                            "gold": fmt_val(gv), "agent": fmt_val(av),
                        })

            elif field == "mcp":
                g_raw = get_mcp_value(gold)
                a_raw = get_mcp_value(agent)

                g_exist, a_exist = mcp_existence(g_raw), mcp_existence(a_raw)
                if g_exist is not None:
                    field_stats["mcp_existence"]["total"] += 1
                    if a_exist is not None:
                        field_stats["mcp_existence"]["answered"] += 1
                        if g_exist == a_exist:
                            field_stats["mcp_existence"]["correct"] += 1
                        else:
                            mismatches.append({
                                "app": app_name, "field": "mcp_existence",
                                "gold": g_exist, "agent": a_exist,
                            })

                g_scope, a_scope = mcp_scope(g_raw), mcp_scope(a_raw)
                if g_scope is not None:
                    field_stats["mcp_scope"]["total"] += 1
                    if a_scope is not None:
                        field_stats["mcp_scope"]["answered"] += 1
                        if g_scope == a_scope:
                            field_stats["mcp_scope"]["correct"] += 1
                        else:
                            mismatches.append({
                                "app": app_name, "field": "mcp_scope",
                                "gold": g_scope, "agent": a_scope,
                            })

            elif field == "auth_methods":
                if is_not_found(gold_val):
                    continue
                field_stats["auth_methods_exact"]["total"] += 1
                field_stats["auth_methods_overlap"]["total"] += 1

                if is_not_found(agent_val):
                    continue
                field_stats["auth_methods_exact"]["answered"] += 1
                field_stats["auth_methods_overlap"]["answered"] += 1

                exact, overlap = compare_auth_methods(gold_val, agent_val)
                if exact:
                    field_stats["auth_methods_exact"]["correct"] += 1
                else:
                    mismatches.append({
                        "app": app_name, "field": "auth_methods",
                        "gold": fmt_val(gold_val), "agent": fmt_val(agent_val),
                    })
                field_stats["auth_methods_overlap"]["total_overlap"] += overlap

            else:
                if is_not_found(gold_val):
                    continue
                field_stats[field]["total"] += 1

                if is_not_found(agent_val):
                    continue
                field_stats[field]["answered"] += 1

                if compare_simple(gold_val, agent_val):
                    field_stats[field]["correct"] += 1
                else:
                    mismatches.append({
                        "app": app_name, "field": field,
                        "gold": fmt_val(gold_val), "agent": fmt_val(agent_val),
                    })

    # Print coverage / precision table
    print(f"{'field':30} {'coverage':>10} {'precision':>10}  detail")
    print(f"{'-'*30} {'-'*10} {'-'*10}  {'-'*30}")

    for key, stats in field_stats.items():
        total = stats["total"]
        if total == 0:
            print(f"{key:30} {'n/a':>10} {'n/a':>10}  no gold values")
            continue

        answered = stats["answered"]
        coverage = answered / total * 100

        if key == "auth_methods_overlap":
            avg_overlap = stats["total_overlap"] / answered * 100 if answered else 0
            print(f"{key:30} {coverage:>9.0f}% {avg_overlap:>9.1f}%  {answered}/{total} answered, avg Jaccard")
        else:
            correct = stats["correct"]
            precision = correct / answered * 100 if answered else 0
            print(f"{key:30} {coverage:>9.0f}% {precision:>9.0f}%  {correct}/{answered} correct of {total} gold")

    # Print mismatch table
    if mismatches:
        max_g = max(len(m["gold"]) for m in mismatches)
        max_a = max(len(m["agent"]) for m in mismatches)
        cw = max(max_g, max_a, 10)
        print(f"\n{'='*90}")
        print(f"Mismatches ({len(mismatches)}):")
        print(f"{'app':25} {'field':30} {'gold':<{cw}}  {'agent':<{cw}}")
        print(f"{'-'*25} {'-'*30} {'-'*cw}  {'-'*cw}")
        for m in mismatches:
            print(f"{m['app']:25} {m['field']:30} {m['gold']:<{cw}}  {m['agent']:<{cw}}")
    else:
        print("\nNo mismatches!")

    print()


if __name__ == "__main__":
    main()
