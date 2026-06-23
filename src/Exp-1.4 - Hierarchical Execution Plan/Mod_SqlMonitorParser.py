"""
SQL Monitor Query Plan Parser - Pure Parsing Module

A module for parsing SQL execution plans from Oracle SQL Monitor reports.
Preserves exact functionality from the original SqlMonitorQueryPlanParser_2.py
Removes LLM/prompt related functionality - focuses purely on parsing.

Author: Refactored from original SqlMonitorQueryPlanParser_2.py
"""

import os
import re
import base64
import zlib
import json
from typing import Any, Dict, List, Tuple, Union
from bs4 import BeautifulSoup
from xml.etree import ElementTree as ET


class SqlMonitorParser:
    """
    Main parser class for SQL Monitor reports.
    Preserves exact parsing functionality from original code.
    """
    
    def __init__(self):
        self._decoded_path: str = ""
        self._content: str = ""
        self._sql_query: str = ""
        self._execution_plan: List[Dict[str, Any]] = []
        self._total_stats: Dict[str, Any] = {}
        self._is_parsed: bool = False
    
    # ========== PUBLIC METHODS ==========
    
    def parse_file(self, file_path: str) -> None:
        """Parse SQL monitor file - equivalent to original extract_plan_from_file"""
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File not found: {file_path}")
        
        try:
            # Decode and decompress the file (original functionality)
            self._decoded_path = self._decode_and_decompress_html(file_path)
            
            with open(self._decoded_path, "r", encoding="utf-8") as f:
                self._content = f.read()
            
            # Extract SQL query (original logic)
            query_match = re.search(r"<sql_fulltext[^>]*>(.*?)</sql_fulltext>", self._content, re.S)
            self._sql_query = query_match.group(1).strip() if query_match else ""

            # Parse plan XML (original logic)
            plan_match = re.search(r"<plan>(.*?)</plan>", self._content, re.S)
            if not plan_match:
                raise ValueError("No <plan> section found")
            
            plan_xml = "<plan>" + plan_match.group(1) + "</plan>"
            plan_root = ET.fromstring(plan_xml)

            # Parse each operation (original flat list logic)
            flat_nodes = [self._parse_operation(op) for op in plan_root.findall("operation")]

            # Attach activity sampled info (original logic)
            self._attach_activity_sampled(flat_nodes)

            # Build hierarchical structure using depth (original logic)
            nested_tree = self._build_plan_tree(flat_nodes)

            # Parse plan_monitor maps (original logic)
            optimizer_map, stats_map = self._parse_plan_monitor_maps()

            # Cleanup (original logic)
            nested_tree = [self._cleanup_plan_tree(n) for n in nested_tree]

            # Attach maps by id (original order)
            for node in nested_tree:
                self._attach_maps_to_tree(node, optimizer_map, stats_map)

            self._execution_plan = nested_tree
            
            # Extract total stats (original logic)
            self._total_stats = self._extract_total_stats()
            # print(json.dumps(nested_tree, indent=2))

            self._is_parsed = True
                
        except Exception as e:
            raise ValueError(f"Failed extracting plan: {str(e)}")
    
    def get_sql_query(self) -> str:
        """Get the SQL query text"""
        self._check_parsed()
        return self._sql_query
    
    def get_full_execution_plan(self) -> List[Dict[str, Any]]:
        """Get complete hierarchical execution plan with all nodes"""
        self._check_parsed()
        return self._execution_plan
    
    def get_flagged_nodes(self) -> List[Dict[str, Any]]:
        """Get flagged problematic nodes (original flag_problematic_nodes logic)"""
        self._check_parsed()
        if not self._execution_plan:
            return []
        
        # Original logic: strip fields then flag
        simplified_exec_plan = self._strip_fields(self._execution_plan[0].copy())
        return self._flag_problematic_nodes(simplified_exec_plan, self._total_stats)
    
    def get_execution_plan_sequence(self) -> List[str]:
        """Get readable plan sequence (original plan_to_sequence logic)"""
        self._check_parsed()
        if not self._execution_plan:
            return []
        
        simplified_exec_plan = self._strip_fields(self._execution_plan[0].copy())
        return self._plan_to_sequence(simplified_exec_plan)
    
    def get_total_stats(self) -> Dict[str, Any]:
        """Get total execution statistics (original extract_total_stats logic)"""
        self._check_parsed()
        return self._total_stats
    
    # ========== PRIVATE METHODS (ORIGINAL LOGIC PRESERVED) ==========
    
    def _check_parsed(self) -> None:
        """Check if file has been parsed"""
        if not self._is_parsed:
            raise RuntimeError("No file has been parsed. Call parse_file() first.")
    
    def _decode_and_decompress_html(self, file_path: str) -> str:
        """Original decode_and_decompress_html function"""
        with open(file_path, 'r', encoding='utf-8') as file:
            soup = BeautifulSoup(file, "lxml")
            content = str(soup.body or "")

            header = content.split("<!--FXTMODEL-->", 1)[-1].split("<report_id>", 1)[0].strip()
            main_content = content.split("</report_id>", 1)[-1].split("</report>", 1)[0].strip()

            # decode/decompress
            decoded_content = base64.b64decode(main_content)
            decompressed_content = zlib.decompress(decoded_content)
            extract_str = decompressed_content.decode("utf-8")
            extract = extract_str.lstrip("b'")

            combined_doc = header + "\n" + extract + "</report>"
            parts = file_path.split(os.sep)[-2:]
            path = "_".join(parts)
            op_path = f"decoded_content_{path}.html"

        with open(op_path, "w", encoding="utf-8") as f:
            f.write(combined_doc)
        return op_path
    
    @staticmethod
    def _to_number(s: str) -> Union[int, float, str]:
        """Original _to_number function"""
        if s is None:
            return s
        s = s.strip()
        if s == "":
            return s
        # integer?
        if re.fullmatch(r"-?\d+", s):
            return int(s)
        # float?
        if re.fullmatch(r"-?\d+\.\d+", s):
            try:
                return float(s)
            except Exception:
                return s
        return s
    
    def _parse_predicates(self, predicate_text):
        """Original parse_predicates function"""
        # If it's an Element, extract combined text
        if isinstance(predicate_text, ET.Element):
            text = "".join(predicate_text.itertext()).strip()
        else:
            text = (predicate_text or "").strip()

        # keep your original algorithm (recursively split top-level AND/OR)
        def parse_expr(expr):
            expr = expr.strip()
            if expr.startswith("(") and expr.endswith(")"):
                return parse_expr(expr[1:-1].strip())

            depth = 0
            tokens = []
            last = 0
            current_op = None
            i = 0
            L = len(expr)
            while i < L:
                ch = expr[i]
                if ch == "(":
                    depth += 1
                    i += 1
                    continue
                if ch == ")":
                    depth -= 1
                    i += 1
                    continue
                if depth == 0:
                    # check for " AND " or " OR "
                    if expr[i:i+5].upper() == " AND ":
                        tokens.append(expr[last:i].strip())
                        current_op = "AND" if current_op is None else current_op
                        last = i + 5
                        i = last
                        continue
                    if expr[i:i+4].upper() == " OR ":
                        tokens.append(expr[last:i].strip())
                        current_op = "OR" if current_op is None else current_op
                        last = i + 4
                        i = last
                        continue
                i += 1

            if current_op:
                tokens.append(expr[last:].strip())
                return {"operator": current_op, "conditions": [parse_expr(t) for t in tokens if t]}
            else:
                return {"expr": expr}

        return parse_expr(text)
    
    def _parse_operation(self, elem: ET.Element) -> dict:
        """Parse a single plan operation into a node dict (patched)."""
        node: Dict[str, Any] = {}

        # copy attributes
        for k, v in elem.attrib.items():
            node[k] = v

        # default children
        node.setdefault("children", [])

        # direct child tags
        for child in elem:
            tag = child.tag.lower()
            text = (child.text or "").strip()

            if tag == "optimizer":
                estimates = {}
                for g in child:
                    t = (g.text or "").strip()
                    if not t:
                        continue
                    estimates[g.tag.lower()] = self._to_number(t)
                if estimates:
                    node.setdefault("estimates", {}).update(estimates)
                continue

            if tag == "stats" and child.attrib.get("type") == "plan_monitor":
                actuals = {}
                for stat in child.findall("stat"):
                    name = stat.attrib.get("name")
                    val = (stat.text or "").strip()
                    if val != "":
                        num_val = self._to_number(val)
                        actuals[name] = num_val
                        if name == "starts":
                            node["starts"] = num_val
                if actuals:
                    node.setdefault("actuals", {}).update(actuals)
                continue

            if tag == "activity_sampled":
                activity = {}
                for act in child.findall("activity"):
                    cls = act.attrib.get("class")
                    val = (act.text or "").strip()
                    if val != "":
                        activity[cls] = self._to_number(val)
                if activity:
                    node["activity_sampled"] = activity
                continue

            if tag == "predicates":
                pred_type = child.attrib.get("type", "unknown")
                inner_text = "".join(child.itertext()).strip()
                if inner_text:
                    node.setdefault("predicates", {})[pred_type] = self._parse_predicates(inner_text)
                for expr in child.findall("expr"):
                    expr_text = (expr.text or "").strip()
                    if expr_text:
                        node.setdefault("predicates", {})[pred_type] = self._parse_predicates(expr_text)
                continue

            if tag == "object":
                if text:
                    node["object"] = text
                continue

            if tag == "object_alias":
                if text:
                    node["object_alias"] = text
                continue

            if tag == "qblock":
                if text:
                    node["qblock"] = text
                continue

            if tag in {"cost", "card", "bytes", "cpu_cost", "io_cost", "time", "position", "pos"}:
                if text != "":
                    node[tag] = self._to_number(text)
                continue

            if tag == "operation":
                continue

            if text:
                node[tag] = text

        # recurse into child operations
        children = [self._parse_operation(op) for op in elem.findall("operation")]
        if children:
            node["children"] = children

        # infer object type from operation name
        op_name = node.get("name", "").upper()
        if "VIEW" in op_name:
            node["object_type"] = "VIEW"
        elif "INDEX" in op_name:
            node["object_type"] = "INDEX"
        elif "TABLE" in op_name:
            node["object_type"] = "TABLE"
        else:
            node["object_type"] = "UNKNOWN"

        return node
    
    def _build_plan_tree(self, flat_plan: List[dict]) -> List[dict]:
        """Original build_plan_tree function"""
        roots: List[dict] = []
        stack: List[dict] = []

        for node in flat_plan:
            # ensure children field exists (we will reset)
            node.setdefault("children", [])

            # If node doesn't have depth, fallback to top-level append
            depth = int(node.get("depth", 0))

            # pop while top of stack has depth >= node.depth
            while stack and int(stack[-1].get("depth", 0)) >= depth:
                stack.pop()

            if stack:
                stack[-1]["children"].append(node)
            else:
                roots.append(node)

            stack.append(node)

        return roots
    
    def _parse_plan_monitor_maps(self) -> Tuple[Dict[str, dict], Dict[str, dict]]:
        """Original parse_plan_monitor_maps function"""
        optimizer_map: Dict[str, dict] = {}
        stats_map: Dict[str, dict] = {}

        pm_match = re.search(r"<plan_monitor[^>]*>(.*?)</plan_monitor>", self._content, re.S)
        if not pm_match:
            return optimizer_map, stats_map

        pm_xml = "<plan_monitor>" + pm_match.group(1) + "</plan_monitor>"
        try:
            pm_root = ET.fromstring(pm_xml)
        except ET.ParseError:
            return optimizer_map, stats_map

        for op in pm_root.findall("operation"):
            op_id = op.attrib.get("id")
            if not op_id:
                continue

            # --- Optimizer block (estimates)
            opt = op.find("optimizer")
            if opt is not None:
                est = {}
                for c in opt:
                    tag = c.tag.lower()
                    val = (c.text or "").strip()
                    if not val:
                        continue
                    if tag == "cardinality":
                        est["card"] = self._to_number(val)
                    else:
                        est[tag] = self._to_number(val)
                if est:
                    optimizer_map[op_id] = {"estimated": est}

            # --- Stats block (actuals)
            stats = op.find("stats")
            if stats is not None and stats.attrib.get("type") == "plan_monitor":
                actual = {}
                for stat in stats.findall("stat"):
                    name = stat.attrib.get("name")
                    val = (stat.text or "").strip()
                    if not val:
                        continue
                    if name == "cardinality":  # normalize like we did for estimates
                        actual["card"] = self._to_number(val)
                    else:
                        actual[name] = self._to_number(val)
                if actual:
                    stats_map[op_id] = {"actual": actual}

        return optimizer_map, stats_map
    
    def _attach_activity_sampled(self, nodes: List[Dict[str, Any]]) -> None:
        """Original attach_activity_sampled function"""
        plan_mon_match = re.search(r"<plan_monitor.*?>(.*?)</plan_monitor>", self._content, re.S)
        if not plan_mon_match:
            return

        plan_mon_xml = "<plan_monitor>" + plan_mon_match.group(1) + "</plan_monitor>"
        plan_mon_root = ET.fromstring(plan_mon_xml)

        # Build mapping id -> activity dict
        activity_map = {}
        for op in plan_mon_root.findall("operation"):
            op_id = op.attrib.get("id")
            if not op_id:
                continue
            activity = {}
            act_elem = op.find("activity_sampled")
            if act_elem is not None:
                for act in act_elem.findall("activity"):
                    cls = act.attrib.get("class")
                    val = (act.text or "").strip()
                    if val:
                        activity[cls] = self._to_number(val)
            if activity:
                activity_map[op_id] = activity

        # Recursive helper to apply activity_sampled to each node
        def apply_activity(node: Dict[str, Any]):
            op_id = node.get("id")
            if op_id and op_id in activity_map:
                node["activity_sampled"] = activity_map[op_id]
            for child in node.get("children", []):
                apply_activity(child)

        for n in nodes:
            apply_activity(n)
    
    def _attach_maps_to_tree(self, node: dict,
                            optimizer_map: Dict[str, dict],
                            stats_map: Dict[str, dict]) -> dict:
        """Original attach_maps_to_tree function"""
        op_id = node.get("id")

        # attach optimizer (estimated)
        if op_id in optimizer_map:
            node.update(optimizer_map[op_id])

        # attach stats (actuals)
        if op_id in stats_map:
            node.update(stats_map[op_id])

        # recurse into children
        if "children" in node and isinstance(node["children"], list):
            for child in node["children"]:
                self._attach_maps_to_tree(child, optimizer_map, stats_map)

        return node
    
    def _cleanup_plan_tree(self, node: Union[dict, List[dict]]):
        """Original cleanup_plan_tree function"""
        if isinstance(node, list):
            return [self._cleanup_plan_tree(n) for n in node]

        if not isinstance(node, dict):
            return node

        # If SELECT STATEMENT has an 'estimated' but the op-level didn't have optimizer info,
        # don't invent fields. (This prevents copying child cpu/io into parent.)
        if node.get("name") == "SELECT STATEMENT" and "estimated" in node:
            # Only keep `cost` if present in estimated; otherwise remove estimated entirely
            est = node.get("estimated", {})
            filtered = {}
            if "cost" in est:
                filtered["cost"] = est["cost"]
            if filtered:
                node["estimated"] = filtered
            else:
                node.pop("estimated", None)

        # Recurse
        for c in node.get("children", []):
            self._cleanup_plan_tree(c)

        # prune empty dicts
        to_delete = []
        for k, v in list(node.items()):
            if v in (None, {}, []) and k not in ("name", "children"):
                to_delete.append(k)
        for k in to_delete:
            node.pop(k, None)

        return node
    
    def _extract_total_stats(self) -> dict:
        """Original extract_total_stats logic"""
        # Extract the <sql_monitor_report ...> ... </sql_monitor_report>
        m = re.search(r"<sql_monitor_report[^>]*>(.*?)</sql_monitor_report>", self._content, re.S)
        if not m:
            return {}

        xml = "<sql_monitor_report" + m.group(0).split("<sql_monitor_report", 1)[1]  # full valid xml
        root = ET.fromstring(xml)

        out = {}

        # --- Stats ---
        stats_node = root.find("stats[@type='monitor']")
        if stats_node is not None:
            for stat in stats_node.findall("stat"):
                name = stat.attrib.get("name")
                text = (stat.text or "").strip()
                if text == "":
                    out[name] = None
                else:
                    out[name] = self._to_number(text)

        # --- Activity sampled ---
        activity_node = root.find("activity_sampled")
        if activity_node is not None:
            activity = {}
            for act in activity_node.findall("activity"):
                cls = act.attrib.get("class")
                val = (act.text or "").strip()
                if val != "":
                    activity[cls] = self._to_number(val)
            if activity:
                out["activity_sampled"] = activity

        return out
    
    def _strip_fields(self, plan_node):
        """Original strip_fields function"""
        # fields to drop
        drop_keys = {
            "from_most_recent",
            "from_sql_exec_start",
            "first_active",
            "last_active",
            "object_alias",
            "qblock",
            "time",
            "max_starts",
            "dop"
        }

        # remove from node
        for key in list(plan_node.keys()):
            if key in drop_keys:
                del plan_node[key]

        # recursively clean children
        for child in plan_node.get("children", []):
            self._strip_fields(child)

        return plan_node
    
    def _flag_problematic_nodes(self, plan, total_stats):
        """Flag problematic nodes with enriched context (parent_chain aware)."""
        flagged = []

        def to_int(x):
            try:
                return int(x)
            except Exception:
                return 0

        # visit now carries a parent_chain list, root -> ... -> parent
        def visit(node, parent_chain):
            if not isinstance(node, dict):
                return

            est = node.get("estimated", {})
            act = node.get("actual", {})
            reasons = []

            est_card = to_int(est.get("card"))
            act_card = to_int(act.get("card"))

            # --- Cardinality mismatch ---
            # if est_card > 0 and act_card > 0:
            #     ratio = abs(act_card - est_card) / max(est_card, max(act_card, 1))
            #     if ratio > 0.999:
            #         reasons.append(f"Cardinality mismatch: estimated {est_card} vs actual {act_card}")
            # elif est_card == 0 and act_card > 100:
            #     reasons.append(f"Cardinality mismatch: estimated 0 vs actual {act_card}")
            # elif act_card == 0 and est_card > 100:
            #     reasons.append(f"Cardinality mismatch: actual 0 vs extimated {est_card}")

            # --- duration hotspot ---
            if "actual" in node and "duration" in node["actual"]:
                row_duration = node["actual"]["duration"]
                total_duration = (total_stats.get("elapsed_time", 0))/1e6
                if total_duration > 0 and row_duration >= 0.9 * total_duration:
                    reasons.append(f"Duration {row_duration} is >= 90% of total elapsed_time {total_duration}")

            # --- activity hotspot ---
            if "activity_sampled" in node and node["activity_sampled"]:
                for activity_type, value in node["activity_sampled"].items():
                    total_value = total_stats.get("activity_sampled", {}).get(activity_type, 0)
                    if total_value > 0 and value >= 0.9 * total_value:
                        reasons.append(f"Activity {activity_type} = {value} is >= 90% of total {total_value}")

            if reasons:
                # compute nearest view ancestor (if any) by scanning parent_chain from nearest -> farthest
                nearest_view = None
                ancestor_chain = []
                for p in reversed(parent_chain):  # nearest parent first
                    ancestor_chain.append({"id": p.get("id"), "name": p.get("name"), "object": p.get("object")})
                    if p.get("object_type") == "VIEW" and p.get("object"):
                        nearest_view = p.get("object")
                        # don't break — we still want full ancestor_chain list (but we could break if desired)
                        break

                # collect candidate tokens for SQL search:
                # - nearest_view, - local alias (if present), - column names from local predicates
                extra_tokens = []
                if nearest_view:
                    extra_tokens.append(nearest_view)

                # try to extract alias token from the node or nearest_view node
                def clean_alias(a):
                    if not a: return None
                    return a.replace('"', '').split('@')[0]
                node_alias = node.get("object_alias") or None
                if node_alias:
                    aa = clean_alias(node_alias)
                    if aa:
                        extra_tokens.append(aa)
                # try parent alias from parent_chain
                for p in parent_chain[::-1]:
                    if p.get("object_alias"):
                        aa = clean_alias(p.get("object_alias"))
                        if aa and aa not in extra_tokens:
                            extra_tokens.append(aa)
                            break

                # parse predicate column names (very small lightweight parse)
                pred_cols = set()
                preds = node.get("predicates") or {}
                for k, pred in (preds.items()):
                    # extract quoted column tokens
                    import re
                    if isinstance(pred, str):
                        cols = re.findall(r'\"([A-Z0-9_]+)\"', pred)
                        for c in cols:
                            pred_cols.add(c)
                for c in list(pred_cols)[:6]:
                    if c not in extra_tokens:
                        extra_tokens.append(c)

                # produce compact SQL context
                sql_snips = self.get_object_sql_context(node.get("object") or nearest_view or "", extra_tokens)

                flagged.append({
                    "id": node.get("id"),
                    "operation": node.get("name"),
                    "object": node.get("object"),
                    "object_type": node.get("object_type"),
                    "nearest_view_ancestor": nearest_view,
                    "ancestor_chain": ancestor_chain,
                    "predicates": {
                        "local": node.get("predicates"),
                        "inherited": None  # inherited handling can be filled if you pass parent's predicates explicitly
                    },
                    "sql_context_snippets": sql_snips,
                    "reasons": reasons,
                    "estimated": {
                        "card": est.get("card"),
                        "bytes": est.get("bytes"),
                        "cost": est.get("cost"),
                        "cpu_cost": est.get("cpu_cost"),
                        "io_cost": est.get("io_cost"),
                        "time": est.get("time"),
                    },
                    "actual": {
                        "card": act.get("card"),
                        "read_reqs": act.get("read_reqs"),
                        "read_bytes": act.get("read_bytes"),
                        "cpu_cost": act.get("cpu_cost"),
                        "max_card": act.get("max_card"),
                        "dop": act.get("dop"),
                        "duration": act.get("duration"),
                        "executions": act.get("starts")
                    }
                })

            # call children with new parent_chain
            next_parent_chain = parent_chain + [node]
            for child in node.get("children", []):
                visit(child, next_parent_chain)

        # start recursion
        visit(plan, [])
        return flagged
    
    def get_object_sql_context(self, object_name: str, extra_tokens: list = None) -> str:
        """
        Return a compact SQL snippet relevant to object_name.
        - Searches for the object_name, its alias (if available), and tokens in extra_tokens.
        - Returns list of small snippets (token + short matched text).
        """
        if not self._sql_query or not object_name:
            return ""

        q = self._sql_query
        snippets = []

        # Basic: search for the object name (case-insensitive)
        import re
        def find_snip(tok):
            if not tok:
                return None
            # return up to ~120 chars of context around the first match
            pat = re.compile(r'.{0,120}\b' + re.escape(tok) + r'\b.{0,120}', re.I)
            m = pat.search(q)
            if m:
                s = m.group(0).replace("\n", " ").strip()
                return s
            return None

        # 1) view/object itself
        s = find_snip(object_name)
        if s:
            snippets.append({"token": object_name, "snippet": s})

        # 2) any extra tokens (alias, predicate columns)
        if extra_tokens:
            for tok in extra_tokens:
                if tok and tok.upper() != object_name.upper():
                    s = find_snip(tok)
                    if s:
                        snippets.append({"token": tok, "snippet": s})
                        if len(snippets) >= 6:
                            break

        # 3) fallback: try common predicate column names found in predicates
        if len(snippets) < 1:
            fallback_cols = ["BASE_NAME", "SOURCE_TYPE", "SOURCE_ID", "ENTERPRISE_ID"]
            for c in fallback_cols:
                s = find_snip(c)
                if s:
                    snippets.append({"token": c, "snippet": s})
                    if len(snippets) >= 6:
                        break

        # Return compact JSON-like string (the caller may keep it as list)
        return snippets

    def _plan_to_sequence(self, plan, prefix=""):
        """Original plan_to_sequence function"""
        parts = []

        # Base operation
        node_str = plan["name"]
        if plan.get("options"):
            node_str += f" {plan['options']}"
        if plan.get("object"):
            node_str += f" ({plan['object']})"
        parts.append(f"{prefix}{node_str}")

        # Recurse children
        for child in plan.get("children", []):
            parts.extend(self._plan_to_sequence(child, prefix + "  -> "))

        return parts


