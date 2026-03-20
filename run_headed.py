"""
Headed test runner — combines all CSV test cases into a single browser session
so login state carries through to subsequent test case steps.
"""
import asyncio
import sys
import pandas as pd

if sys.platform.startswith("win"):
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from core.execution.executor import TestExecutor

BASE_URL = "https://networkadequacy-elevance-uat-internal.hilabs.com/"
CSV_FILE = "testspecs/sample_testcase.csv"


def load_combined_testcase(csv_path: str) -> dict:
    """Read all CSV rows and merge into one testcase (single browser session)."""
    df = pd.read_csv(csv_path).fillna("")
    df["Action"] = df["Action"].str.lower().str.strip()
    df["Target"] = df["Target"].str.lower().str.strip()
    df["Confidence"] = df["Confidence"].str.lower().str.strip() if "Confidence" in df.columns else "high"

    steps = []
    for _, row in df.iterrows():
        steps.append({
            "step": int(row["Step"]),
            "action": row["Action"],
            "target": row["Target"],
            "data": row["Data"] if row["Data"] != "" else None,
            "confidence": row["Confidence"] if row["Confidence"] != "" else "high",
        })

    # Re-number steps sequentially across all test cases
    for i, step in enumerate(steps, start=1):
        step["step"] = i

    return {"testcase_id": "ALL", "steps": steps}


async def main():
    testcase = load_combined_testcase(CSV_FILE)
    print(f"Running {len(testcase['steps'])} steps in a single browser session (headed)...\n")

    executor = TestExecutor(headless=False)
    result = await executor.run_testcase(testcase, BASE_URL)

    print(f"\n===== RESULT: {result['status']} =====")
    for s in result["steps"]:
        status = s["status"].ljust(7)
        healed = " [HEALED]" if s.get("healed") else ""
        err = f"\n           ERROR: {s['error']}" if s.get("error") else ""
        print(f"  Step {s['step']:2d} [{status}] {s['action']} | {s['target']} | {s.get('data') or ''}{healed}{err}")


if __name__ == "__main__":
    asyncio.run(main())
