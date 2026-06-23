import json
import os
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass
from enum import Enum
from langchain_ollama import ChatOllama
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage

# Configuration
class AgentRole(Enum):
    DIAGNOSIS = "diagnosis"
    STORAGE = "storage"
    OPTIMIZER = "optimizer"

@dataclass
class Message:
    role: str
    content: str
    
@dataclass
class DiagnosisResult:
    issues: List[str]
    recommendations: List[str]
    root_cause: str
    
class SQLMonitorDataExtractor:
    """Extracts relevant information from SQL Monitor report"""
    
    def __init__(self, monitor_data: Dict):
        self.data = monitor_data
        
    def get_query(self) -> str:
        return self.data.get("query", {}).get("query_text", "")
    
    def get_execution_stats(self) -> Dict:
        return {
            "elapsed_time": self.data.get("report_attributes", {}).get("elapsed_time"),
            "cpu_time": self.data.get("report_attributes", {}).get("cpu_time"),
            "db_time": self.data.get("stats", {}).get("db_time_stats", {}),
            "io_stats": self.data.get("stats", {}).get("io_stats", {}),
            "offload_stats": self.data.get("stats", {}).get("offload_stats", {})
        }
    
    def get_execution_plan(self) -> Dict:
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
                "predicates": info.get("predicates"),
                "parent_id": info.get("parent_id"),
                "depth": info.get("depth")
            })
        
        return sorted(plan, key=lambda x: int(x["id"]))
    
    def get_table_info(self) -> List[str]:
        """Extract all tables involved in the query"""
        tables = set()
        operations = self.data.get("operations", {})
        
        for op_data in operations.values():
            obj = op_data.get("info", {}).get("object")
            if obj and not obj.startswith(":"):
                tables.add(obj)
                
        return list(tables)
    
    def get_optimizer_env(self) -> Dict:
        return self.data.get("optimizer_env", {})

class DiagnosisAgent:
    """LLM Agent for SQL performance diagnosis using Ollama"""
    
    def __init__(self, model: str = "llama2"):
        self.llm = ChatOllama(model=model, temperature=0.3)
        self.conversation_history = []
        self.system_prompt = """You are an expert Database Administrator specializing in SQL performance tuning.
        Your role is to diagnose SQL performance issues through systematic analysis.
        
        When presented with a SQL query and performance concern:
        1. First analyze the query structure
        2. Request specific information you need (execution plan, statistics, etc.)
        3. Identify performance bottlenecks
        4. Provide specific diagnosis
        
        Ask for information step by step. Be specific about what you need.
        No live/direct connection to a database instance .
        When you've identified the root cause and done provided all the required optimizations, clearly state: "DIAGNOSIS COMPLETE: [summary of issues]"
        """
        
    def analyze(self, message: str) -> str:
        """Analyze SQL performance issue"""
        
        self.conversation_history.append(Message("user", message))
        
        # Build messages for Ollama
        messages = [SystemMessage(content=self.system_prompt)]
        
        for m in self.conversation_history:
            if m.role == "user":
                messages.append(HumanMessage(content=m.content))
            else:
                messages.append(AIMessage(content=m.content))
        
        # Invoke Ollama
        response = self.llm.invoke(messages)
        reply = response.content
        
        self.conversation_history.append(Message("assistant", reply))
        
        return reply
    
    def is_diagnosis_complete(self, response: str) -> bool:
        """Check if diagnosis is complete"""
        return "DIAGNOSIS COMPLETE" in response
    
    def extract_diagnosis(self, response: str) -> str:
        """Extract the final diagnosis"""
        if "DIAGNOSIS COMPLETE:" in response:
            return response.split("DIAGNOSIS COMPLETE:")[1].strip()
        return ""

