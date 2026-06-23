import json
import os
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass
from enum import Enum
from langchain_ollama import ChatOllama
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
from langchain_oci import ChatOCIGenAI

# Configuration
class DiagnosisStep(Enum):
    QUERY_ANALYSIS = "query_analysis"
    EXECUTION_PLAN = "execution_plan"
    TABLE_STRUCTURE = "table_structure"
    SPECIAL_KEYWORDS = "special_keywords"
    GENERAL_OPTIMIZATION = "general_optimization"

@dataclass
class Message:
    role: str
    content: str

@dataclass
class StepDiagnosis:
    step: DiagnosisStep
    findings: List[str]
    recommendations: List[str]
    conversation_log: List[Message]

class SQLMonitorDataExtractor:
    """Data extractor for SQL Monitor report"""
    
    def __init__(self, monitor_data: Dict):
        self.data = monitor_data
        
    def get_query(self) -> str:
        return self.data.get("query", {}).get("query_text", "")
    
    def get_execution_plan(self) -> List[Dict]:
        """Extract execution plan from operations"""
        operations = self.data.get("operations", {})
        plan = []
        
        for op_id, op_data in operations.items():
            info = op_data.get("info", {})
            stats = op_data.get("stats", {})
            optimizer = op_data.get("optimizer", {})
            
            plan.append({
                "id": op_id,
                "name": info.get("name"),
                "options": info.get("options"),
                "object": info.get("object"),
                "rows": stats.get("rows"),
                "cost": optimizer.get("operation_cost"),
                "cpu_cost": optimizer.get("cpu_cost"),
                "io_cost": optimizer.get("io_cost"),
                "time": optimizer.get("time"),
                "executions": stats.get("executions"),
                "predicates": info.get("predicates"),
                "parent_id": info.get("parent_id"),
                "depth": info.get("depth")
            })
        
        return sorted(plan, key=lambda x: int(x["id"]))
    
    def get_tables_and_views(self) -> Dict:
        """Extract table and view information"""
        return {
            "tables": self.data.get("table_names", []),
            "views": self.data.get("views", [])
        }
    
    def get_table_columns(self, table_name: str = None) -> Dict:
        """Get column information for tables"""
        table_columns = self.data.get("table_columns", {})
        
        if table_name:
            return {table_name: table_columns.get(table_name, [])}
        
        # Return summary for all tables
        summary = {}
        for table, columns in table_columns.items():
            summary[table] = {
                "column_count": len(columns),
                "indexed_columns": len([c for c in columns if c.get("no_indexes", 0) > 0]),
                "sample_columns": columns[:3] if len(columns) > 3 else columns
            }
        return summary
    
    def get_execution_stats(self) -> Dict:
        return {
            "elapsed_time": self.data.get("report_attributes", {}).get("elapsed_time"),
            "cpu_time": self.data.get("report_attributes", {}).get("cpu_time"),
            "db_time": self.data.get("stats", {}).get("db_time_stats", {}),
            "io_stats": self.data.get("stats", {}).get("io_stats", {})
        }
    
    def detect_special_keywords(self) -> List[str]:
        """Detect special Oracle keywords and hints in query"""
        query = self.get_query().upper()
        special_keywords = []
        
        # Oracle functions that might affect performance
        oracle_functions = ['NVL', 'NVL2', 'DECODE', 'COALESCE', 'CASE', 'TO_DATE', 
                           'TO_CHAR', 'TO_NUMBER', 'SUBSTR', 'INSTR', 'REGEXP_LIKE']
        
        # Oracle hints
        hints = ['/*+', 'FULL', 'INDEX', 'NO_INDEX', 'USE_HASH', 'USE_NL', 
                 'USE_MERGE', 'PARALLEL', 'NO_PARALLEL', 'APPEND', 'DRIVING_SITE']
        
        for keyword in oracle_functions:
            if keyword in query:
                special_keywords.append(f"Function: {keyword}")
        
        for hint in hints:
            if hint in query:
                special_keywords.append(f"Hint: {hint}")
                
        return special_keywords

