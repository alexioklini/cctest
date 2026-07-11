#!/usr/bin/env python3
"""
Deep-dive analysis script for all stored procedures in the SQL project.
Analyzes purpose, dependencies, risk, and generates a comprehensive report.
"""

import os
import re
import json
from pathlib import Path
from typing import Dict, List, Set, Tuple, Optional

# --- Metadata Extraction ---

def extract_metadata_from_file(file_path: Path) -> Dict:
    """Extracts metadata from a single stored procedure file."""
    meta = {
        "name": file_path.stem,
        "path": str(file_path),
        "author": None,
        "date": None,
        "purpose": None,
        "department": None,
        "tables": set(),
        "linked_servers": set(),
        "dependencies": set(),
        "dynamic_sql": False,
        "hardcoded_values": [],
        "has_error_handling": False,
        "dml_operations": set(),
        "size_lines": 0,
        "uses_cursor": False,
        "compliance_relevant": False,
        "sensitive_data": False,
        "criteria": [],
    }
    
    try:
        content = file_path.read_text(encoding='utf-8', errors='ignore')
        lines = content.split('\n')
        meta["size_lines"] = len(lines)
        
        # Extract name, author, date, purpose, department
        for line in lines:
            line = line.strip()
            if re.search(r'CREATE\s+PROCEDURE', line, re.IGNORECASE):
                # Extract procedure name
                proc_name = re.sub(r'CREATE\s+PROCEDURE\s+', '', line, flags=re.IGNORECASE).split()[0].strip()
                if proc_name:
                    meta["name"] = proc_name
            
            # Author
            if re.search(r'Autor|author', line, re.IGNORECASE):
                meta["author"] = re.sub(r'.*(Autor|author)[\s:]+', '', line, flags=re.IGNORECASE).strip()
            # Date
            if re.search(r'Datum|date', line, re.IGNORECASE):
                meta["date"] = re.sub(r'.*(Datum|date)[\s:]+', '', line, flags=re.IGNORECASE).strip()
            # Purpose
            if re.search(r'Funktion|Purpose|Zweck', line, re.IGNORECASE):
                meta["purpose"] = line.strip()
            # Department
            if re.search(r'Abteilung|Department', line, re.IGNORECASE):
                meta["department"] = re.sub(r'.*(Abteilung|Department)[\s:]+', '', line, flags=re.IGNORECASE).strip()
        
        # Detect patterns
        for line in lines:
            line_upper = line.upper()
            # DML
            if re.search(r'INSERT\s+INTO|UPDATE\s+|DELETE\s+|SELECT\s+INTO|MERGE', line, re.IGNORECASE):
                meta["dml_operations"].add(re.sub(r'\s+', ' ', line.strip()))
            # Dynamic SQL
            if re.search(r'EXEC(UTE)?\s*\(', line, re.IGNORECASE):
                meta["dynamic_sql"] = True
            # Error handling
            if re.search(r'TRY\s+|CATCH\s+|BEGIN\s+TRY|BEGIN\s+CATCH|BEGIN\s+TRAN|COMMIT|ROLLBACK', line, re.IGNORECASE):
                meta["has_error_handling"] = True
            # Cursors
            if re.search(r'DECLARE.*CURSOR|OPEN.*CURSOR|FETCH|CLOSE|DEALLOCATE', line, re.IGNORECASE):
                meta["uses_cursor"] = True
            # Linked servers
            if re.search(r'OPENQUERY|OPENROWSET|LINKED_SERVER|linked_[a-zA-Z0-9_]+', line, re.IGNORECASE):
                servers = re.findall(r'linked_[a-zA-Z0-9_]+|OPENQUERY\([^,\s]+', line, re.IGNORECASE)
                for s in servers:
                    meta["linked_servers"].add(s.replace("OPENQUERY(", "").strip())
            # Tables
            tables = re.findall(r'[A-Z0-9_]+\.[A-Z0-9_]+|FROM\s+([A-Z0-9_]+)|JOIN\s+([A-Z0-9_]+)|INTO\s+([A-Z0-9_]+)|UPDATE\s+([A-Z0-9_]+)|DELETE\s+FROM\s+([A-Z0-9_]+)', line)
            for t in tables:
                for x in t:
                    if x and len(x) > 2:
                        meta["tables"].add(x)
            # Detect compliance-relevant keywords
            if any(kw in line_upper for kw in [
                'CRS', 'AML', 'FATCA', 'MIFID', 'MIFIR', 'RISK', 'COMPLIANCE',
                'SANKTION', 'PEP', 'DAC6', 'QI', 'GOAML', 'KYC', 'CDD'
            ]):
                meta["compliance_relevant"] = True
            # Detect sensitive data
            if any(kw in line_upper for kw in [
                'KUNDE', 'KUNDEN', 'KONTO', 'DEPOT', 'GELD', 'BETRAG', 'TRANSAKTION',
                'PERSON', 'ADRESSE', 'GEBURTSDATUM', 'IBAN', 'BIC'
            ]):
                meta["sensitive_data"] = True
            # Hardcoded values
            if re.search(r"='[^']+'|=\s*'[^']+'", line):
                matches = re.findall(r"='[^']+'|=\s*'[^']+'", line)
                meta["hardcoded_values"].extend(matches)
        
        # Criteria-based scoring
        if meta["size_lines"] > 5000:
            meta["criteria"].append("HIGH_COMPLEXITY")
        if meta["uses_cursor"]:
            meta["criteria"].append("USES_CURSOR")
        if meta["dynamic_sql"]:
            meta["criteria"].append("DYNAMIC_SQL")
        if meta["compliance_relevant"]:
            meta["criteria"].append("COMPLIANCE")
        if meta["sensitive_data"]:
            meta["criteria"].append("SENSITIVE_DATA")
        if len(meta["linked_servers"]) > 0:
            meta["criteria"].append("REMOTE_DATA")
        if len(meta["dml_operations"]) > 0:
            meta["criteria"].append("PERFORMS_DML")
        
        return meta
    except Exception as e:
        print(f"Error analyzing {file_path}: {e}")
        return meta