class StorageAgent:
    """LLM Agent with access to SQL Monitor data using Ollama"""
    
    def __init__(self, monitor_data: Dict, model: str = "llama2"):
        self.llm = ChatOllama(model=model, temperature=0.3)
        self.extractor = SQLMonitorDataExtractor(monitor_data)
        self.conversation_history = []
        
    def respond(self, request: str) -> str:
        """Respond to diagnosis agent's requests with relevant data"""
        
        system_prompt = f"""You are a database user/storage system with access to SQL Monitor report data.
        You provide specific information when requested by the DBA system.
        
        Available data:
        - SQL Query: {self.extractor.get_query()[:500]}...
        - Execution Statistics: {json.dumps(self.extractor.get_execution_stats(), indent=2)}
        - Tables involved: {self.extractor.get_table_info()}
        
        When asked for specific information, provide it clearly and concisely.
        If asked for execution plan, provide the relevant operations.
        """
        
        self.conversation_history.append(Message("user", request))
        
        # Check what information is being requested
        request_lower = request.lower()
        
        # Prepare context based on request
        context = ""
        if "execution plan" in request_lower or "plan" in request_lower:
            plan = self.extractor.get_execution_plan()
            context = f"\n\nExecution Plan:\n{json.dumps(plan[:10], indent=2)}"  # Show first 10 operations
            
        if "statistics" in request_lower or "stats" in request_lower:
            stats = self.extractor.get_execution_stats()
            context += f"\n\nExecution Statistics:\n{json.dumps(stats, indent=2)}"
            
        if "table" in request_lower:
            tables = self.extractor.get_table_info()
            context += f"\n\nTables: {tables}"
            
        if "optimizer" in request_lower:
            opt_env = self.extractor.get_optimizer_env()
            context += f"\n\nOptimizer Environment:\n{json.dumps(opt_env, indent=2)}"
        
        # Build messages for Ollama
        messages = [SystemMessage(content=system_prompt + context)]
        
        for m in self.conversation_history:
            if m.role == "user":
                messages.append(HumanMessage(content=m.content))
            else:
                messages.append(AIMessage(content=m.content))
        
        # Invoke Ollama
        response = self.llm.invoke(messages)
        reply = response.content
        
        self.conversation_history.append(Message("assistant", reply))
        
        return reply
    
    def get_initial_message(self) -> str:
        """Generate initial message with query and concern"""
        query = self.extractor.get_query()
        stats = self.extractor.get_execution_stats()
        
        return f"""I have a SQL query that's facing performance issue. 

Query:
{query[:1000]}...

Performance Issue: The query execution is very slow.
- Elapsed Time: {stats.get('elapsed_time')}
- CPU Time: {stats.get('cpu_time')}
- Buffer Gets: {stats.get('io_stats', {}).get('buffer_gets')}

Can you help diagnose the performance issue?"""

class OptimizationAgent:
    """LLM Agent for query optimization based on diagnosis using Ollama"""
    
    def __init__(self, model: str = "llama2"):
        self.llm = ChatOllama(model=model, temperature=0.3)
        
    def optimize_query(self, original_query: str, diagnosis: str) -> Dict:
        """Generate optimized query based on diagnosis"""
        
        system_prompt = """You are a SQL optimization expert.
        Based on the performance diagnosis provided, rewrite the SQL query to address the identified issues.
        
        Provide:
        1. Optimized SQL query
        2. Explanation of changes made
        3. Expected performance improvements
        4. Additional recommendations (indexes, statistics, etc.)
        """
        
        user_prompt = f"""Original Query:
{original_query}

Performance Diagnosis:
{diagnosis}

Please provide an optimized version of this query addressing the identified issues."""
        
        # Create messages for Ollama
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt)
        ]
        
        # Invoke Ollama
        response = self.llm.invoke(messages)
        
        return {
            "optimized_query": response.content,
            "original_query": original_query,
            "diagnosis": diagnosis
        }

