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

@dataclass
class PerformanceMetric:
    operation_id: str
    operation_name: str
    metric_type: str
    value: float
    severity: str  # 'critical', 'warning', 'info'
    details: str
    
class SQLMonitorDataExtractor:
    """Enhanced extractor with advanced performance analysis capabilities"""
    
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
                "depth": info.get("depth"),
                "optimizer": optimizer,
                "stats": stats
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
    
    def analyze_cardinality_mismatches(self) -> List[PerformanceMetric]:
        """Detect significant cardinality estimation errors"""
        metrics = []
        operations = self.data.get("operations", {})
        
        for op_id, op_data in operations.items():
            optimizer = op_data.get("optimizer", {})
            stats = op_data.get("stats", {})
            info = op_data.get("info", {})
            
            est_rows_str = optimizer.get("est_rows", "0")
            actual_rows_str = stats.get("rows", "0 rows")
            
            # Parse values
            try:
                est_rows = float(est_rows_str) if est_rows_str else 0
                actual_rows = float(actual_rows_str.replace(" rows", "")) if actual_rows_str else 0
                
                if est_rows > 0:
                    # Calculate mismatch ratio
                    ratio = actual_rows / est_rows if est_rows > 0 else float('inf')
                    
                    # Significant mismatch if ratio > 100 or < 0.01
                    if ratio > 100 or (ratio < 0.01 and actual_rows > 100):
                        severity = 'critical' if ratio > 1000 or (ratio < 0.001 and actual_rows > 1000) else 'warning'
                        
                        metrics.append(PerformanceMetric(
                            operation_id=op_id,
                            operation_name=f"{info.get('name')} {info.get('options', '')}".strip(),
                            metric_type="cardinality_mismatch",
                            value=ratio,
                            severity=severity,
                            details=f"Estimated: {int(est_rows)} rows, Actual: {int(actual_rows)} rows (ratio: {ratio:.2f}x)"
                        ))
            except (ValueError, TypeError):
                pass
                
        return metrics
    
    def analyze_io_hotspots(self) -> List[PerformanceMetric]:
        """Identify operations with high I/O consumption"""
        metrics = []
        operations = self.data.get("operations", {})
        total_io_bytes = 0
        io_operations = []
        
        # First pass: collect all I/O operations
        for op_id, op_data in operations.items():
            stats = op_data.get("stats", {})
            info = op_data.get("info", {})
            
            io_bytes_str = stats.get("io_bytes", "0 bytes")
            io_reqs_str = stats.get("io_reqs", "0 count")
            
            try:
                io_bytes = float(io_bytes_str.replace(" bytes", "")) if io_bytes_str else 0
                io_reqs = float(io_reqs_str.replace(" count", "")) if io_reqs_str else 0
                
                if io_bytes > 0 or io_reqs > 0:
                    io_operations.append({
                        'id': op_id,
                        'name': f"{info.get('name')} {info.get('options', '')}".strip(),
                        'object': info.get('object'),
                        'io_bytes': io_bytes,
                        'io_reqs': io_reqs
                    })
                    total_io_bytes += io_bytes
            except (ValueError, TypeError):
                pass
        
        # Second pass: identify hotspots
        for op in sorted(io_operations, key=lambda x: x['io_bytes'], reverse=True):
            if total_io_bytes > 0:
                io_percentage = (op['io_bytes'] / total_io_bytes) * 100
                
                # Flag operations consuming > 30% of total I/O
                if io_percentage > 30:
                    severity = 'critical' if io_percentage > 50 else 'warning'
                    
                    metrics.append(PerformanceMetric(
                        operation_id=op['id'],
                        operation_name=op['name'],
                        metric_type="io_hotspot",
                        value=op['io_bytes'],
                        severity=severity,
                        details=f"I/O: {op['io_bytes']/1024/1024:.2f} MB ({io_percentage:.1f}% of total), Requests: {int(op['io_reqs'])}"
                    ))
                    
        return metrics
    
    def analyze_cpu_hotspots(self) -> List[PerformanceMetric]:
        """Identify operations with high CPU consumption"""
        metrics = []
        operations = self.data.get("operations", {})
        total_cpu_cost = 0
        cpu_operations = []
        
        # First pass: collect all CPU costs
        for op_id, op_data in operations.items():
            optimizer = op_data.get("optimizer", {})
            info = op_data.get("info", {})
            
            cpu_cost_str = optimizer.get("cpu_cost", "0")
            
            try:
                cpu_cost = float(cpu_cost_str) if cpu_cost_str else 0
                
                if cpu_cost > 0:
                    cpu_operations.append({
                        'id': op_id,
                        'name': f"{info.get('name')} {info.get('options', '')}".strip(),
                        'object': info.get('object'),
                        'cpu_cost': cpu_cost
                    })
                    total_cpu_cost += cpu_cost
            except (ValueError, TypeError):
                pass
        
        # Second pass: identify hotspots
        for op in sorted(cpu_operations, key=lambda x: x['cpu_cost'], reverse=True):
            if total_cpu_cost > 0:
                cpu_percentage = (op['cpu_cost'] / total_cpu_cost) * 100
                
                # Flag operations consuming > 30% of total CPU
                if cpu_percentage > 30:
                    severity = 'critical' if cpu_percentage > 50 else 'warning'
                    
                    metrics.append(PerformanceMetric(
                        operation_id=op['id'],
                        operation_name=op['name'],
                        metric_type="cpu_hotspot",
                        value=op['cpu_cost'],
                        severity=severity,
                        details=f"CPU Cost: {op['cpu_cost']:.0f} ({cpu_percentage:.1f}% of total)"
                    ))
                    
        return metrics
    
    def analyze_nested_loops_depth(self) -> List[PerformanceMetric]:
        """Detect excessive nested loops operations"""
        metrics = []
        operations = self.data.get("operations", {})
        nested_loops_count = 0
        max_depth = 0
        
        for op_id, op_data in operations.items():
            info = op_data.get("info", {})
            stats = op_data.get("stats", {})
            
            if "NESTED LOOPS" in info.get("name", ""):
                nested_loops_count += 1
                depth = info.get("depth", 0)
                max_depth = max(max_depth, depth)
                
                # Check for high iteration count
                executions_str = stats.get("executions", "0 count")
                try:
                    executions = int(executions_str.replace(" count", ""))
                    
                    if executions > 10000:
                        severity = 'critical' if executions > 100000 else 'warning'
                        
                        metrics.append(PerformanceMetric(
                            operation_id=op_id,
                            operation_name=info.get("name"),
                            metric_type="excessive_iterations",
                            value=executions,
                            severity=severity,
                            details=f"Nested loop executed {executions:,} times at depth {depth}"
                        ))
                except (ValueError, TypeError):
                    pass
        
        # Flag excessive nested loop depth
        if nested_loops_count > 2 or max_depth > 3:
            metrics.append(PerformanceMetric(
                operation_id="0",
                operation_name="Query Structure",
                metric_type="nested_loops_depth",
                value=nested_loops_count,
                severity='warning' if nested_loops_count > 2 else 'info',
                details=f"Query has {nested_loops_count} nested loops with max depth {max_depth}"
            ))
            
        return metrics
    
    def analyze_full_table_scans(self) -> List[PerformanceMetric]:
        """Detect full table scans on large tables"""
        metrics = []
        operations = self.data.get("operations", {})
        
        for op_id, op_data in operations.items():
            info = op_data.get("info", {})
            stats = op_data.get("stats", {})
            
            if "TABLE ACCESS" in info.get("name", "") and "FULL" in str(info.get("options", "")):
                rows_str = stats.get("rows", "0 rows")
                io_bytes_str = stats.get("io_bytes", "0 bytes")
                
                try:
                    rows = int(rows_str.replace(" rows", ""))
                    io_bytes = float(io_bytes_str.replace(" bytes", "")) if io_bytes_str else 0
                    
                    # Flag full scans reading > 100MB or > 100k rows
                    if io_bytes > 100 * 1024 * 1024 or rows > 100000:
                        severity = 'critical' if io_bytes > 1024 * 1024 * 1024 else 'warning'
                        
                        metrics.append(PerformanceMetric(
                            operation_id=op_id,
                            operation_name=f"FULL SCAN on {info.get('object', 'Unknown')}",
                            metric_type="full_table_scan",
                            value=io_bytes,
                            severity=severity,
                            details=f"Full scan reading {rows:,} rows, {io_bytes/1024/1024:.2f} MB"
                        ))
                except (ValueError, TypeError):
                    pass
                    
        return metrics
    
    def analyze_wait_events(self) -> List[PerformanceMetric]:
        """Analyze wait event distribution"""
        metrics = []
        db_time = self.data.get("stats", {}).get("db_time_stats", {})
        
        # Parse times
        elapsed_time_str = db_time.get("elapsed_time", "0 microsec")
        cpu_time_str = db_time.get("cpu_time", "0 microsec")
        io_wait_str = db_time.get("user_io_wait_time", "0 microsec")
        cluster_wait_str = db_time.get("cluster_wait_time", "0 microsec")
        
        try:
            elapsed = float(elapsed_time_str.replace(" microsec", "")) / 1000000  # to seconds
            cpu = float(cpu_time_str.replace(" microsec", "")) / 1000000
            io_wait = float(io_wait_str.replace(" microsec", "")) / 1000000
            cluster_wait = float(cluster_wait_str.replace(" microsec", "")) / 1000000
            
            if elapsed > 0:
                io_wait_pct = (io_wait / elapsed) * 100
                cpu_pct = (cpu / elapsed) * 100
                cluster_pct = (cluster_wait / elapsed) * 100
                
                # Flag high I/O wait
                if io_wait_pct > 30:
                    metrics.append(PerformanceMetric(
                        operation_id="0",
                        operation_name="Query Wait Events",
                        metric_type="io_wait",
                        value=io_wait_pct,
                        severity='warning' if io_wait_pct > 30 else 'info',
                        details=f"I/O wait: {io_wait_pct:.1f}% of elapsed time ({io_wait:.2f}s)"
                    ))
                
                # Flag low CPU utilization (possible I/O bound)
                if cpu_pct < 30 and elapsed > 1:
                    metrics.append(PerformanceMetric(
                        operation_id="0",
                        operation_name="Query Wait Events",
                        metric_type="low_cpu_utilization",
                        value=cpu_pct,
                        severity='info',
                        details=f"CPU only {cpu_pct:.1f}% of elapsed time - query may be I/O bound"
                    ))
                    
        except (ValueError, TypeError):
            pass
            
        return metrics
    
    def analyze_index_efficiency(self) -> List[PerformanceMetric]:
        """Analyze index access patterns and efficiency"""
        metrics = []
        operations = self.data.get("operations", {})
        
        for op_id, op_data in operations.items():
            info = op_data.get("info", {})
            stats = op_data.get("stats", {})
            
            # Check for index skip scans (less efficient)
            if "INDEX" in info.get("name", "") and "SKIP SCAN" in str(info.get("options", "")):
                metrics.append(PerformanceMetric(
                    operation_id=op_id,
                    operation_name=f"INDEX SKIP SCAN on {info.get('object', 'Unknown')}",
                    metric_type="inefficient_index",
                    value=0,
                    severity='warning',
                    details="Index skip scan detected - consider adding leading column to WHERE clause"
                ))
            
            # Check for index full scans
            elif "INDEX" in info.get("name", "") and "FULL SCAN" in str(info.get("options", "")):
                metrics.append(PerformanceMetric(
                    operation_id=op_id,
                    operation_name=f"INDEX FULL SCAN on {info.get('object', 'Unknown')}",
                    metric_type="index_full_scan",
                    value=0,
                    severity='info',
                    details="Full index scan - might benefit from more selective predicates"
                ))
                
        return metrics

    def get_comprehensive_analysis(self) -> Dict:
        """Get all performance metrics in a structured format"""
        return {
            "cardinality_mismatches": self.analyze_cardinality_mismatches(),
            "io_hotspots": self.analyze_io_hotspots(),
            "cpu_hotspots": self.analyze_cpu_hotspots(),
            "nested_loops": self.analyze_nested_loops_depth(),
            "full_scans": self.analyze_full_table_scans(),
            "wait_events": self.analyze_wait_events(),
            "index_efficiency": self.analyze_index_efficiency()
        }

