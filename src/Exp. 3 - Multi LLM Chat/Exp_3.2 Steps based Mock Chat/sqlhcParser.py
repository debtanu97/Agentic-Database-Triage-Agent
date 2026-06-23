"""
Parser for sqlhc ... diagnostics HTML.

What it extracts:
- table_names: set of table names found in the "Tables" section
- table_columns: dict mapping table -> list of dicts {col_name, no_indexes, no_distinct_values}
- views: list of non-editioned view names from "Non Editioned View Metadata"

Output:
Writes JSON to /mnt/data/extracted_metadata.txt

Requires: bs4 (BeautifulSoup), lxml or html.parser
"""

import re
import json
from bs4 import BeautifulSoup
from html import unescape
from pathlib import Path

HTML_PATH = Path("missing_join_sqhlc.html")
OUT_PATH = Path("sqlhc_metadata.json")


def normalize_header_text(cell):
    """Return normalized header text (remove <br>, whitespace, lower)."""
    text = "".join(cell.stripped_strings)
    text = text.replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip().lower()


def find_tables_section(soup):
    """Find the main 'Tables' section table and return row elements."""
    # Approach: find anchor <a name="tables"> then the following <details> that contains the main table.
    # Fallbacks: find <summary> tag whose text contains 'Tables'
    anchor = soup.find(lambda tag: tag.name == "a" and tag.get("name") == "tables")
    if anchor:
        # the <details> containing the section is often the next sibling <details>
        details = anchor.find_next("details")
        if details:
            tbl = details.find("table")
            if tbl:
                return tbl.find_all("tr")
    # fallback: search for summary with text 'Tables'
    summary = soup.find(lambda tag: tag.name == "summary" and "tables" in tag.get_text(strip=True).lower())
    if summary:
        details = summary.find_parent("details")
        if details:
            tbl = details.find("table")
            if tbl:
                return tbl.find_all("tr")
    return []


def extract_table_names(soup):
    """Extract table names from the Tables section."""
    rows = find_tables_section(soup)
    table_names = []
    for tr in rows:
        tds = tr.find_all("td")
        # table rows we care about have many <td>; header rows have <th>
        if not tds or len(tds) < 2:
            continue
        # In the file, second td (index 1) contains the table name string (e.g. anc_absence_plans_f)
        name_cell = tds[1]
        name_text = name_cell.get_text(strip=True)
        if name_text and re.match(r"^[a-z0-9_]+$", name_text, re.I):
            table_names.append(name_text)
    # dedupe and return as sorted set
    return sorted(set(table_names))


def extract_table_columns(soup):
    """
    Find 'Table Columns' detail sections and parse each table block.
    Each such block has a <summary> like 'Table Columns: anc_absence_plans_f (FUSION)'
    """
    table_columns = {}
    # Find all <summary> tags whose text starts with 'Table Columns:'
    summaries = soup.find_all("summary")
    for s in summaries:
        txt = s.get_text(" ", strip=True)
        m = re.match(r"Table Columns:\s*([\w$]+)", txt, re.I)
        if not m:
            # Some summary text might be like "Table Columns: anc_absence_plans_f (FUSION)"
            m = re.match(r"Table Columns:\s*([a-z0-9_]+)", txt, re.I)
        if not m:
            continue
        table_name = m.group(1).lower()
        # the actual table that lists columns typically appears as the next <table> after the summary's parent <details>
        details = s.find_parent("details")
        if details is None:
            continue
        tbl = details.find("table")
        if tbl is None:
            # sometimes there is nested details -> find the next <table> sibling
            tbl = details.find_next("table")
            if tbl is None:
                continue

        # parse header to map columns to indices
        header = tbl.find("tr")
        headers = []
        if header:
            header_cells = header.find_all(["th", "td"])
            for hc in header_cells:
                headers.append(normalize_header_text(hc))
        # header mapping
        # We'll try to find the index of these names:
        # - 'indexes' or 'indexes' (count shown in column 2)
        # - 'column name' (the column name)
        # - 'num distinct' (num distinct values)
        idx_indexes = None
        idx_colname = None
        idx_num_distinct = None
        for i, h in enumerate(headers):
            if "index" in h and idx_indexes is None:
                idx_indexes = i
            if "column name" in h and idx_colname is None:
                idx_colname = i
            if "num" in h and "distinct" in h and idx_num_distinct is None:
                idx_num_distinct = i

        # fallback positions based on how the report looks:
        # from sample: Indexes is at position 1, Column Name at 3 (0-based: 1 and 3), Num Distinct near 11
        if idx_indexes is None:
            idx_indexes = 1
        if idx_colname is None:
            # find column that contains 'Column Name' substring by scanning text of header cells
            for i, h in enumerate(headers):
                if "column" in h and "name" in h:
                    idx_colname = i
                    break
        if idx_colname is None:
            # fallback to 3
            idx_colname = 3
        if idx_num_distinct is None:
            # try to locate header containing 'distinct'
            for i, h in enumerate(headers):
                if "distinct" in h:
                    idx_num_distinct = i
                    break
        if idx_num_distinct is None:
            # fallback to last few columns
            idx_num_distinct = min(len(headers)-1, 11)

        # iterate over data rows (skip header row)
        cols = []
        for row in tbl.find_all("tr")[1:]:
            tds = row.find_all("td")
            if not tds or len(tds) <= max(idx_indexes, idx_colname, idx_num_distinct):
                # skip rows that are not data rows
                continue
            # extract col name
            try:
                col_name = tds[idx_colname].get_text(" ", strip=True)
            except Exception:
                col_name = ""
            if not col_name:
                # skip blank rows
                continue
            # indexes count is often a link cell (e.g. <a href="#i_..."> 3 </a>)
            raw_indexes_text = tds[idx_indexes].get_text(" ", strip=True)
            # extract integer from raw text
            try:
                no_indexes = int(re.search(r"\d+", raw_indexes_text).group())
            except Exception:
                no_indexes = 0
            # num distinct
            raw_distinct = tds[idx_num_distinct].get_text(" ", strip=True)
            try:
                no_distinct = int(re.search(r"\d+", raw_distinct).group())
            except Exception:
                # Sometimes distinct is 0 or empty; set None or 0
                no_distinct = 0

            cols.append({
                "col_name": unescape(col_name),
                "no_indexes": no_indexes,
                "no_distinct_values": no_distinct
            })

        table_columns[table_name] = cols

    return table_columns


