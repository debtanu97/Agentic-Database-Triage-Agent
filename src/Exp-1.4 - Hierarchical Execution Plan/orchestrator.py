import os
import re
import base64
import zlib
import json
import requests
from typing import Any, Dict, List, Tuple, Union
from bs4 import BeautifulSoup
from xml.etree import ElementTree as ET
from langchain.prompts import ChatPromptTemplate
from langchain.output_parsers import StructuredOutputParser, ResponseSchema
from Mod_SqlMonitorParser import SqlMonitorParser
from Mod_LLMService import LLMService

def format_prompt(query_plan: dict) -> str:
    sql_query = query_plan["sql_query"]
    flagged_json = query_plan["execution_plan"]
    plan_seq = query_plan["plan_seq"]

    template = """  
        You are a SQL Execution Plan Diagnostic Assistant.

        Input: Flagged execution plan nodes (JSON).
        Flagged Nodes: {flagged_json}

        Task: For each node:
        - Diagnose the exact inefficiency based ONLY on evidence in the node JSON.
        - Use these fields:
        • "operation" + "object" → identify the operation.
        • "predicates.local" vs "predicates.inherited" → detect OR, IS NULL, <> that block index usage.
        • "nearest_view_ancestor" and "ancestor_chain" → if a TABLE ACCESS sits under a VIEW and predicates only appear at the view level, diagnose as a **predicate pushdown issue** (filters not applied at base table).
        • "sql_context_snippets" → reference exact predicates, columns, or view aliases in your explanation.
        • "reasons", "estimated", "actual" → cite runtime/IO/cost evidence.

        Diagnosis rules:
        - TABLE ACCESS:
        • If "nearest_view_ancestor" is present → first check if predicates are only at the view level → call it **predicate pushdown issue**, not missing index.
        • If no view ancestor and OR/IS NULL/<> appear → call it **index blocking predicates**; suggest rewrite (e.g., UNION, IS NOT NULL).
        • If simple equality predicates and no index → call it **missing index**.
        - JOIN:
        • If estimated vs actual cardinality mismatch is large, or NESTED LOOPS with high input rows, suggest HASH JOIN or MERGE JOIN tied to the join column.
        - Always anchor diagnosis to the exact table, column, view, or predicate from JSON, not generic “indexes”.

        Output format (strict):
        [
            {{
                "node": "<operation + object>",
                "issue": "<short description citing JSON evidence>",
                "specific_fixes": "<exact table, column, view, or predicate that should be changed>"
            }}
        ]
    """

    prompt = ChatPromptTemplate.from_template(template)

    input_dict = {
        "sql_query": sql_query,
        "flagged_json": json.dumps(flagged_json, separators=(",", ":")),
        "plan_seq": plan_seq
    }

    final_prompt = prompt.format(**input_dict)

    return final_prompt

if __name__ == "__main__":
    # replace with your file path
    decoded_file = "Sql/Push_Predicate/monitor_1.html"

    # extract total monitor stats
    try:
        parser = SqlMonitorParser()
        llmService = LLMService(mode="local", local_server_url="http://127.0.0.1:8080/generate")
        llmServiceOCI = LLMService(
            mode="oci",
            oci_profile="DEFAULT",
            oci_config_path="/Users/debtanu/Documents/DB-Triage-Agentic/SQLMonitorDiagnsis/config",
            oci_compartment_id="ocid1.compartment.oc1..aaaaaaaa47jbpgerlzz4fdgpqrbelelkj7mamirdqdbwdrtkm3ez6b7mnizq",
            oci_endpoint_id="ocid1.generativeaimodel.oc1.us-chicago-1.amaaaaaask7dceyajqi26fkxly6qje5ysvezzrypapl7ujdnqfjq6hzo2loq"
        )
        
        parser.parse_file(decoded_file)
        plan = parser.get_execution_plan_sequence()
        flagged_nodes = parser.get_flagged_nodes()
        sql_query = parser.get_sql_query()

        # print(parser.get_full_execution_plan)
    except Exception as e:
        print("Failed extracting total stats:", e)

    # extract plan
    try:
        formatted_prompt = format_prompt({
            "sql_query" : sql_query,
            "execution_plan" : flagged_nodes,
            "plan_seq" : plan
        })
        
        # print(formatted_prompt)
        # print(llmService.generate(formatted_prompt))
        print(llmServiceOCI.generate(formatted_prompt), 100000)
        

    except Exception as e:
        print("Failed extracting plan:", e)