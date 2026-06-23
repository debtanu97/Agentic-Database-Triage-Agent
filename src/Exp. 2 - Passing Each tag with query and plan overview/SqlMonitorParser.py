from bs4 import BeautifulSoup
import base64
import zlib
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
import json

class SqlMonitorParser:
    def __init__(self,path):
        self.path=path
        self.decoded_html_path=self.decode_and_decompress_html()
        self.tree = ET.parse(self.decoded_html_path)
        self.root = self.tree.getroot()
    
    def decode_and_decompress_html(self):
        with open(self.path, 'r') as file:
            soup = BeautifulSoup(file,"lxml")
            content=str(soup.body)
            header = content.split("<!--FXTMODEL-->", 1)[-1].split("<report_id>",1)[0].strip()
            main_content = content.split("</report_id>", 1)[-1].split("</report>", 1)[0].strip()
            decoded_content=base64.b64decode(main_content)
            #decompress
            decompressed_content=zlib.decompress(decoded_content)
            extract_str=decompressed_content.decode("utf-8")
            extract=extract_str.lstrip("b'")
            combined_doc=header+"\n"+extract+"</report>"
            op_path = "decoded_content.html"
        with open(op_path, "w") as f:
            f.write(combined_doc)
        return op_path

    def getReportAttributes(self):
        attributes = {}
        report_attrs = self.root.attrib
        attributes['elapsed_time'] = report_attrs.get('elapsed_time') + " sec"
        attributes['cpu_time'] = report_attrs.get('cpu_time') + " sec"
        attributes['instance_count'] = report_attrs.get('inst_count')
        attributes['cpu_cores'] = report_attrs.get('cpu_cores')
        attributes['hyperthread_status'] = " Enabled " if (report_attrs.get('hyperthread')=='Y') else " Disabled "
        return attributes

    def getReportParameters(self):
        param=self.root.find("sql_monitor_report").find("report_parameters")
        parameters={}
        parameters['bucket_count']=param.find("bucket_count").text
        parameters['interval_start']=param.find("interval_start").text
        parameters['interval_end']=param.find("interval_end").text
        parameters['duration']=self.root.find("sql_monitor_report").find("target").find("duration").text + " sec"
        return parameters
    
    def getOptimizerEnv(self):
        param=self.root.find("sql_monitor_report").find("target")
        optimizer_env={}
        for env in param.find("optimizer_env"):
            name=env.get("name")
            value=env.text
            if (name =="parallel_execution_message_size"):
                value=value+" Bytes"
            optimizer_env[name]=value
        return optimizer_env
    
    def getQuery(self):
        param=self.root.find("sql_monitor_report").find("target")
        sql_query={}
        full_text=param.find("sql_fulltext").text
        compact_query = " ".join(full_text.split())
        sql_query['query_text']=compact_query
        return sql_query
    
    def getStats(self):
        param = self.root.find("sql_monitor_report").find("stats")
        stats = {
            'db_time_stats': {},
            'io_stats': {},
            'offload_stats': {},
            'exec_stats': {}
        }

        categories = {
            'db_time_stats': [
                'elapsed_time', 'cpu_time', 'user_io_wait_time',
                'concurrency_wait_time', 'cluster_wait_time',
                'application_wait_time', 'plsql_exec_time',
                'other_wait_time'
            ],
            'io_stats': [
                'buffer_gets', 'disk_reads', 'read_reqs',
                'read_bytes', 'unc_bytes', 'elig_bytes', 'ret_bytes'
            ],
            'offload_stats': [
                'cell_offload_efficiency', 'cell_offload_efficiency2'
            ],
            'exec_stats': [
                'user_fetch_count'
            ]
        }

        # Rename cryptic names
        rename_map = {
            'unc_bytes': 'uncompressed_bytes',
            'elig_bytes': 'eligible_bytes',
            'ret_bytes': 'returned_bytes'
        }

        def infer_unit(name: str) -> str:
            if name.endswith("_time"):
                return " microsec"
            if name.endswith("_bytes"):
                return " bytes"
            if name.endswith("_count") or name in ("buffer_gets", "disk_reads", "read_reqs"):
                return " count"
            if "efficiency" in name:
                return " %"
            return ""  # default: no unit

        # Populate stats
        for category, stat_names in categories.items():
            for name in stat_names:
                elem = param.find(f"stat[@name='{name}']")
                if elem is not None and elem.text is not None:
                    pretty_name = rename_map.get(name, name)
                    unit = infer_unit(name)
                    stats[category][pretty_name] = f"{elem.text}{unit}"

        return stats


    
    def getOperations(self):
        operations = {}

        def get_text(elem, default=None):
            return elem.text.strip() if elem is not None and elem.text else default

        # Map optimizer XML tags → dict keys
        opt_map = {
            "cardinality": "est_rows",
            "bytes": "est_bytes",
            "cost": "operation_cost",
            "cpu_cost": "cpu_cost",
            "io_cost": "io_cost",
            "time": "time",
        }

        # Unit inference
        def infer_unit(name: str) -> str:
            if name in ("duration", "time"):
                return " ms"
            if name in ("starts", "executions", "dop"):
                return " count"
            if name in ("cardinality", "rows"):
                return " rows"
            if "reqs" in name:
                return " count"
            if "bytes" in name:
                return " bytes"
            return ""

        # Iterate over <plan_monitor>/<operation>
        for op in self.root.findall(".//plan_monitor/operation"):
            op_id = int(op.attrib.get("id"))

            # ----- Info block -----
            info = {
                "name": op.attrib.get("name"),
                "options": op.attrib.get("options"),
                "depth": int(op.attrib.get("depth")),
                "position": int(op.attrib.get("position")),
                "object": get_text(op.find("object/name")),
                "object_alias": get_text(op.find("object_alias")),
                "qblock": get_text(op.find("qblock")),
                "parent_id": int(op.attrib.get("parent_id")) if "parent_id" in op.attrib else None,
                "predicates": {
                    "storage": get_text(self.root.find(f".//plan/operation[@id='{op_id}']/predicates[@type='storage']")),
                    "filter": get_text(self.root.find(f".//plan/operation[@id='{op_id}']/predicates[@type='filter']")),
                    "access": get_text(self.root.find(f".//plan/operation[@id='{op_id}']/predicates[@type='access']")),
                }
            }

            # ----- Optimizer block -----
            optimizer = {}
            opt_elem = op.find("optimizer")
            if opt_elem is not None:
                for child in opt_elem:
                    if child.tag in opt_map:
                        key = opt_map[child.tag]
                        val = get_text(child)
                        unit = infer_unit(key)
                        optimizer[key] = f"{val}{unit}" if val is not None else None

            # ----- Stats block -----
            stats = {}
            stats_elem = op.find("stats[@type='plan_monitor']")
            if stats_elem is not None:
                for stat in stats_elem.findall("stat"):
                    name = stat.attrib.get("name")
                    text = get_text(stat)
                    if text is None:
                        continue
                    if name == "duration":
                        key = "duration"
                    elif name == "starts":
                        key = "executions"
                    elif name == "cardinality":
                        key = "rows"
                    elif name == "read_reqs":
                        key = "io_reqs"
                    elif name == "read_bytes":
                        key = "io_bytes"
                    else:
                        key = name
                    unit = infer_unit(key)
                    stats[key] = f"{text}{unit}"

            # ----- Build final dict -----
            operations[op_id] = {
                "info": info,
                "optimizer": optimizer,
                "stats": stats,
            }

        return operations



    
    def getActivitySamples(self):
        sql_report = self.root.find(".//sql_monitor_report")

        
        activity_sampled = sql_report.find("activity_sampled")
        activity_sampled_stats = {}

        id_counter = 1
        for act in activity_sampled.findall("activity"):
            cls = act.attrib.get("class")
            event = act.attrib.get("event")
            count = int(act.text)

            activity_sampled_stats[id_counter] = {
                "class": cls,
                "event": event,
                "total_count": count,
                "detail": []
            }
            id_counter += 1

       
        detail_elem = sql_report.find("activity_detail")
        first_sample_time = datetime.strptime(detail_elem.attrib["first_sample_time"], "%d/%m/%Y %H:%M:%S")
        bucket_interval = int(detail_elem.attrib["bucket_interval"])  # in seconds

        
        for bucket in detail_elem.findall("bucket"):
            bucket_no = int(bucket.attrib["number"])
            timestamp = first_sample_time + timedelta(seconds=(bucket_no - 1) * bucket_interval)

            for act in bucket.findall("activity"):
                cls = act.attrib.get("class")
                event = act.attrib.get("event")
                line = int(act.attrib.get("line"))
                count = int(act.text)

               
                for act_id, entry in activity_sampled_stats.items():
                    if entry["class"] == cls and entry.get("event") == event:
                        entry["detail"].append({
                            "count_no": count,
                            "line_no": line,
                            "bucket_no": bucket_no,
                            "timestamp": timestamp.strftime("%d/%m/%Y %H:%M:%S")
                        })
                        break

        return activity_sampled_stats

    def getMetrics(self):      
        stattype = self.root.find(".//stattype[@name='metrics']")

        stat_info_map = {}
        for stat in stattype.find("stat_info").findall("stat"):
            stat_id = stat.attrib["id"]
            stat_name = stat.attrib["name"]
            stat_info_map[stat_id] = stat_name

        metrics = {name: [] for name in stat_info_map.values()}

        buckets_elem = stattype.find("buckets")
        bucket_interval = int(buckets_elem.attrib["bucket_interval"])
        start_time = datetime.strptime(buckets_elem.attrib["start_time"], "%d/%m/%Y %H:%M:%S")

        for bucket in buckets_elem.findall("bucket"):
            bucket_id = int(bucket.attrib["bucket_id"])
            timestamp = start_time + timedelta(seconds=(bucket_id - 1) * bucket_interval)
            timestamp_str = timestamp.strftime("%d/%m/%Y %H:%M:%S")

            for stat in bucket.findall("stat"):
                stat_id = stat.attrib["id"]
                stat_name = stat_info_map[stat_id]
                value = float(stat.attrib["value"])
                
                metrics[stat_name].append({
                    "value": value,
                    "bucket_id": bucket_id,
                    "timestamp": timestamp_str
                })

        return {"metrics": metrics}

    def runner(self, output_file="sql_monitor_parsed.txt"):
        """
        Calls all getter functions, aggregates results, and writes to a text file.
        """

        results = {
            "report_attributes": self.getReportAttributes(),
            "report_parameters": self.getReportParameters(),
            "optimizer_env": self.getOptimizerEnv(),
            "query": self.getQuery(),   
            "stats": self.getStats(),
            "operations": self.getOperations(),
            #"activity_samples": self.getActivitySamples(),
            #"metrics": self.getMetrics()
        }

        with open(output_file, "w") as f:
            f.write(json.dumps(results, indent=4))

        print(f"Parsing complete. Output written to {output_file}")
        return results


parser=SqlMonitorParser("Sql/Push_Predicate/monitor_1.html")
ops=parser.runner()
