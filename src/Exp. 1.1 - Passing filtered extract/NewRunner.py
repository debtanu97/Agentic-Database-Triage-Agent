from langchain_core.prompts import ChatPromptTemplate
from langchain_ollama import ChatOllama

llm = ChatOllama(model="gpt-oss:20b")
with open("sql_monitor_filtered.txt" , 'r') as f:
    text=f.read()

prompt = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            """
            You are an expert SQL performance tuning consultant and database analyst.

            Your task:
            1. Analyze the given SQL execution plan and performance report.
            2. Identify **exact problems** that cause inefficiency (e.g., high CPU cost operations, large disk reads, inefficient joins, poor index usage).
            3. For each problem, provide **a clear solution** with **SQL-level fixes or tuning strategies**.
            4. Restrict to the **top 3 most impactful optimizations**.
            5. Always include **SQL query rewrites or index creation statements** if applicable.
            6. Do NOT just summarize the execution plan; always explain the *problem → solution → SQL fix*.

            Output format (strictly follow):
            <problem>
            - Exhaustive Description of the problem

            <solution>
            - Recommended solution steps in detail
            - SQL query/index statements for the recommended solution
                        """,
                    ),
                    (
                        "human",
                        "Analyze the given SQL performance report extract below and recommend optimisations:\n{report}\n"
                    ),
                ]
            ).partial()

formatted_prompt=prompt.format(report=text)
raw_response = llm.invoke(formatted_prompt)

try:
    llm_op=raw_response.content
    with open("exp2/IJ_gpt.txt","w") as f:
        f.write(llm_op)
except Exception as e:
    print("Error parsing response", e, "Raw Response - ", raw_response)

