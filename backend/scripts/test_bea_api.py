"""
Quick test of BEA Input-Output API (Phase 5). Run from backend with BEA_API_KEY in .env or env.
Usage: python scripts/test_bea_api.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), ".env"))

key = os.environ.get("BEA_API_KEY", "").strip()
if not key:
    print("BEA_API_KEY not set in .env")
    sys.exit(1)

import requests

r = requests.get(
    "https://apps.bea.gov/api/data",
    params={
        "UserID": key,
        "method": "GetData",
        "datasetname": "InputOutput",
        "TableID": "259",
        "Year": "2022",  # 2024 may not be available; try 2022
        "ResultFormat": "json",
    },
    timeout=30,
)
print("Status:", r.status_code)
data = r.json()
if r.status_code != 200:
    print("Error:", data)
    sys.exit(1)

bea = data.get("BEAAPI") or {}
results = bea.get("Results")
print("Results type:", type(results).__name__)

if isinstance(results, list) and results:
    rows = (results[0].get("Data") or []) if isinstance(results[0], dict) else []
    print("Rows (list path):", len(rows))
elif isinstance(results, dict):
    rows = results.get("Data") or []
    print("Rows (dict path):", len(rows))
else:
    rows = []
    print("Rows: 0 (no Data found)")

if rows:
    print("First row keys:", list(rows[0].keys()))
    print("First row sample:", {k: (str(v)[:60] + "..." if len(str(v)) > 60 else v) for k, v in list(rows[0].items())[:8]})
    # Count unique row/col descriptors
    row_descrs = set((r.get("RowDescr") or r.get("RowCode") or "") for r in rows)
    col_descrs = set((r.get("ColDescr") or r.get("ColCode") or "") for r in rows)
    print("Unique RowDescr count:", len(row_descrs))
    print("Unique ColDescr count:", len(col_descrs))
else:
    print("No data rows. Full BEAAPI keys:", list(bea.keys()))
    if results:
        print("Results keys:", list(results.keys()) if isinstance(results, dict) else "N/A")