def extract_non_editioned_views(soup):
    """Extract view names under 'Non Editioned View Metadata'"""
    views = []
    # locate the Non Editioned View Metadata section summary
    metadata_summary = None
    for summary in soup.find_all("summary"):
        if "non editioned view metadata" in summary.get_text(" ", strip=True).lower():
            metadata_summary = summary
            break
    if metadata_summary:
        details = metadata_summary.find_parent("details")
        if details:
            # inside this details there are nested <details> for each view with summary text like 'View: FUSION.PAY_RANGE_DEFS_V'
            for nested in details.find_all("details"):
                s = nested.find("summary")
                if not s:
                    continue
                txt = s.get_text(" ", strip=True)
                m = re.search(r"View:\s*([A-Z0-9_.$]+)", txt, re.I)
                if m:
                    views.append(m.group(1))
            # also check for anchors like <a name="FUSION.PAY_RANGE_DEFS_V">
            for a in details.find_all("a"):
                name = a.get("name")
                if name and "." in name and name.upper() == name:
                    if name not in views:
                        views.append(name)
    return sorted(set(views))

def extract_views_from_metadata(soup):
    """Extract view names under 'Metadata' (for views) section in SQLHC report"""
    views = []
    # Find the <summary> that says "Metadata"
    metadata_summary = None
    for summary in soup.find_all("summary"):
        if summary.get_text(" ", strip=True).lower() == "metadata":
            metadata_summary = summary
            break
    if not metadata_summary:
        return views

    details = metadata_summary.find_parent("details")
    if not details:
        return views

    # Loop through nested <details> for each view
    for nested in details.find_all("details"):
        s = nested.find("summary")
        if not s:
            continue
        txt = s.get_text(" ", strip=True)
        m = re.search(r"View:\s*([A-Z0-9_.$]+)", txt, re.I)
        if m:
            views.append(m.group(1))

    # Also check for <a name="FUSION.SOMETHING">
    for a in details.find_all("a"):
        name = a.get("name")
        if name and "." in name and name.upper() == name:
            if name not in views:
                views.append(name)

    return sorted(set(views))



def main():
    if not HTML_PATH.exists():
        raise FileNotFoundError(f"HTML input file not found: {HTML_PATH}")

    with HTML_PATH.open("rb") as f:
        soup = BeautifulSoup(f, "lxml")  # lxml or html.parser

    table_names = extract_table_names(soup)
    table_columns = extract_table_columns(soup)
    views = extract_non_editioned_views(soup)
    if not views:
        views=extract_views_from_metadata(soup)

    result = {
        "table_names": table_names,
        "table_columns": table_columns,
        "views": views
    }

    # write JSON to file (pretty)
    with OUT_PATH.open("w", encoding="utf-8") as fo:
        json.dump(result, fo, indent=2, ensure_ascii=False)

    print("Wrote extracted JSON to:", OUT_PATH)
    return result



data = main()
# print summary
print("Tables found:", len(data["table_names"]))
print("Views found:", len(data["views"]))