class SQLDiagnosisOrchestrator:
    """Main orchestrator for the multi-agent diagnosis system using Ollama"""
    
    def __init__(self, monitor_data: Dict, model: str = "llama2", max_iterations: int = 10):
        """
        Initialize the orchestrator with Ollama models
        
        Args:
            monitor_data: Parsed SQL monitor data
            model: Ollama model name (e.g., 'llama2', 'mistral', 'codellama')
            max_iterations: Maximum conversation iterations
        """
        self.monitor_data = monitor_data
        self.max_iterations = max_iterations
        self.model = model
        
        # Initialize agents with Ollama
        self.diagnosis_agent = DiagnosisAgent(model=model)
        self.storage_agent = StorageAgent(monitor_data, model=model)
        self.optimization_agent = OptimizationAgent(model=model)
        
        self.conversation_log = []
        self.final_diagnosis = None
        
    def run_diagnosis(self) -> DiagnosisResult:
        """Run the diagnosis conversation between agents"""
        
        print("Starting SQL Performance Diagnosis with Ollama...\n")
        print(f"Using model: {self.model}\n")
        print("="*60)
        
        # Initial message from storage agent
        initial_msg = self.storage_agent.get_initial_message()
        self.conversation_log.append(("Storage Agent", initial_msg))
        print(f"Storage Agent:\n{initial_msg}\n")
        print("-"*60)
        
        current_message = initial_msg
        
        for iteration in range(self.max_iterations):
            print(f"\nIteration {iteration + 1}:")
            
            # Diagnosis agent analyzes
            diagnosis_response = self.diagnosis_agent.analyze(current_message)
            self.conversation_log.append(("Diagnosis Agent", diagnosis_response))
            print(f"Diagnosis Agent:\n{diagnosis_response}\n")
            
            # Check if diagnosis is complete
            if self.diagnosis_agent.is_diagnosis_complete(diagnosis_response):
                self.final_diagnosis = self.diagnosis_agent.extract_diagnosis(diagnosis_response)
                print("\n" + "="*60)
                print("DIAGNOSIS COMPLETE!")
                print("="*60)
                break
            
            print("-"*60)
            
            # Storage agent responds with requested information
            storage_response = self.storage_agent.respond(diagnosis_response)
            self.conversation_log.append(("Storage Agent", storage_response))
            print(f"Storage Agent:\n{storage_response}\n")
            print("-"*60)
            
            current_message = storage_response
        
        if not self.final_diagnosis:
            self.final_diagnosis = "Diagnosis incomplete after maximum iterations"
        
        return DiagnosisResult(
            issues=[self.final_diagnosis],
            recommendations=[],
            root_cause=self.final_diagnosis
        )
    
    def optimize_query(self) -> Dict:
        """Optimize query based on diagnosis"""
        
        if not self.final_diagnosis:
            raise ValueError("No diagnosis available. Run diagnosis first.")
        
        print("\n" + "="*60)
        print("Starting Query Optimization...")
        print("="*60)
        
        extractor = SQLMonitorDataExtractor(self.monitor_data)
        original_query = extractor.get_query()
        
        optimization_result = self.optimization_agent.optimize_query(
            original_query, 
            self.final_diagnosis
        )
        
        print(f"\nOptimization Complete!")
        print(f"Optimized Query:\n{optimization_result['optimized_query']}")
        
        return optimization_result
    
    def save_results(self, filename: str = "diagnosis_results.json"):
        """Save diagnosis and optimization results"""
        
        results = {
            "conversation_log": self.conversation_log,
            "final_diagnosis": self.final_diagnosis,
            "model_used": self.model,
            "monitor_data_summary": {
                "elapsed_time": self.monitor_data.get("report_attributes", {}).get("elapsed_time"),
                "cpu_time": self.monitor_data.get("report_attributes", {}).get("cpu_time"),
                "tables": SQLMonitorDataExtractor(self.monitor_data).get_table_info()
            }
        }
        
        with open(filename, 'w') as f:
            json.dump(results, f, indent=2)
        
        print(f"\nResults saved to {filename}")

# Main execution
def main(model: str = "llama3"):
    """
    Main execution function
    
    Args:
        model: Ollama model to use (e.g., 'llama2', 'mistral', 'codellama', 'llama2:13b')
    """
    # Load SQL Monitor data
    with open('sql_monitor_parsed.txt', 'r') as f:
        monitor_data = json.load(f)
    
    # Initialize orchestrator with Ollama
    orchestrator = SQLDiagnosisOrchestrator(
        monitor_data=monitor_data,
        model=model,
        max_iterations=10
    )
    
    # Run diagnosis
    diagnosis_result = orchestrator.run_diagnosis()
    
    # Optimize query based on diagnosis
    optimization_result = orchestrator.optimize_query()
    
    # Save results
    orchestrator.save_results()
    
    return diagnosis_result, optimization_result

def run():
    # Example usage with different Ollama models
    # You can change the model based on what you have installed
    # Popular options: 'llama2', 'mistral', 'codellama', 'llama2:13b', 'llama2:70b'
    
    try:
        # You can specify which model to use    
        
        print("Starting SQL Diagnosis with Ollama model")
        
        diagnosis, optimization = main(model="gpt-oss:20b")
        
        print("\n" + "="*60)
        print("FINAL SUMMARY")
        print("="*60)
        print(f"Root Cause: {diagnosis.root_cause}")
        print(f"\nOptimization Applied: Yes")
        with open("final_optimized_query.txt", "w") as f:
            f.write("-- Optimized SQL Query --\n")
            f.write(optimization['optimized_query'])
            f.write("\n\n-- Original Query --\n")
            f.write(optimization['original_query'])
            f.write("\n\n-- Diagnosis --\n")
            f.write(optimization['diagnosis'])
        
    except Exception as e:
        print(f"Error: {e}")
        print("\nTroubleshooting:")
        print("1. Ensure Ollama is installed: curl https://ollama.ai/install.sh | sh")
        print("2. Ensure Ollama is running: ollama serve")
        print("3. Pull the required model: ollama pull llama2")
        print("4. Install required packages: pip install langchain-ollama langchain-core")

run()