class DiagnosisAgent:
    """Enhanced LLM Agent for SQL performance diagnosis using Ollama"""
    
    def __init__(self, model: str = "llama2"):
        self.llm = ChatOllama(model=model, temperature=0.3)
        self.conversation_history = []
        self.system_prompt = """You are an expert Database Administrator specializing in SQL performance tuning.
        Your role is to diagnose SQL performance issues through systematic analysis.
        
        You will receive initial performance metrics that have been automatically detected.
        Use these metrics as a starting point for your diagnosis.
        
        When presented with a SQL query and performance metrics:
        1. Review the provided performance metrics and their severity
        2. Analyze the query structure and execution plan
        3. Request additional specific information if needed
        4. Correlate different metrics to identify root causes
        5. Consider the relationship between cardinality mismatches, I/O patterns, and CPU usage
        6. Provide specific diagnosis with actionable recommendations
        
        Focus on:
        - Cardinality estimation errors and their impact
        - I/O and CPU hotspots in the execution plan
        - Inefficient join methods (nested loops with high iterations)
        - Full table scans on large tables
        - Wait event distribution
        - Index usage efficiency
        
        Ask for information and details step by step. Be specific about what you need.
        No live/direct connection to a database instance.
        Do not ask to run or verify any optimization commands/ diagnostic query blocks, nor wait for any feedback or results.
        When you've identified the root cause and and done provided all the required optimizations, clearly state: "DIAGNOSIS COMPLETE: [summary of issues]"
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
        if "DIAGNOSIS COMPLETE" in response:
            return response.split("DIAGNOSIS COMPLETE:")[1].strip()
        return ""

class StorageAgent:
    """Enhanced LLM Agent with access to SQL Monitor data and performance metrics"""
    
    def __init__(self, monitor_data: Dict, model: str = "llama2"):
        self.llm = ChatOllama(model=model, temperature=0.3)
        self.extractor = SQLMonitorDataExtractor(monitor_data)
        self.conversation_history = []
        self.performance_analysis = self.extractor.get_comprehensive_analysis()
        
    def respond(self, request: str) -> str:
        """Respond to diagnosis agent's requests with relevant data"""
        
        # Build comprehensive system prompt with performance insights
        perf_summary = self._format_performance_summary()
        
        system_prompt = f"""You are a database user with access to SQL Monitor report data of a query execution and performance metrics.
        You provide specific information when requested by the DBA.
        
        Available data:
        - SQL Query: {self.extractor.get_query()[:500]}...
        - Execution Statistics: {json.dumps(self.extractor.get_execution_stats(), indent=2)}
        - Tables involved: {self.extractor.get_table_info()}
        
        Performance Analysis Summary:
        {perf_summary}
        
        When asked for specific information, provide it clearly and concisely.
        Include relevant performance metrics when discussing operations.
        If asked for execution plan, provide the relevant operations with their performance characteristics.
        """
        
        self.conversation_history.append(Message("user", request))
        
        # Check what information is being requested
        request_lower = request.lower()
        
        # Prepare context based on request
        context = ""
        if "execution plan" in request_lower or "plan" in request_lower:
            plan = self.extractor.get_execution_plan()
            # Enhance plan with performance metrics
            enhanced_plan = self._enhance_plan_with_metrics(plan)
            context = f"\n\nExecution Plan with Performance Metrics:\n{json.dumps(enhanced_plan[:10], indent=2)}"
            
        if "statistics" in request_lower or "stats" in request_lower:
            stats = self.extractor.get_execution_stats()
            context += f"\n\nExecution Statistics:\n{json.dumps(stats, indent=2)}"
            
        if "table" in request_lower:
            tables = self.extractor.get_table_info()
            context += f"\n\nTables: {tables}"
            
        if "optimizer" in request_lower:
            opt_env = self.extractor.get_optimizer_env()
            context += f"\n\nOptimizer Environment:\n{json.dumps(opt_env, indent=2)}"
            
        if "cardinality" in request_lower or "estimation" in request_lower:
            card_issues = self.performance_analysis.get("cardinality_mismatches", [])
            if card_issues:
                context += f"\n\nCardinality Mismatches Detected:\n"
                for issue in card_issues[:5]:
                    context += f"- {issue.operation_name}: {issue.details}\n"
                    
        if "hotspot" in request_lower or "bottleneck" in request_lower:
            io_hotspots = self.performance_analysis.get("io_hotspots", [])
            cpu_hotspots = self.performance_analysis.get("cpu_hotspots", [])
            if io_hotspots or cpu_hotspots:
                context += f"\n\nPerformance Hotspots:\n"
                for hotspot in io_hotspots[:3]:
                    context += f"- I/O: {hotspot.operation_name}: {hotspot.details}\n"
                for hotspot in cpu_hotspots[:3]:
                    context += f"- CPU: {hotspot.operation_name}: {hotspot.details}\n"
        
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
    
    def _format_performance_summary(self) -> str:
        """Format performance analysis into readable summary"""
        summary = []
        
        # Critical issues first
        critical_issues = []
        warning_issues = []
        
        for category, metrics in self.performance_analysis.items():
            for metric in metrics:
                if metric.severity == 'critical':
                    critical_issues.append(f"[CRITICAL] {metric.operation_name}: {metric.details}")
                elif metric.severity == 'warning':
                    warning_issues.append(f"[WARNING] {metric.operation_name}: {metric.details}")
        
        if critical_issues:
            summary.append("Critical Performance Issues:")
            summary.extend(critical_issues[:5])
            
        if warning_issues:
            summary.append("\nWarning Performance Issues:")
            summary.extend(warning_issues[:5])
            
        return "\n".join(summary)
    
    def _enhance_plan_with_metrics(self, plan: List[Dict]) -> List[Dict]:
        """Enhance execution plan with performance metrics"""
        enhanced = []
        
        for op in plan:
            op_copy = op.copy()
            op_id = op['id']
            
            # Add performance metrics for this operation
            perf_metrics = []
            
            for category, metrics in self.performance_analysis.items():
                for metric in metrics:
                    if metric.operation_id == op_id:
                        perf_metrics.append({
                            'type': metric.metric_type,
                            'severity': metric.severity,
                            'details': metric.details
                        })
            
            if perf_metrics:
                op_copy['performance_issues'] = perf_metrics
                
            enhanced.append(op_copy)
            
        return enhanced
    
    def get_initial_message(self) -> str:
        """Generate enhanced initial message with detected issues"""
        query = self.extractor.get_query()
        stats = self.extractor.get_execution_stats()
        
        # Get critical issues
        critical_issues = []
        for category, metrics in self.performance_analysis.items():
            for metric in metrics:
                if metric.severity == 'critical':
                    critical_issues.append(metric)
        
        message = f"""I have a SQL query that's facing performance issue. 

Query:
{query[:1000]}...

Performance Issue: The query execution is very slow.
- Elapsed Time: {stats.get('elapsed_time')}
- CPU Time: {stats.get('cpu_time')}
- Buffer Gets: {stats.get('io_stats', {}).get('buffer_gets')}
- Disk Reads: {stats.get('io_stats', {}).get('disk_reads')}

AUTOMATED PERFORMANCE ANALYSIS DETECTED THE FOLLOWING:

"""
        
        # Add detected issues
        if critical_issues:
            message += "Critical Issues Found:\n"
            for issue in critical_issues[:3]:
                message += f"- {issue.operation_name}: {issue.details}\n"
            message += "\n"
        
        # Add summary of all issue categories
        issue_summary = []
        for category, metrics in self.performance_analysis.items():
            if metrics:
                issue_summary.append(f"{category.replace('_', ' ').title()}: {len(metrics)} issues")
        
        if issue_summary:
            message += "Issue Categories: " + ", ".join(issue_summary) + "\n\n"
        
        message += "Can you help diagnose the performance issue based on these findings?"
        
        return message

