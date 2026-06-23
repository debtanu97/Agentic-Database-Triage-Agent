from bs4 import BeautifulSoup
import base64
import zlib
from sentence_transformers import SentenceTransformer, util
import xml.etree.ElementTree as ET
from langchain_ollama import OllamaEmbeddings
from sklearn.cluster import AgglomerativeClustering
import numpy as np
from collections import defaultdict
from sklearn.cluster import DBSCAN

class DataPrep:
    def __init__(self):
        pass

    def group_by_labels(blocks, labels):
        clusters = defaultdict(list)
        for block, label in zip(blocks, labels):
            clusters[int(label)].append(block)   # force int, not numpy.int64
        return list(clusters.values())

    def _parse_html(self, path):
        with open(path, 'r') as file:
            soup = BeautifulSoup(file,"lxml")
            content=str(soup.body)
            main_content = content.split("</report_id>", 1)[-1].split("</report>", 1)[0].strip()
            #decode
            decoded_content=base64.b64decode(main_content)
            #decompress
            decompressed_content=zlib.decompress(decoded_content)
            extract_str=decompressed_content.decode("utf-8")
            extract=extract_str.lstrip("b'")
        with open("decoded_content.html", "w") as f:
            f.write(extract)
    

    def parse_sql_monitor_report(self,path):
        tree = ET.parse(path)
        root = tree.getroot()
        
        sections = []
        for child in root:
            section_name = child.tag
            section_content = ET.tostring(child, encoding="unicode")
            sections.append((section_name, section_content.strip()))
        return sections
    
    def xml_to_dict(self,element):
        return {child.tag: child.text for child in element if child.text}
    
    def get_sqltext_from_xml(self, path):
        tree = ET.parse(path)
        root = tree.getroot()
        for sql in root.iter("sql_fulltext"):
            return sql.text.strip()
        

    def semantic_units_from_xml(self, xml_path, max_len=3000):
        tree = ET.parse(xml_path)
        root = tree.getroot()
        units = []
        ignore_tags = {"script", "style", "metadata", "link", "noscript"}

        def split_long_text(tag_path, text):
            """Split text into fixed-size chunks if too long."""
            chunks = []
            for i in range(0, len(text), max_len):
                chunk = text[i:i+max_len].strip()
                if chunk:
                    chunks.append(f"[{tag_path}]\n{chunk}")
            return chunks

        for section in root:
            if section.tag.lower() in ignore_tags: 
                continue

            if len(section) > 0:
                for sub in section:
                    txt = ET.tostring(sub, encoding="unicode").strip()
                    if txt:
                        tag_path = f"{section.tag}/{sub.tag}"
                        if len(txt) > max_len:
                            units.extend(split_long_text(tag_path, txt))
                        else:
                            units.append(f"[{tag_path}]\n{txt}")
            else:
                txt = section.text
                if txt:
                    if len(txt) > max_len:
                        units.extend(split_long_text(section.tag, txt))
                    else:
                        units.append(f"[{section.tag}]\n{txt}")

        return units
    
    def semantic_units_from_html(self, html_path):
        with open(html_path, "r", encoding="utf-8") as f:
            soup = BeautifulSoup(f, "lxml")  # or "html.parser"

        units = []
        ignore_tags = {"script", "style", "metadata", "link", "noscript"}

        # Iterate over top-level elements in <body>
        for section in soup.body.find_all(recursive=False):
            if section.name.lower() in ignore_tags:
                continue

            # If section has children (tags inside it)
            if section.find_all(recursive=False):
                for sub in section.find_all(recursive=False):
                    if sub.name and sub.name.lower() not in ignore_tags:
                        txt = sub.get_text(" ", strip=True)
                        if txt:
                            units.append(f"[{section.name}/{sub.name}]\n{txt}")
            else:
                txt = section.get_text(" ", strip=True)
                if txt:
                    units.append(f"[{section.name}]\n{txt}")

        return units    

    def cluster_units(self, units, model="llama3", eps=0.4, min_samples=1):
        # embed the units
        embedder = OllamaEmbeddings(model=model)
        vectors = embedder.embed_documents(units)

        # run HDBSCAN
        clustering = DBSCAN(
            metric="euclidean",       # or "cosine" if embeddings work better with cosine
            eps=eps,
            min_samples=min_samples,
        )
        labels = clustering.fit_predict(vectors)

        # organize results
        clusters = {}
        for lbl, unit in zip(labels, units):
            if lbl == -1:
                # optional: collect noise separately
                clusters.setdefault("noise", []).append(unit)
            else:
                clusters.setdefault(lbl, []).append(unit)
        return clusters

    


        





