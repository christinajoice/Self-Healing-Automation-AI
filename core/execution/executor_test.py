from core.execution.executor import TestExecutor
from core.parser.testcase_parser import parse_testcase_file


# Sample base URL (replace with real app later)
BASE_URL = "https://the-internet.herokuapp.com/login"


testcases = parse_testcase_file("testspecs/sample_testcase.csv")
executor = TestExecutor(headless=False)


for tc in testcases:
    result = executor.run_testcase(tc, BASE_URL)
    print(result)