class OptimizationAgent:
    """Enhanced LLM Agent for query optimization with awareness of detected issues"""
    
    def __init__(self, model: str = "llama2"):
        self.llm = ChatOllama(model=model, temperature=0.3)
        
    def optimize_query(self, original_query: str, diagnosis: str, performance_metrics: Dict = None) -> Dict:
        """Generate optimized query based on diagnosis and performance metrics"""
        
        system_prompt = """You are a SQL optimization expert.
        Based on the performance diagnosis and metrics provided, rewrite the SQL query to address the identified issues.
        
        Focus on addressing:
        1. Cardinality estimation errors - improve statistics or rewrite predicates
        2. I/O hotspots - reduce data access through better indexing or partitioning
        3. CPU hotspots - simplify complex operations or expressions
        4. Nested loops with high iterations - consider hash joins or merge joins
        5. Full table scans - add appropriate indexes or partition pruning
        6. Wait events - optimize based on dominant wait class
        
        Provide:
        1. Optimized SQL query with comments explaining changes
        2. Detailed explanation of each optimization applied
        3. Expected performance improvements with metrics
        4. Additional recommendations (indexes, statistics, hints, etc.)
        5. Alternative optimization strategies if applicable
        """
        
        # Format performance metrics if provided
        perf_context = ""
        if performance_metrics:
            critical_metrics = []
            for category, metrics in performance_metrics.items():
                for metric in metrics:
                    if hasattr(metric, 'severity') and metric.severity == 'critical':
                        critical_metrics.append(f"- {metric.operation_name}: {metric.details}")
            
            if critical_metrics:
                perf_context = "\n\nCritical Performance Metrics:\n" + "\n".join(critical_metrics[:5])
        
        user_prompt = f"""Original Query:
{original_query}

Performance Diagnosis:
{diagnosis}
{perf_context}

Please provide an optimized version of this query addressing all identified issues."""
        
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
            "diagnosis": diagnosis,
            "performance_metrics_addressed": performance_metrics is not None
        }

