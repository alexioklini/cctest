import os
import re
from collections import defaultdict

# Projektverzeichnis
PROJECT_DIR = "/Users/alexander/Documents/dev/cctest/eval/sql"

# Dateiendungen, die durchsucht werden sollen
SQL_FILE_EXTENSIONS = (".sql", ".dbq")

# Muster für Tabellen- und Spaltenextraktion
TABLE_PATTERNS = [
    re.compile(r"\bFROM\s+([\w.]+)", re.IGNORECASE),
    re.compile(r"\bJOIN\s+([\w.]+)", re.IGNORECASE),
    re.compile(r"\bINTO\s+([\w.]+)", re.IGNORECASE),
    re.compile(r"\bUPDATE\s+([\w.]+)", re.IGNORECASE),
]

COLUMN_PATTERNS = [
    re.compile(r"\bSELECT\s+(.+?)\s+FROM\b", re.IGNORECASE),
    re.compile(r"\bWHERE\s+(.+?)(?:\s+ORDER BY|\s+GROUP BY|\s+LIMIT|\s*;|$)", re.IGNORECASE),
]

def find_sql_files(directory):
    """Suche alle SQL- und DBQ-Dateien im Projektverzeichnis."""
    sql_files = []
    for root, _, files in os.walk(directory):
        for file in files:
            if file.lower().endswith(SQL_FILE_EXTENSIONS):
                sql_files.append(os.path.join(root, file))
    return sql_files

def extract_tables_and_columns(sql_content):
    """Extrahiere Tabellen und Spalten aus einem SQL-Inhalt."""
    tables = set()
    columns = defaultdict(set)

    # Extrahiere Tabellen
    for pattern in TABLE_PATTERNS:
        matches = pattern.findall(sql_content)
        for match in matches:
            table = match.strip("`[]\"")
            tables.add(table)

    # Extrahiere Spalten (vereinfacht)
    for pattern in COLUMN_PATTERNS:
        matches = pattern.findall(sql_content)
        for match in matches:
            # Grobe Extraktion von Spalten (vereinfacht)
            # Hier könnte man eine genauere Extraktion mit SQL-Parser implementieren
            column_matches = re.findall(r"\b([a-zA-Z_][\w.]*\.[a-zA-Z_][\w.]*)\b", match)
            column_matches += re.findall(r"\b([a-zA-Z_][\w.]*)\b", match)
            for column in column_matches:
                if "." in column:
                    table, column = column.split(".", 1)
                    columns[table.strip("`[]\"")].add(column.strip("`[]\""))
                else:
                    columns["unknown"].add(column.strip("`[]\""))

    return tables, columns

def generate_html_report(tables, columns):
    """Generiere eine HTML-Datei mit den extrahierten Tabellen und Spalten."""
    html_content = """
<!DOCTYPE html>
<html lang="de">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>SQL-Tabellen und Felder</title>
    <style>
        body {
            font-family: Arial, sans-serif;
            margin: 20px;
            line-height: 1.6;
        }
        h1 {
            color: #333;
        }
        table {
            width: 100%;
            border-collapse: collapse;
            margin-top: 20px;
        }
        th, td {
            border: 1px solid #ddd;
            padding: 8px;
            text-align: left;
        }
        th {
            background-color: #f2f2f2;
            position: sticky;
            top: 0;
        }
        tr:nth-child(even) {
            background-color: #f9f9f9;
        }
        .table-header {
            background-color: #4CAF50;
            color: white;
        }
        .container {
            max-width: 1400px;
            margin: 0 auto;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>SQL-Tabellen und Felder</h1>
        <p><strong>Projektverzeichnis:</strong> /Users/alexander/Documents/dev/cctest/eval/sql</p>
        <table>
            <thead>
                <tr>
                    <th class="table-header">Tabelle</th>
                    <th class="table-header">Felder</th>
                    <th class="table-header">Häufigkeit in Skripten</th>
                </tr>
            </thead>
            <tbody>
"""

    for table in sorted(tables):
        field_list = ", ".join(sorted(columns.get(table, set())))
        frequency = len(columns.get(table, set()))
        html_content += f"""
            <tr>
                <td>{table}</td>
                <td>{field_list if field_list else 'Keine Felder gefunden'}</td>
                <td>{frequency}</td>
            </tr>
"""

    html_content += """
            </tbody>
        </table>
    </div>
</body>
</html>
"""
    return html_content

def main():
    # Suche alle SQL-Dateien
    sql_files = find_sql_files(PROJECT_DIR)
    if not sql_files:
        print("Keine SQL- oder DBQ-Dateien gefunden.")
        return

    print(f"Gefundene SQL/DBQ-Dateien: {len(sql_files)}")

    # Extrahiere Tabellen und Spalten
    all_tables = set()
    all_columns = defaultdict(set)

    for file_path in sql_files:
        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as file:
                content = file.read()
                tables, columns = extract_tables_and_columns(content)
                all_tables.update(tables)
                for table, cols in columns.items():
                    all_columns[table].update(cols)
        except Exception as e:
            print(f"Fehler beim Lesen von {file_path}: {e}")

    # Generiere HTML-Report
    html_report = generate_html_report(all_tables, all_columns)

    # Speichere die HTML-Datei
    output_path = "sql_tabellen_und_felder.html"
    with open(output_path, "w", encoding="utf-8") as output_file:
        output_file.write(html_report)

    print(f"✅ HTML-Report wurde erfolgreich erstellt: {output_path}")
    print(f"📊 Enthaltene Tabellen: {len(all_tables)}")

if __name__ == "__main__":
    main()
