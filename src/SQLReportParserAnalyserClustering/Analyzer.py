# analyzer.py
from typing import Dict, List
from langchain_ollama import ChatOllama


class DiagnosisAgent:
    def __init__(self,query:str="", model: str = "llama3"):
        self.llm = ChatOllama(model=model)
        self.query = query

    def rule_based_diagnosis(self, topic: str, summaries: List[str]) -> List[str]:
        """Simple heuristics for common database performance issues."""
        findings = []
        text = " ".join(summaries).lower()

        if topic == "cpu_usage":
            if "nested loop" in text or "full table scan" in text:
                findings.append("High CPU usage likely due to inefficient execution plan (nested loop/full scan). Consider rewriting queries or adding indexes.")
            if "90%" in text or "100%" in text:
                findings.append("CPU utilization is critically high. Check top queries and parallelism.")

        elif topic == "memory_usage":
            if "swap" in text or "out of memory" in text:
                findings.append("Memory pressure detected. Consider increasing PGA/SGA or optimizing queries.")
            if "buffer cache miss" in text:
                findings.append("Buffer cache misses indicate poor caching. Check index usage.")

        elif topic == "locks_contention":
            if "deadlock" in text or "blocked" in text:
                findings.append("Lock contention issue. Review transaction isolation and reduce long-running locks.")

        elif topic == "index_optimization":
            if "missing index" in text:
                findings.append("Missing indexes detected. Add appropriate indexes to optimize queries.")
            if "fragmentation" in text:
                findings.append("Index fragmentation found. Consider rebuilding/reorganizing indexes.")

        return findings

    def llm_diagnosis(self, topic: str, summaries: List[str]) -> str:
        """Fallback LLM-based reasoning for diagnosis."""
        input_text = "\n".join(summaries)
        prompt = f"""
        You are a database performance expert.
        Analyze the following {topic} related summaries and provide a concise diagnosis
        with recommended optimizations:
        
        {input_text}

        Along with the optimizations, also provide sql queries that can be used to implement the recommendations.
        {self.query}
        """
        response = self.llm.invoke(prompt)
        return response.content

    def analyze(self, grouped_reports: Dict[str, List[Dict]]) -> Dict[str, Dict]:
        """Run analysis across all topics."""
        diagnosis_report = {}

        for topic, entries in grouped_reports.items():
            summaries = [entry["summary"] for entry in entries]
            if not summaries:
                continue
            # Step 1: Rule-based analysis
            rule_findings = self.rule_based_diagnosis(topic, summaries)

            # Step 2: LLM fallback
            llm_findings = self.llm_diagnosis(topic, summaries)

            diagnosis_report[topic] = {
                "summaries": summaries,
                "rule_based_findings": rule_findings,
                "llm_findings": llm_findings
            }

        return diagnosis_report