class SQLDiagnosisOrchestrator:
    """Enhanced orchestrator with comprehensive performance analysis"""
    
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
        latest_diagnosis=""
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
            if "paste" in diagnosis_response and "results" in diagnosis_response:
                self.final_diagnosis=latest_diagnosis
                print("\n" + "="*60)
                print("DIAGNOSIS COMPLETE!")
                print("="*60)
                break

            latest_diagnosis=diagnosis_response
            print("-"*60)
            
            # Storage agent responds with requested information
            storage_response = self.storage_agent.respond(diagnosis_response)
            self.conversation_log.append(("Storage Agent", storage_response))
            print(f"Storage Agent:\n{storage_response}\n")
            print("-"*60)
            
            current_message = storage_response
            
        
        if not self.final_diagnosis:
            self.final_diagnosis = latest_diagnosis
        
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
            self.final_diagnosis,
            self.storage_agent.performance_analysis
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
        
        print(f"Starting SQL Diagnosis with Ollama model: llama3")
        print("Make sure Ollama is running (ollama serve) and the model is pulled\n")
        
        diagnosis, optimization = main(model="gpt-oss:20b")
        
        print("\n" + "="*60)
        print("FINAL SUMMARY")
        print("="*60)
        print(f"Root Cause: {diagnosis.root_cause}")
        print(f"\nOptimization Applied: Yes")
        with open("final_optimized_query.txt", "w") as f:
            f.write("-- Optimized SQL Query --\n")
            f.write(optimization['optimized_query'])
        
    except Exception as e:
        print(f"Error: {e}")
        print("\nTroubleshooting:")
        print("1. Ensure Ollama is installed: curl https://ollama.ai/install.sh | sh")
        print("2. Ensure Ollama is running: ollama serve")
        print("3. Pull the required model: ollama pull llama2")
        print("4. Install required packages: pip install langchain-ollama langchain-core")

run()