class DiagnosisAgent:
    """Rule-based Diagnosis Agent"""
    
    def __init__(self, model: str = "llama2"):
        self.llm = ChatOCIGenAI(
            model_id="xai.grok-4",
            service_endpoint="https://inference.generativeai.us-chicago-1.oci.oraclecloud.com",
            compartment_id="ocid1.compartment.oc1..aaaaaaaa47jbpgerlzz4fdgpqrbelelkj7mamirdqdbwdrtkm3ez6b7mnizq",
            model_kwargs={"max_tokens": 10000, "temperature": 0.3, "top_p": 0.2,},
            auth_file_location='~/.oci/config'
        )
        self.step_results = {}
        
    def analyze_step(self, step: DiagnosisStep, data: str, context: str = "") -> str:
        """Analyze a specific step with appropriate system prompt"""
        
        prompts = {
            DiagnosisStep.QUERY_ANALYSIS: """You are a SQL expert analyzing query structure.
Look for:
- Improper or missing joins
- Cartesian products
- Suboptimal join order
- Complex nested subqueries
- Missing or incorrect join conditions
- Redundant operations
Provide specific issues found and recommendations.""",

            DiagnosisStep.EXECUTION_PLAN: """You are analyzing SQL execution plan performance.
Focus on:
- Operations consuming >50% of total time
- Operations scanning millions of rows
- High CPU or I/O cost operations
- Nested loops with high iteration counts
- Full table scans on large tables
- Inefficient access paths
- If no major problem found, do not provide any generic optimisations
- Be concise and straight to the point
Map findings to specific query parts.""",

            DiagnosisStep.TABLE_STRUCTURE: """You are analyzing table structure and indexes.
Identify:
- Indexed columns with low cardinality (few distinct values)
- Missing indexes on frequently filtered columns
- Over-indexing issues
- Partition opportunities
- Statistics issues
Ask for specific column details if needed.""",

            DiagnosisStep.SPECIAL_KEYWORDS: """You are analyzing Oracle-specific functions and hints.
Evaluate:
- Impact of NVL, DECODE, and other functions on performance
- Hint effectiveness and conflicts
- Function-based index opportunities
- Expression optimization possibilities""",

            DiagnosisStep.GENERAL_OPTIMIZATION: """You are providing general SQL optimization recommendations.
Consider:
- Query rewrite opportunities
- Index suggestions
- Partitioning strategies
- Statistics gathering recommendations
- Hint additions or removals
Focus on query-level changes only, no system configurations."""
        }
        
        system_prompt = prompts[step] + "\nNo live database connection exists. All recommendations must be query-level."
        
        if context:
            user_message = f"{context}\n\nData:\n{data}"
        else:
            user_message = data
            
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_message)
        ]
        
        response = self.llm.invoke(messages)
        return response.content
    
    def request_column_details(self, table_name: str, columns: List[str] = None) -> str:
        """Request specific column details"""
        if columns:
            return f"Please provide details for columns {', '.join(columns)} in table {table_name}"
        else:
            return f"Please provide column details for table {table_name}"