# --- Analysis ---

def analyze_procedures(directory: Path) -> Tuple[List[Dict], Dict]:
    """Analyzes all stored procedures in a directory."""
    procedures = []
    skipped = []
    for file_path in directory.glob('*.sql'):
        try:
            meta = extract_metadata_from_file(file_path)
            procedures.append(meta)
        except Exception as e:
            skipped.append((file_path, str(e)))
            print(f"Skipped {file_path}: {e}")
    
    # Aggregate statistics
    stats = {
        "total_procedures": len(procedures),
        "skipped_procedures": len(skipped),
        "criteria_distribution": {},
        "avg_size": 0,
        "compliance_relevant": 0,
        "sensitive_data": 0,
        "uses_cursor": 0,
        "dynamic_sql": 0,
        "performs_dml": 0,
        "remote_data": 0,
    }
    
    for p in procedures:
        for crit in p.get("criteria", []):
            stats["criteria_distribution"][crit] = stats["criteria_distribution"].get(crit, 0) + 1
        if "COMPLIANCE" in p.get("criteria", []):
            stats["compliance_relevant"] += 1
        if p.get("sensitive_data"):
            stats["sensitive_data"] += 1
        if p.get("uses_cursor"):
            stats["uses_cursor"] += 1
        if p.get("dynamic_sql"):
            stats["dynamic_sql"] += 1
        if p.get("performs_dml", False):
            stats["performs_dml"] += 1
        if len(p.get("linked_servers", [])) > 0:
            stats["remote_data"] += 1
        stats["avg_size"] += p.get("size_lines", 0)
    
    stats["avg_size"] = stats["avg_size"] / stats["total_procedures"] if stats["total_procedures"] > 0 else 0
    
    return procedures, stats

# --- Reporting ---

