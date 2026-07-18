"""
MCP server exposing a DuckDB instance loaded from the Instacart dataset.

Tools exposed to the calling LLM:
  - list_tables()          -> names + row counts of loaded tables
  - describe_table(name)   -> column names and types for one table
  - run_query(sql)         -> executes a SELECT-only query, returns rows as JSON

Design decision: run_query rejects anything that isn't a single SELECT
statement (no INSERT/UPDATE/DELETE/DROP/ATTACH/etc). This is a second,
independent safety layer underneath the human-approval gate enforced by
the OpenAI Responses API (require_approval="always"). Two failure modes
have to align for something bad to happen: the model has to propose a
destructive query AND a human has to approve it AND the query has to pass
the SELECT-only filter. That third layer stays even if approval is ever
relaxed to "never" for faster iteration during dev.

Run:
    python duckdb_server.py

This starts an HTTP server on 0.0.0.0:8000. For local testing with the
OpenAI Responses API, expose it publicly with a tunnel, e.g.:
    ngrok http 8000
and point the Streamlit app's MCP_SERVER_URL at the ngrok https URL + "/mcp".
"""

import glob
import os
import re

import duckdb
from fastmcp import FastMCP

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")

mcp = FastMCP("instacart-duckdb")

# One shared in-memory DuckDB connection for the life of the process.
con = duckdb.connect(database=":memory:")


def _load_data():
    """Load every CSV in data/ as a table named after the file."""
    loaded = []
    for path in glob.glob(os.path.join(DATA_DIR, "*.csv")):
        table_name = os.path.splitext(os.path.basename(path))[0]
        # sanitize table name: alnum + underscore only
        table_name = re.sub(r"[^a-zA-Z0-9_]", "_", table_name)
        con.execute(
            f"CREATE OR REPLACE TABLE {table_name} AS "
            f"SELECT * FROM read_csv_auto(?, sample_size=-1)",
            [path],
        )
        loaded.append(table_name)
    return loaded


_LOADED_TABLES = _load_data()


_SELECT_ONLY = re.compile(r"^\s*(with\b.*)?select\b", re.IGNORECASE | re.DOTALL)
_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|attach|detach|copy|create|pragma|call|export|import)\b",
    re.IGNORECASE,
)


@mcp.tool()
def list_tables() -> dict:
    """List all tables currently loaded in the database, with row counts."""
    if not _LOADED_TABLES:
        return {"tables": [], "note": "No CSVs found in data/. Add files and restart."}
    result = []
    for t in _LOADED_TABLES:
        count = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        result.append({"table": t, "row_count": count})
    return {"tables": result}


@mcp.tool()
def describe_table(table_name: str) -> dict:
    """Return column names and types for a given table."""
    if table_name not in _LOADED_TABLES:
        return {"error": f"Unknown table '{table_name}'. Known tables: {_LOADED_TABLES}"}
    rows = con.execute(f"DESCRIBE {table_name}").fetchall()
    columns = [{"name": r[0], "type": r[1]} for r in rows]
    return {"table": table_name, "columns": columns}


@mcp.tool()
def run_query(sql: str, row_limit: int = 500) -> dict:
    """
    Execute a read-only SQL query against the loaded tables and return the
    result as rows of JSON. Only SELECT (optionally with a WITH clause) is
    permitted; the query is rejected before execution otherwise.
    """
    if not _SELECT_ONLY.match(sql):
        return {"error": "Only SELECT statements are permitted."}
    if _FORBIDDEN.search(sql):
        return {"error": "Query contains a disallowed keyword (DDL/DML)."}

    try:
        cursor = con.execute(sql)
        columns = [d[0] for d in cursor.description]
        rows = cursor.fetchmany(row_limit)
        return {
            "columns": columns,
            "rows": [dict(zip(columns, r)) for r in rows],
            "truncated": len(rows) == row_limit,
        }
    except Exception as e:
        return {"error": str(e)}


if __name__ == "__main__":
    print(f"Loaded tables: {_LOADED_TABLES}")
    mcp.run(transport="http", host="0.0.0.0", port=8000, path="/mcp")