class StorageAgent:
    """Rule-based Storage Agent with structured data access"""
    
    def __init__(self, monitor_data: Dict, model: str = "llama2"):
        self.llm = ChatOCIGenAI(
            model_id="xai.grok-4",
            service_endpoint="https://inference.generativeai.us-chicago-1.oci.oraclecloud.com",
            compartment_id="ocid1.compartment.oc1..aaaaaaaa47jbpgerlzz4fdgpqrbelelkj7mamirdqdbwdrtkm3ez6b7mnizq",
            model_kwargs={"temperature": 0.3, "max_tokens": 10000, "top_p": 0.2,},
            auth_file_location='~/.oci/config'
        )
        self.extractor = SQLMonitorDataExtractor(monitor_data)
        
    def provide_query(self) -> str:
        """Step 1: Provide the SQL query"""
        query = self.extractor.get_query()
        return f"Here is the SQL query for analysis:\n\n{query}\n\nPlease analyze for any structural issues, improper joins, or other anomalies."
    
    def provide_execution_plan(self, chunk_size: int = 10) -> List[str]:
        """Step 2: Provide execution plan in chunks"""
        plan = self.extractor.get_execution_plan()
        stats = self.extractor.get_execution_stats()
        chunks = []
        
        # First chunk includes overview
        overview = f"""Execution Overview:
- Elapsed Time: {stats['elapsed_time']}
- CPU Time: {stats['cpu_time']}
- Total Operations: {len(plan)}

Execution Plan (analyzing in chunks):
"""
        
        for i in range(0, len(plan), chunk_size):
            chunk_data = plan[i:i+chunk_size]
            chunk_text = overview if i == 0 else "Continuing execution plan:\n\n"
            
            for op in chunk_data:
                chunk_text += f"""
Operation {op['id']}:
- Name: {op['name']} {op['options'] or ''}
- Object: {op['object'] or 'N/A'}
- Rows: {op['rows']}
- Cost: {op['cost']}
- CPU Cost: {op['cpu_cost']}
- Executions: {op['executions']}
"""
            chunks.append(chunk_text)
            
        return chunks
    
    def provide_table_structure(self) -> str:
        """Step 3: Provide table structure summary"""
        tables_views = self.extractor.get_tables_and_views()
        summary = self.extractor.get_table_columns()
        
        result = f"""Database Objects:
Tables: {', '.join(tables_views['tables'][:10])}
Views: {', '.join(tables_views['views'][:10])}

Table Structure Summary:
"""
        for table, info in list(summary.items())[:5]:
            result += f"\n{table}:"
            result += f"\n  - Total columns: {info['column_count']}"
            result += f"\n  - Indexed columns: {info['indexed_columns']}"
            result += f"\n  - Sample columns: {info['sample_columns'][:2]}"
            
        result += "\n\nI can provide detailed column information for specific tables upon request."
        return result
    
    def provide_column_details(self, table_name: str) -> str:
        """Provide detailed column information for a specific table"""
        columns = self.extractor.get_table_columns(table_name)
        
        if not columns.get(table_name):
            return f"No column information found for table {table_name}"
        
        result = f"Column details for {table_name}:\n"
        for col in columns[table_name][:20]:  # Limit to first 20 columns
            result += f"\n- {col['col_name']}:"
            result += f"\n  Indexes: {col['no_indexes']}"
            result += f"\n  Distinct values: {col['no_distinct_values']}"
            
        return result
    
    def provide_special_keywords(self) -> str:
        """Step 4: Provide special keywords and hints"""
        keywords = self.extractor.detect_special_keywords()
        query = self.extractor.get_query()
        
        result = "Special Keywords and Hints Analysis:\n\n"
        
        if keywords:
            result += "Found the following special elements:\n"
            for keyword in keywords:
                result += f"- {keyword}\n"
                
            result += f"\nQuery excerpt for context:\n{query[:500]}..."
        else:
            result += "No special Oracle functions or hints detected in the query."
            
        return result
    
    def provide_optimization_context(self) -> str:
        """Step 5: Provide context for general optimization"""
        stats = self.extractor.get_execution_stats()
        
        return f"""Query Performance Context for Optimization:

Execution Statistics:
- Elapsed Time: {stats['elapsed_time']}
- CPU Time: {stats['cpu_time']}
- Buffer Gets: {stats['io_stats'].get('buffer_gets')}
- Disk Reads: {stats['io_stats'].get('disk_reads')}

Please provide general optimization recommendations considering all previous findings.
Focus on query-level optimizations only."""