# ========== STANDALONE FUNCTIONS (ORIGINAL FUNCTIONALITY) ==========

def decode_and_decompress_html(file_path: str) -> str:
    """Original decode_and_decompress_html function"""
    parser = SqlMonitorParser()
    return parser._decode_and_decompress_html(file_path)

def parse_predicates(predicate_text):
    """Original parse_predicates function"""
    parser = SqlMonitorParser()
    return parser._parse_predicates(predicate_text)

def parse_operation(elem: ET.Element) -> dict:
    """Original parse_operation function"""
    parser = SqlMonitorParser()
    return parser._parse_operation(elem)

def build_plan_tree(flat_plan: List[dict]) -> List[dict]:
    """Original build_plan_tree function"""
    parser = SqlMonitorParser()
    return parser._build_plan_tree(flat_plan)

def parse_plan_monitor_maps(content: str) -> Tuple[Dict[str, dict], Dict[str, dict]]:
    """Original parse_plan_monitor_maps function"""
    parser = SqlMonitorParser()
    parser._content = content
    return parser._parse_plan_monitor_maps()

def attach_activity_sampled(content: str, nodes: List[Dict[str, Any]]) -> None:
    """Original attach_activity_sampled function"""
    parser = SqlMonitorParser()
    parser._content = content
    parser._attach_activity_sampled(nodes)

