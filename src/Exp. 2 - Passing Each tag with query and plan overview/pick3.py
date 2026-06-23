import json
from langchain_ollama import ChatOllama

def get_top3_solutions(input_path, output_path, model="llama3"):
    # 1. Read the full solutions file
    with open(input_path, "r") as f:
        content = f.read()

    # 2. Build a simple prompt
    prompt = f"""
        You are an expert SQL performance analyst.

        The following text contains multiple problem/solution suggestions for SQL performance, grouped by different report sections.

        Task:
        - Read through all of them carefully.
        - Select the **top 3 most important and impactful solutions** overall.
        - Provide them in a clear format.

        Text to analyze:
        {content}

        Output format:
        top_3_solutions:
        1. <problem>: <description of problem>
        <solution>: <recommended optimization steps, along with SQL statements if present>
        2. ...
        3. ...
        """

    # 3. Initialize LLM
    llm = ChatOllama(model=model)

    # 4. Send to LLM
    response = llm.invoke(prompt)
    result = response.content if hasattr(response, "content") else str(response)

    # 5. Save results
    with open(output_path, "w") as f:
        f.write(result)

    print(f"[DONE] Top 3 solutions written to {output_path}")
    return result


# Example usage
top3 = get_top3_solutions("topic_llm_output.txt", "top3_solutions.txt")
print(top3)
