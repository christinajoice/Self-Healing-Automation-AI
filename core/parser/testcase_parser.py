import pandas as pd
from typing import Dict, List

REQUIRED_COLUMNS = {"TestCaseID", "Step", "Action", "Target", "Data"}


def parse_testcase_file(file_path: str) -> List[Dict]:
    # Load file
    if file_path.endswith(".csv"):
        df = pd.read_csv(file_path)
    elif file_path.endswith((".xls", ".xlsx")):
        df = pd.read_excel(file_path)
    else:
        raise ValueError("Unsupported file format. Use CSV or Excel")

    # Validate columns
    missing = REQUIRED_COLUMNS - set(df.columns)
    if missing:
        raise ValueError(f"Missing required columns: {missing}")

    # Normalize data
    df = df.fillna("")
    df["Action"] = df["Action"].str.lower().str.strip()
    # Keep Target casing as-is — it is used as the cache key in locators.json.
    # Lowercasing would cause cache misses on every run.
    df["Target"] = df["Target"].str.strip()

    testcases = {}

    # Group by TestCaseID
    for _, row in df.iterrows():
        tc_id = row["TestCaseID"]

        step_data = {
            "step": int(row["Step"]),
            "action": row["Action"],
            "target": row["Target"],
            "data": row["Data"] if row["Data"] != "" else None,
            "confidence": str(row.get("Confidence", "high") or "high").strip().lower() or "high",
        }

        testcases.setdefault(tc_id, []).append(step_data)

    # Sort steps and return structured list
    return [
        {
            "testcase_id": tc_id,
            "steps": sorted(steps, key=lambda x: x["step"]),
        }
        for tc_id, steps in testcases.items()
    ]
