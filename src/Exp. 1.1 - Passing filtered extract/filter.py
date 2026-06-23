import json
import re

def load_parsed_report(path):
    with open(path, "r") as f:
        return json.load(f)

def safe_int(value, default=0):
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return int(value)
    try:
        digits = re.findall(r"\d+", str(value))
        if digits:
            return int("".join(digits)) 
        return default
    except Exception:
        return default

def build_plan_overview(data):
    raw_plan = []
    for op_id, op_data in sorted(data.get("operations", {}).items(), key=lambda x: int(x[0])):
        info = op_data.get("info", {})
        raw_plan.append({
            "id": int(op_id),
            "name": info.get("name"),
            "options": info.get("options"),
            "object": info.get("object"),
            "depth": info.get("depth"),
            "position": info.get("position"),
            "parent_id": info.get("parent_id"),
        })

    by_parent = {}
    for node in raw_plan:
        parent = node.get("parent_id")
        by_parent.setdefault(parent, []).append(node)

    def recurse(node, level=0):
        lines = []
        prefix = "  " * level + f"{node['id']}: {node['name']}"
        if node.get("options"):
            prefix += f" {node['options']}"
        if node.get("object"):
            prefix += f" on {node['object']}"
        lines.append(prefix)

        for child in sorted(by_parent.get(node["id"], []), key=lambda x: x["position"]):
            lines.extend(recurse(child, level + 1))
        return lines

    roots = [n for n in raw_plan if n["parent_id"] is None]
    lines = []
    for root in roots:
        lines.extend(recurse(root))

    return '\n'.join(lines)



def rank_operations(data, top_n=5):
    ops = data.get("operations", {})
    scored = []
    for op_id, op in ops.items():
        optimizer = op.get("optimizer", {})
        stats = op.get("stats", {})

        cost = safe_int(optimizer.get("operation_cost", 0) or 0)
        rows = safe_int(stats.get("rows"))
        io = safe_int(stats.get("io_reqs"))

        score = (cost * 2) + rows + io
        scored.append((score, op_id, op))

    top_ops = [op for _, _, op in sorted(scored, reverse=True)[:top_n]]
    return top_ops


def filter_report(data, top_n=5):
    focused = {}

    if "query" in data:
        focused["query"] = data["query"]

    for key in ["report_attributes", "report_parameters", "optimizer_env"]:
        if key in data:
            focused[key] = data[key]

    focused["plan_overview"] = build_plan_overview(data)

    top_ops = rank_operations(data, top_n=top_n)
    focused["problematic_operations"] = top_ops

    total_ops = len(data.get("operations", {}))
    skipped_ops = total_ops - len(top_ops)
    if skipped_ops > 0:
        focused["skipped_operations"] = f"{skipped_ops} operations skipped (low cost / low I/O)"

    stats = data.get("stats", {})
    if stats:
        focused["stats_summary"] = {
            "db_time_stats": stats.get("db_time_stats", {}),
            "io_stats": stats.get("io_stats", {}),
        }

    return focused


def save_focused_report(focused, output_path):
    with open(output_path, "w") as f:
        json.dump(focused, f, indent=2)
    print(f"[DONE] Focused extract written to {output_path}")


input_path = "sql_monitor_parsed.txt"
output_path = "sql_monitor_filtered.txt"

data = load_parsed_report(input_path)
focused = filter_report(data, top_n=5)  
save_focused_report(focused, output_path)