def attach_maps_to_tree(node: dict,
                        optimizer_map: Dict[str, dict],
                        stats_map: Dict[str, dict]) -> dict:
    """Original attach_maps_to_tree function"""
    parser = SqlMonitorParser()
    return parser._attach_maps_to_tree(node, optimizer_map, stats_map)

def cleanup_plan_tree(node: Union[dict, List[dict]]):
    """Original cleanup_plan_tree function"""
    parser = SqlMonitorParser()
    return parser._cleanup_plan_tree(node)

def extract_plan_from_file(file_path: str) -> dict:
    """Original extract_plan_from_file function"""
    parser = SqlMonitorParser()
    parser.parse_file(file_path)
    return {
        "sql_query": parser.get_sql_query(),
        "execution_plan": parser.get_full_execution_plan()
    }

def extract_total_stats(file_path: str) -> dict:
    """Original extract_total_stats function"""
    parser = SqlMonitorParser()
    parser.parse_file(file_path)
    return parser.get_total_stats()

def strip_fields(plan_node):
    """Original strip_fields function"""
    parser = SqlMonitorParser()
    return parser._strip_fields(plan_node)

def flag_problematic_nodes(plan, total_stats):
    """Original flag_problematic_nodes function"""
    parser = SqlMonitorParser()
    return parser._flag_problematic_nodes(plan, total_stats)

