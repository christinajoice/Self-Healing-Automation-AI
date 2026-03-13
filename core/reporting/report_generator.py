import json
import os
from datetime import datetime
from pathlib import Path

REPORT_DIR = "reports"
os.makedirs(REPORT_DIR, exist_ok=True)


class ReportGenerator:
    def __init__(self, output_dir: str = "reports", default_format: str = "json"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.default_format = default_format

        # 🔑 REQUIRED: store execution results
        self.execution_data = []

    # -----------------------------
    # Data collection
    # -----------------------------
    def log_testcase(self, testcase_result: dict):
        """
        Store each testcase result (used for batch / future parallel runs)
        """
        self.execution_data.append(testcase_result)

    # -----------------------------
    # Public entry point
    # -----------------------------
    def generate(self, results: dict):
        self.log_testcase(results)

        json_file = self.generate_json()
        html_file = self.generate_html()

        return {
            "json": json_file,
            "html": html_file
        }


    # -----------------------------
    # JSON Report
    # -----------------------------
    def generate_json(self):
        filename = (
            self.output_dir
            / f"execution_{datetime.utcnow().strftime('%Y-%m-%d_%H-%M-%S')}.json"
        )

        payload = {
            "generated_at": datetime.utcnow().isoformat(),
            "results": self.execution_data,
        }

        with open(filename, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)

        print(f"📄 JSON report saved: {filename}")
        return filename

    # -----------------------------
    # HTML Report
    # -----------------------------
    def generate_html(self):
        filename = (
            self.output_dir
            / f"execution_{datetime.utcnow().strftime('%Y-%m-%d_%H-%M-%S')}.html"
        )

        html_content = """
        <html>
        <head>
            <title>Test Execution Report</title>
            <style>
                body { font-family: Arial; padding: 20px; }
                .pass { color: green; }
                .fail { color: red; }
                pre { background: #f4f4f4; padding: 10px; }
            </style>
        </head>
        <body>
            <h1>Test Execution Report</h1>
        """

        for testcase in self.execution_data:
            status_class = "pass" if testcase["status"] == "PASS" else "fail"
            html_content += f"""
            <h2 class="{status_class}">
                {testcase["testcase_id"]}: {testcase["status"]}
            </h2>
            """

            if testcase.get("failed_step"):
                html_content += f"""
                <p><b>Failed Step:</b></p>
                <pre>{json.dumps(testcase["failed_step"], indent=2)}</pre>
                <p><b>Error:</b></p>
                <pre>{testcase.get("error")}</pre>
                """

        html_content += "</body></html>"

        with open(filename, "w", encoding="utf-8") as f:
            f.write(html_content)

        print(f"📄 HTML report saved: {filename}")
        return filename