class RuleBasedSQLDiagnosisOrchestrator:
    """Orchestrator for rule-based diagnosis flow"""
    
    def __init__(self, monitor_data: Dict, model: str = "llama2"):
        self.monitor_data = monitor_data
        self.model = model
        self.diagnosis_agent = DiagnosisAgent(model=model)
        self.storage_agent = StorageAgent(monitor_data, model=model)
        self.step_diagnoses = []
        self.conversation_log = []
        
    def run_diagnosis(self) -> Dict:
        """Execute the 5-step diagnosis process"""
        
        print("="*60)
        print("STARTING RULE-BASED SQL DIAGNOSIS")
        print("="*60)
        
        # Step 1: Query Analysis
        print("\n" + "="*60)
        print("STEP 1: QUERY STRUCTURE ANALYSIS")
        print("="*60)
        step1_diagnosis = self._execute_query_analysis()
        self.step_diagnoses.append(step1_diagnosis)
        
        # Step 2: Execution Plan Analysis
        print("\n" + "="*60)
        print("STEP 2: EXECUTION PLAN ANALYSIS")
        print("="*60)
        step2_diagnosis = self._execute_plan_analysis()
        self.step_diagnoses.append(step2_diagnosis)
        
        # Step 3: Table Structure Analysis
        print("\n" + "="*60)
        print("STEP 3: TABLE STRUCTURE ANALYSIS")
        print("="*60)
        step3_diagnosis = self._execute_table_analysis()
        self.step_diagnoses.append(step3_diagnosis)
        
        # Step 4: Special Keywords Analysis
        print("\n" + "="*60)
        print("STEP 4: SPECIAL KEYWORDS AND HINTS ANALYSIS")
        print("="*60)
        step4_diagnosis = self._execute_keyword_analysis()
        self.step_diagnoses.append(step4_diagnosis)
        
        # Step 5: General Optimization
        print("\n" + "="*60)
        print("STEP 5: GENERAL OPTIMIZATION RECOMMENDATIONS")
        print("="*60)
        step5_diagnosis = self._execute_general_optimization()
        self.step_diagnoses.append(step5_diagnosis)
        
        return self._compile_final_diagnosis()
    
    def _execute_query_analysis(self) -> StepDiagnosis:
        """Execute Step 1: Query Analysis"""
        step_log = []
        
        # Storage provides query
        query_data = self.storage_agent.provide_query()
        step_log.append(Message("storage", query_data))
        print(f"Storage Agent:\n{query_data[:500]}...\n")
        
        # Diagnosis analyzes
        analysis = self.diagnosis_agent.analyze_step(DiagnosisStep.QUERY_ANALYSIS, query_data)
        step_log.append(Message("diagnosis", analysis))
        print(f"Diagnosis Agent:\n{analysis}\n")
        
        return StepDiagnosis(
            step=DiagnosisStep.QUERY_ANALYSIS,
            findings=[analysis],
            recommendations=[],
            conversation_log=step_log
        )
    
    def _execute_plan_analysis(self) -> StepDiagnosis:
        """Execute Step 2: Execution Plan Analysis"""
        step_log = []
        findings = []
        
        # Get execution plan in chunks
        plan_chunks = self.storage_agent.provide_execution_plan()
        
        # Include query for context
        query = self.storage_agent.extractor.get_query()
        context = f"Analyzing execution plan for this query:\n{query[:500]}...\n"
        
        for i, chunk in enumerate(plan_chunks):
            step_log.append(Message("storage", chunk))
            print(f"Storage Agent (Plan chunk {i+1}/{len(plan_chunks)}):\n{chunk[:500]}...\n")
            
            # Analyze each chunk
            analysis = self.diagnosis_agent.analyze_step(
                DiagnosisStep.EXECUTION_PLAN, 
                chunk,
                context if i == 0 else ""
            )
            findings.append(analysis)
            step_log.append(Message("diagnosis", analysis))
            print(f"Diagnosis Agent:\n{analysis[:500]}...\n")
            
        return StepDiagnosis(
            step=DiagnosisStep.EXECUTION_PLAN,
            findings=findings,
            recommendations=[],
            conversation_log=step_log
        )
    
    def _execute_table_analysis(self) -> StepDiagnosis:
        """Execute Step 3: Table Structure Analysis"""
        step_log = []
        findings = []
        
        # Provide initial table structure
        table_summary = self.storage_agent.provide_table_structure()
        step_log.append(Message("storage", table_summary))
        print(f"Storage Agent:\n{table_summary}\n")
        
        # Initial analysis
        analysis = self.diagnosis_agent.analyze_step(DiagnosisStep.TABLE_STRUCTURE, table_summary)
        findings.append(analysis)
        step_log.append(Message("diagnosis", analysis))
        print(f"Diagnosis Agent:\n{analysis}\n")
        
        # Check if diagnosis requests specific column details
        if "provide details" in analysis.lower() or "column details" in analysis.lower():
            # Extract table names from the request (simplified)
            tables = self.storage_agent.extractor.get_tables_and_views()['tables'][:3]
            
            for table in tables:
                # Provide column details
                details = self.storage_agent.provide_column_details(table)
                step_log.append(Message("storage", details))
                print(f"Storage Agent (Details for {table}):\n{details[:500]}...\n")
                
                # Analyze column details
                col_analysis = self.diagnosis_agent.analyze_step(
                    DiagnosisStep.TABLE_STRUCTURE,
                    details,
                    f"Analyzing columns for table {table}"
                )
                findings.append(col_analysis)
                step_log.append(Message("diagnosis", col_analysis))
                print(f"Diagnosis Agent:\n{col_analysis[:500]}...\n")
                
        return StepDiagnosis(
            step=DiagnosisStep.TABLE_STRUCTURE,
            findings=findings,
            recommendations=[],
            conversation_log=step_log
        )
    
    def _execute_keyword_analysis(self) -> StepDiagnosis:
        """Execute Step 4: Special Keywords Analysis"""
        step_log = []
        
        # Provide keywords and hints
        keywords_data = self.storage_agent.provide_special_keywords()
        step_log.append(Message("storage", keywords_data))
        print(f"Storage Agent:\n{keywords_data}\n")
        
        # Analyze impact
        analysis = self.diagnosis_agent.analyze_step(DiagnosisStep.SPECIAL_KEYWORDS, keywords_data)
        step_log.append(Message("diagnosis", analysis))
        print(f"Diagnosis Agent:\n{analysis}\n")
        
        return StepDiagnosis(
            step=DiagnosisStep.SPECIAL_KEYWORDS,
            findings=[analysis],
            recommendations=[],
            conversation_log=step_log
        )
    
    def _execute_general_optimization(self) -> StepDiagnosis:
        """Execute Step 5: General Optimization"""
        step_log = []
        
        # Provide optimization context
        context_data = self.storage_agent.provide_optimization_context()
        
        # Include summary of previous findings
        previous_findings = "\n\nPrevious findings summary:\n"
        for diagnosis in self.step_diagnoses:
            if diagnosis.findings:
                previous_findings += f"- {diagnosis.step.value}: Key issues identified\n"
        
        full_context = context_data + previous_findings
        step_log.append(Message("storage", full_context))
        print(f"Storage Agent:\n{full_context}\n")
        
        # Get final optimization recommendations
        optimization = self.diagnosis_agent.analyze_step(
            DiagnosisStep.GENERAL_OPTIMIZATION, 
            full_context
        )
        step_log.append(Message("diagnosis", optimization))
        print(f"Diagnosis Agent:\n{optimization}\n")
        
        return StepDiagnosis(
            step=DiagnosisStep.GENERAL_OPTIMIZATION,
            findings=[optimization],
            recommendations=[],
            conversation_log=step_log
        )
    
    def _compile_final_diagnosis(self) -> Dict:
        """Compile all step diagnoses into final result"""
        
        print("\n" + "="*60)
        print("COMPILING FINAL DIAGNOSIS")
        print("="*60)
        
        final_result = {
            "diagnosis_summary": {},
            "all_findings": [],
            "all_recommendations": [],
            "conversation_log": self.conversation_log,
            "execution_stats": self.storage_agent.extractor.get_execution_stats()
        }
        
        for step_diagnosis in self.step_diagnoses:
            step_name = step_diagnosis.step.value
            final_result["diagnosis_summary"][step_name] = {
                "findings_count": len(step_diagnosis.findings),
                "key_finding": step_diagnosis.findings[0][:200] if step_diagnosis.findings else "No issues found"
            }
            final_result["all_findings"].extend(step_diagnosis.findings)
            
            print(f"\n{step_name.upper()}:")
            print(f"  Findings: {len(step_diagnosis.findings)}")
            
        return final_result
    
    def generate_optimized_query(self) -> str:
        """Generate optimized query based on all diagnoses"""
        
        print("\n" + "="*60)
        print("GENERATING OPTIMIZED QUERY")
        print("="*60)
        
        # Compile all findings
        all_findings = "\n".join([
            f"{d.step.value}: {finding[:500]}"
            for d in self.step_diagnoses
            for finding in d.findings
        ])
        
        original_query = self.storage_agent.extractor.get_query()
        
        prompt = f"""Based on the following performance analysis findings, provide an optimized version of the SQL query.

Original Query:
{original_query}

Diagnosis Findings:
{all_findings}

Provide:
1. Optimized SQL query with inline comments
2. List of changes made
3. Expected performance improvements

Focus only on query-level optimizations."""
        
        messages = [
            SystemMessage(content="You are a SQL optimization expert. Generate optimized queries based on diagnosis findings."),
            HumanMessage(content=prompt)
        ]
        
        response = self.diagnosis_agent.llm.invoke(messages)
        
        print("Optimization complete!")
        
        return response.content
    
    def save_results(self, filename: str = "rule_based_diagnosis_results.json"):
        """Save diagnosis results to file"""
        
        results = {
            "model_used": self.model,
            "step_diagnoses": [
                {
                    "step": d.step.value,
                    "findings": d.findings,
                    "conversation_count": len(d.conversation_log)
                }
                for d in self.step_diagnoses
            ],
            "monitor_data_summary": {
                "elapsed_time": self.monitor_data.get("report_attributes", {}).get("elapsed_time"),
                "cpu_time": self.monitor_data.get("report_attributes", {}).get("cpu_time"),
                "tables": self.storage_agent.extractor.get_tables_and_views()["tables"][:5]
            }
        }
        
        with open(filename, 'w') as f:
            json.dump(results, f, indent=2)
        
        print(f"\nResults saved to {filename}")

# Main execution
def main():
    """Main execution function"""
    
    # Load SQL Monitor data
    print("Loading SQL Monitor data...")
    with open('new_parsed.txt', 'r') as f:
        monitor_data = json.load(f)
    
    print("Data loaded successfully!\n")
    
    # Initialize orchestrator
    orchestrator = RuleBasedSQLDiagnosisOrchestrator(
        monitor_data=monitor_data,
        model="gpt-oss-120b"
    )
    
    # Run 5-step diagnosis
    diagnosis_result = orchestrator.run_diagnosis()
    
    # Generate optimized query
    optimized_query = orchestrator.generate_optimized_query()
    
    # Save results
    orchestrator.save_results()
    
    # Save optimized query
    with open("optimized_query_rule_based.txt", "w") as f:
        f.write("-- Rule-Based Optimization Results --\n")
        f.write(optimized_query)
    
    print("\n" + "="*60)
    print("DIAGNOSIS COMPLETE")
    print("="*60)
    print(f"Total findings: {len(diagnosis_result['all_findings'])}")
    print("Optimized query saved to: optimized_query_rule_based.txt")
    print("Full results saved to: rule_based_diagnosis_results.json")
    
    return diagnosis_result, optimized_query


diagnosis, optimization = main()