def plan_to_sequence(plan, prefix=""):
    """Original plan_to_sequence function"""
    parser = SqlMonitorParser()
    return parser._plan_to_sequence(plan, prefix)


# ========== EXAMPLE USAGE ==========

if __name__ == "__main__":
    # Example usage - matches original main functionality exactly
    file_path = "Sql/Missing_Index/monitor_1.html"

    # extract total monitor stats
    try:
        total_stats = extract_total_stats(file_path)
        print("Total stats:", json.dumps(total_stats, indent=2))
    except Exception as e:
        print("Failed extracting total stats:", e)

    # extract plan
    try:
        result = extract_plan_from_file(file_path)
        print("SQL query:\n", result["sql_query"][:400], "...\n")        
        # print(json.dumps(result["execution_plan"], indent=2))
    
        simplified_exec_plan = strip_fields(result["execution_plan"][0])
        print(json.dumps(simplified_exec_plan, indent=2))

        flagged_nodes = flag_problematic_nodes(simplified_exec_plan, total_stats)
        print(json.dumps(flagged_nodes, indent=2))
        
        plan_seq = plan_to_sequence(simplified_exec_plan)
        print(plan_seq)

        # Additional demonstration of class-based usage
        print("\n" + "="*50)
        print("Class-based usage example:")
        print("="*50)
        
        parser = SqlMonitorParser()
        parser.parse_file(file_path)
        
        print(f"SQL Query Length: {len(parser.get_sql_query())} characters")
        print(f"Full Execution Plan: {len(parser.get_full_execution_plan())} root nodes")
        print(f"Flagged Nodes: {len(parser.get_flagged_nodes())} problematic operations")
        print(f"Plan Sequence: {len(parser.get_execution_plan_sequence())} operations")
        
        # Show execution plan sequence
        print("\nExecution Plan Sequence:")
        for line in parser.get_execution_plan_sequence():
            print(line)

    except FileNotFoundError:
        print(f"File not found: {file_path}")
        print("Please update the file_path variable to point to a valid SQL monitor file.")
    except Exception as e:
        print("Failed extracting plan:", e)