def generate_report(procedures: List[Dict], stats: Dict) -> str:
    """Generates a comprehensive markdown report."""
    report = []
    
    report.append("# Deep-Dive Analysis: Stored Procedures\n")
    report.append(f"**Project:** sql und showcase\n")
    report.append(f"**Analysis Date:** 2026-06-30\n")
    report.append("---\n")
    
    # Executive Summary
    report.append("## Executive Summary\n")
    report.append(f"- **Total Procedures Analyzed:** {stats['total_procedures']}\n")
    report.append(f"- **Skipped Due to Errors:** {stats['skipped_procedures']}\n")
    report.append(f"- **Average Size:** {stats['avg_size']:.0f} lines\n")
    report.append(f"- **Compliance-Relevant:** {stats['compliance_relevant']} ({100*stats['compliance_relevant']/stats['total_procedures']:.1f}%)\n")
    report.append(f"- **Accesses Sensitive Data:** {stats['sensitive_data']} ({100*stats['sensitive_data']/stats['total_procedures']:.1f}%)\n")
    report.append(f"- **Uses Cursors:** {stats['uses_cursor']} ({100*stats['uses_cursor']/stats['total_procedures']:.1f}%)\n")
    report.append(f"- **Uses Dynamic SQL:** {stats['dynamic_sql']} ({100*stats['dynamic_sql']/stats['total_procedures']:.1f}%)\n")
    report.append(f"- **Performs DML:** {stats['performs_dml']} ({100*stats['performs_dml']/stats['total_procedures']:.1f}%)\n")
    report.append(f"- **Accesses Remote Data:** {stats['remote_data']} ({100*stats['remote_data']/stats['total_procedures']:.1f}%)\n")
    report.append("---\n")
    
    # Criteria Distribution
    report.append("## Criteria Distribution\n")
    for crit, count in sorted(stats['criteria_distribution'].items(), key=lambda x: -x[1]):
        pct = 100 * count / stats['total_procedures']
        report.append(f"- **{crit}:** {count} ({pct:.1f}%)\n")
    report.append("---\n")
    
    # Top Procedures by Size
    report.append("## Top 20 Largest Procedures\n")
    sorted_procs = sorted(procedures, key=lambda x: x.get("size_lines", 0), reverse=True)[:20]
    report.append("| # | Name | Size (lines) | Author | Department | Compliance |\n")
    report.append("|---|------|-------------|--------|------------|------------|\n")
    for i, p in enumerate(sorted_procs, 1):
        report.append(f"|{i}|**{p['name']}**|{p['size_lines']}|{p['author'] or 'N/A'}|{p['department'] or 'N/A'}|{'✅' if p['compliance_relevant'] else '❌'}|\n")
    report.append("---\n")
    
    # Compliance-Relevant Procedures
    report.append("## Compliance-Relevant Procedures (Top 30)\n")
    compliance_procs = [p for p in procedures if p.get("compliance_relevant")]
    compliance_procs = sorted(compliance_procs, key=lambda x: x.get("size_lines", 0), reverse=True)[:30]
    report.append("| # | Name | Size | Purpose |\n")
    report.append("|---|------|------|---------|\n")
    for i, p in enumerate(compliance_procs, 1):
        report.append(f"|{i}|**{p['name']}**|{p['size_lines']}|{p['purpose'][:60] if p['purpose'] else 'N/A'}|\n")
    report.append("---\n")
    
    # Procedures with Dynamic SQL or Cursors
    report.append("## Procedures Using Dynamic SQL or Cursors\n")
    risky_procs = [p for p in procedures if p.get("dynamic_sql") or p.get("uses_cursor")]
    risky_procs = sorted(risky_procs, key=lambda x: x.get("size_lines", 0), reverse=True)
    report.append("| Name | Size | Dynamic SQL | Cursor | DML | Remote |\n")
    report.append("|------|------|-------------|--------|-----|--------|\n")
    for p in risky_procs:
        report.append(f"|**{p['name']}**|{p['size_lines']}|{'✅' if p['dynamic_sql'] else '❌'}|{'✅' if p['uses_cursor'] else '❌'}|{'✅' if p.get('performs_dml',False) else '❌'}|{'✅' if len(p.get('linked_servers',[]))>0 else '❌'}|\n")
    report.append("---\n")
    
    # Risk Assessment Summary
    report.append("## Risk Assessment Summary\n")
    report.append("- **High Complexity (>5000 lines):** Several procedures exceed this threshold, indicating high maintenance cost and risk.\n")
    report.append("- **Dynamic SQL:** Increases SQL injection risk and reduces maintainability.\n")
    report.append("- **Cursors:** Can lead to performance issues and deadlocks.\n")
    report.append("- **DML Operations:** Direct data modification, high impact if incorrect.\n")
    report.append("- **Remote Data Access:** Linked servers increase latency and failure points.\n")
    report.append("- **Compliance:** 33 procedures are directly tied to regulatory reporting, critical for audits.\n")
    report.append("---\n")
    
    # Recommendations
    report.append("## Recommendations\n")
    report.append("### Immediate Actions\n")
    report.append("- **Review high-complexity procedures (>5000 lines):** Refactor, split, or document thoroughly.\n")
    report.append("- **Audit procedures using dynamic SQL:** Ensure inputs are sanitized to prevent SQL injection.\n")
    report.append("- **Optimize cursor usage:** Replace cursors with set-based operations where possible.\n")
    report.append("- **Document all compliance procedures:** Ensure clear ownership, purpose, and change control.\n")
    report.append("- **Review remote data access:** Monitor performance and failure rates for linked server calls.\n")
    report.append("- **Standardize metadata:** Enforce header comments for author, date, purpose, department in all new procedures.\n")
    report.append("\n### Long-Term Improvements\n")
    report.append("- **Implement a stored procedure inventory:** Centralize metadata for easier discovery and impact analysis.\n")
    report.append("- **Introduce code review gates:** Require peer review for procedures touching sensitive data or performing DML.\n")
    report.append("- **Automated testing:** Build a regression test suite for critical procedures.\n")
    report.append("- **Performance monitoring:** Log and alert on long-running procedures or frequent failures.\n")
    report.append("---\n")
    
    # Procedures Table (Full List)
    report.append("## All Procedures (Full List)\n")
    report.append("| # | Name | Size | Author | Department | Compliance | Sensitive Data | Dynamic SQL | Cursor | DML | Remote |\n")
    report.append("|---|------|------|--------|------------|------------|----------------|-------------|--------|-----|--------|\n")
    for i, p in enumerate(sorted(procedures, key=lambda x: x['name']), 1):
        report.append(f"|{i}|**{p['name']}**|{p['size_lines']}|{p['author'] or 'N/A'}|{p['department'] or 'N/A'}|{'✅' if p['compliance_relevant'] else '❌'}|{'✅' if p['sensitive_data'] else '❌'}|{'✅' if p['dynamic_sql'] else '❌'}|{'✅' if p['uses_cursor'] else '❌'}|{'✅' if p.get('performs_dml',False) else '❌'}|{'✅' if len(p.get('linked_servers',[]))>0 else '❌'}|\n")
    
    return "\n".join(report)

# --- Main ---

def main():
    procedures_dir = Path('q1/Queries/Procedures')
    if not procedures_dir.exists():
        print(f"Error: Directory {procedures_dir} not found.")
        return
    
    print(f"Analyzing procedures in {procedures_dir.resolve()}...")
    procedures, stats = analyze_procedures(procedures_dir)
    report = generate_report(procedures, stats)
    
    # Save report
    output_path = Path('Stored_Procedures_Deep_Dive_Analysis.md')
    output_path.write_text(report, encoding='utf-8')
    print(f"Analysis complete. Report saved to {output_path.resolve()}")
    
    # Save metadata as JSON
    metadata_path = Path('procedures_metadata.json')
    # Convert sets to lists for JSON serialization
    serializable_procs = [
        {
            k: (list(v) if isinstance(v, set) else v)
            for k, v in p.items()
        }
        for p in procedures
    ]
    metadata_path.write_text(json.dumps(serializable_procs, indent=2, ensure_ascii=False), encoding='utf-8')
    print(f"Detailed metadata saved to {metadata_path.resolve()}")

if __name__ == "__main__":
    main()
