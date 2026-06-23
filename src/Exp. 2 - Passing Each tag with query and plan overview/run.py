import json
from langchain_ollama import ChatOllama

# ---------------------------
# Build execution plan overview
# ---------------------------
def exec_plan_query(data):
    operations = data.get("operations", {})
    plan = []
    for op_id, op_data in sorted(operations.items(), key=lambda x: int(x[0])):
        info = op_data.get("info", {})
        entry = {
            "id": int(op_id),
            "name": info.get("name"),
            "options": info.get("options"),
            "object": info.get("object"),
            "object_alias": info.get("object_alias"),
            "qblock": info.get("qblock"),
            "depth": info.get("depth"),
            "position": info.get("position"),
            "parent_id": info.get("parent_id"),
        }
        plan.append(entry)
    print(plan)
    return plan


# ---------------------------
# Ask LLM for diagnosis
# ---------------------------
def llm_diagnose(block_name, content, query, plan, llm):
    prompt = f"""
You are an expert database and SQL performance analyst.

Task:
1. Analyze the given SQL performance report block: {block_name}
2. Identify at most 2 high-impact problems.
3. Provide solutions, with SQL statements (indexes, rewrites, hints) if applicable.

Block content:
{content}

SQL Query (reference):
{json.dumps(query, indent=2)}

Execution Plan Overview (reference):
{json.dumps(plan, indent=2)}

Output format:
problem: <title>
- Description: ...
solution:
- Steps: ...
- SQL: ...
"""
    response = llm.invoke(prompt)
    return response.content if hasattr(response, "content") else str(response)

# ---------------------------
# Iterate blocks
# ---------------------------
def iterate_blocks(data, llm):
    query = data.get("query", {})
    plan = exec_plan_query(data)
    solutions = []

    # Top-level simple blocks
    for key in ["report_attributes", "report_parameters", "optimizer_env"]:
        if key in data:
            content = json.dumps(data[key], indent=2)
            solutions.append(
                f"=== {key} ===\n" + llm_diagnose(key, content, query, plan, llm)
            )

    # Stats sub-blocks
    stats = data.get("stats", {})
    for subkey, subval in stats.items():
        block_name = f"stats/{subkey}"
        content = json.dumps(subval, indent=2)
        solutions.append(
            f"=== {block_name} ===\n" + llm_diagnose(block_name, content, query, plan, llm)
        )

    # Operations individually
    operations = data.get("operations", {})
    for op_id, op_data in sorted(operations.items(), key=lambda x: int(x[0])):
        block_name = f"operations/{op_id}"
        content = json.dumps(op_data, indent=2)
        solutions.append(
            f"=== {block_name} ===\n" + llm_diagnose(block_name, content, query, plan, llm)
        )

    return solutions

# ---------------------------
# Main runner
# ---------------------------
def run(input_path, output_path, model="llama3"):
    with open(input_path, "r") as f:
        data = json.load(f)

    llm = ChatOllama(model=model)
    solutions = iterate_blocks(data, llm)

    with open(output_path, "w") as f:
        for sol in solutions:
            f.write(sol + "\n\n")

    print(f"[DONE] {len(solutions)} blocks analyzed. Results in {output_path}")

# ---------------------------
# Example usage
# ---------------------------
run("sql_monitor_parsed.txt", "topic_llm_output.txt")
