from parser.DataPrep import DataPrep
from parser.TopicClassifier import TopicClassifier
from parser.Summarizer import Summarizer
from parser.Analyzer import DiagnosisAgent

grouped_reports = {
    "cpu_usage": [],
    "memory_usage": [],
    "index_optimization": [],
    "io_waits": [],
    "locks_contention": [],
    "query_plan_issues": [],
    "generic_info": []
}
test=DataPrep()
xml_file = "test3.html"
test._parse_html(xml_file)
units = test.semantic_units_from_xml("decoded_content.html")
query = test.get_sqltext_from_xml("decoded_content.html")
clusters = test.cluster_units(units)
classifier=TopicClassifier()
topic_embeddings = classifier.get_topic_embeddings()
summarizer=Summarizer()
for label, units in clusters.items():
        print(f"--- Cluster {label} ---\n")
        content = ""
        for unit in units:
            content=content+str(unit) + "\n\n"
        topic=(classifier.classify_chunk(content, topic_embeddings))[0]
        s=summarizer.summarize(content)
        print(f"Topic: {topic}\nSummary: {s}\n")
        grouped_reports[topic].append({"cluster_id": label,"summary": s})
        
for topic, reports in grouped_reports.items():
    if not reports:
        continue
    with open(f"{topic}_reports.txt", "w") as f:
        for report in reports:
            f.write(f"Cluster ID: {report['cluster_id']}\n")
            f.write(f"Summary:\n{report['summary']}\n")
            f.write("-" * 80 + "\n")
print("Reports grouped and saved by topic.")

agent = DiagnosisAgent()
report = agent.analyze(grouped_reports,query)

with open("diagnosis_report.txt", "w") as f:
    for topic, result in report.items():
        f.write(f"=== {topic.upper()} ===\n")
        f.write("Summaries:\n")
        for summary in result["summaries"]:
            f.write(f"- {summary}\n")
        f.write("Rule-based Findings:\n")
        for finding in result["rule_based_findings"]:
            f.write(f"- {finding}\n")
        f.write("LLM Findings:\n")
        f.write(f"{result['llm_findings']}\n")
        f.write("-" * 80 + "\n")