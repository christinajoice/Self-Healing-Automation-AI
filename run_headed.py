"""
Headed test runner — runs each test case in its own report card while
sharing a single browser session so login state carries through.
"""
import asyncio
import sys

if sys.platform.startswith("win"):
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from core.execution.executor import TestExecutor
from core.parser.testcase_parser import parse_testcase_file

BASE_URL = "https://networkadequacy-elevance-uat-internal.hilabs.com/"
CSV_FILE = "testspecs/HiLabs_testcase.csv"


async def main():
    testcases = parse_testcase_file(CSV_FILE)
    total_steps = sum(len(tc["steps"]) for tc in testcases)
    print(f"Running {len(testcases)} test case(s) / {total_steps} steps in a single browser session (headed)...\n")

    executor = TestExecutor(headless=False)
    all_results = await executor.run_all_testcases(testcases, BASE_URL)

    for tc_result in all_results:
        print(f"\n===== {tc_result['testcase_id']} — {tc_result['status']} =====")
        for s in tc_result["steps"]:
            status = s["status"].ljust(7)
            healed = " [HEALED]" if s.get("healed") else ""
            err = f"\n           ERROR: {s['error']}" if s.get("error") else ""
            print(f"  Step {s['step']:2d} [{status}] {s['action']} | {s['target']} | {s.get('data') or ''}{healed}{err}")


if __name__ == "__main__":
    asyncio.run(main())
