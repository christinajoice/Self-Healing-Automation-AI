import json
import os
import html as html_escape_lib
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

        print(f"[REPORT] JSON report saved: {filename}")
        return filename

    # ------------------------------------------------------------------
    # HTML Report
    # ------------------------------------------------------------------
    def generate_html(self):
        filename = (
            self.output_dir
            / f"execution_{datetime.utcnow().strftime('%Y-%m-%d_%H-%M-%S')}.html"
        )

        generated_at = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

        # ── Global summary counts ──────────────────────────────────────
        total   = len(self.execution_data)
        passed  = sum(1 for t in self.execution_data if t.get("status") == "PASS")
        failed  = total - passed

        all_steps   = [s for t in self.execution_data for s in t.get("steps", [])]
        steps_pass  = sum(1 for s in all_steps if s.get("status") == "PASS" and not s.get("healed"))
        steps_fail  = sum(1 for s in all_steps if s.get("status") == "FAIL")
        steps_skip  = sum(1 for s in all_steps if s.get("status") == "SKIPPED")
        steps_healed= sum(1 for s in all_steps if s.get("healed"))

        # ── Build per-test-case blocks ─────────────────────────────────
        tc_blocks = ""
        for tc in self.execution_data:
            tc_id     = html_escape_lib.escape(str(tc.get("testcase_id", "—")))
            tc_status = tc.get("status", "UNKNOWN")
            tc_start  = tc.get("start_time", "")
            tc_end    = tc.get("end_time", "")
            tc_error  = tc.get("error", "")
            steps     = tc.get("steps", [])

            badge_cls = (
                "badge-pass"    if tc_status == "PASS" else
                "badge-fail"    if tc_status == "FAIL" else
                "badge-skipped"
            )

            s_pass   = sum(1 for s in steps if s.get("status") == "PASS" and not s.get("healed"))
            s_fail   = sum(1 for s in steps if s.get("status") == "FAIL")
            s_skip   = sum(1 for s in steps if s.get("status") == "SKIPPED")
            s_healed = sum(1 for s in steps if s.get("healed"))

            # Steps table rows
            step_rows = ""
            for s in steps:
                s_status   = s.get("status", "")
                s_conf     = s.get("confidence", "")
                s_healed_b = s.get("healed", False)
                s_error    = html_escape_lib.escape(str(s.get("error") or ""))
                s_action   = html_escape_lib.escape(str(s.get("action") or ""))
                s_target   = html_escape_lib.escape(str(s.get("target") or "—"))
                s_data     = html_escape_lib.escape(str(s.get("data") or "—"))
                s_ts       = str(s.get("timestamp", ""))[:19].replace("T", " ")

                row_cls = (
                    "row-fail"   if s_status == "FAIL"    else
                    "row-skip"   if s_status == "SKIPPED" else
                    "row-healed" if s_healed_b            else ""
                )
                st_badge = (
                    "badge-pass"    if s_status == "PASS"    else
                    "badge-fail"    if s_status == "FAIL"    else
                    "badge-skipped"
                )
                cf_badge = f"badge-conf-{s_conf.lower()}" if s_conf else ""
                healed_cell = (
                    '<span class="badge badge-healed">healed</span>'
                    if s_healed_b else '<span class="muted">—</span>'
                )
                error_cell = (
                    f'<span class="error-text" title="{s_error}">'
                    f'{s_error[:60]}{"…" if len(s_error) > 60 else ""}</span>'
                    if s_error else '<span class="muted">—</span>'
                )

                step_rows += f"""
                <tr class="{row_cls}">
                  <td class="col-num">{s.get("step","")}</td>
                  <td class="col-action">{s_action}</td>
                  <td class="col-target muted">{s_target}</td>
                  <td class="col-data muted">{s_data}</td>
                  <td><span class="badge {cf_badge}">{s_conf}</span></td>
                  <td><span class="badge {st_badge}">{s_status}</span></td>
                  <td class="col-center">{healed_cell}</td>
                  <td>{error_cell}</td>
                  <td class="muted col-ts">{s_ts}</td>
                </tr>"""

            error_block = (
                f'<div class="error-block">{html_escape_lib.escape(str(tc_error))}</div>'
                if tc_error else ""
            )

            tc_blocks += f"""
            <details class="tc-card" {"open" if tc_status == "FAIL" else ""}>
              <summary class="tc-summary">
                <span class="tc-chevron">▶</span>
                <span class="tc-id">{tc_id}</span>
                <span class="badge {badge_cls} tc-badge">{tc_status}</span>
                <span class="tc-meta">{len(steps)} steps &nbsp;·&nbsp; {tc_start[:19].replace("T"," ")}</span>
              </summary>

              <div class="tc-body">
                <!-- Mini stats -->
                <div class="mini-stats">
                  <div class="ms-item"><span class="ms-val">{len(steps)}</span><span class="ms-lbl">Total</span></div>
                  <div class="ms-item ms-pass"><span class="ms-val">{s_pass}</span><span class="ms-lbl">Passed</span></div>
                  <div class="ms-item ms-fail"><span class="ms-val">{s_fail}</span><span class="ms-lbl">Failed</span></div>
                  <div class="ms-item ms-skip"><span class="ms-val">{s_skip}</span><span class="ms-lbl">Skipped</span></div>
                  <div class="ms-item ms-healed"><span class="ms-val">{s_healed}</span><span class="ms-lbl">Healed</span></div>
                  <div class="ms-item" style="grid-column:span 2">
                    <span class="ms-val" style="font-size:.9rem">{tc_end[:19].replace("T"," ")}</span>
                    <span class="ms-lbl">Completed</span>
                  </div>
                </div>

                {error_block}

                {"" if not steps else f'''
                <div class="table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th>#</th><th>Action</th><th>Target</th><th>Data</th>
                        <th>Confidence</th><th>Status</th><th>Healed</th>
                        <th>Error</th><th>Timestamp</th>
                      </tr>
                    </thead>
                    <tbody>{step_rows}</tbody>
                  </table>
                </div>'''}
              </div>
            </details>"""

        # ── Assemble full HTML ─────────────────────────────────────────
        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Test Execution Report</title>
<style>
  /* ── Reset & base ───────────────────────────────── */
  *, *::before, *::after {{ box-sizing: border-box; }}
  body {{
    margin: 0;
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto,
                 Helvetica, Arial, sans-serif;
    font-size: 14px;
    line-height: 1.5;
    background: #0d1117;
    color: #e6edf3;
    -webkit-font-smoothing: antialiased;
  }}
  h1, h2, h3 {{ margin: 0; font-weight: 700; }}
  a {{ color: #58a6ff; text-decoration: none; }}

  /* ── Layout ─────────────────────────────────────── */
  .page-header {{
    background: #161b22;
    border-bottom: 1px solid #30363d;
    padding: 0 32px;
    height: 56px;
    display: flex;
    align-items: center;
    gap: 12px;
    position: sticky;
    top: 0;
    z-index: 10;
  }}
  .page-header-logo {{ font-size: 1.4rem; }}
  .page-header-title {{ font-size: 1rem; font-weight: 700; color: #e6edf3; }}
  .page-header-sub {{
    font-size: 0.75rem;
    color: #6e7681;
    margin-left: 4px;
  }}
  .header-right {{
    margin-left: auto;
    font-size: 0.75rem;
    color: #6e7681;
    background: #1c2128;
    border: 1px solid #30363d;
    padding: 3px 10px;
    border-radius: 20px;
  }}
  .main {{
    max-width: 1100px;
    margin: 0 auto;
    padding: 32px 28px 64px;
  }}

  /* ── Summary card ───────────────────────────────── */
  .summary-card {{
    background: #161b22;
    border: 1px solid #30363d;
    border-radius: 12px;
    overflow: hidden;
    margin-bottom: 24px;
  }}
  .summary-header {{
    padding: 14px 20px;
    border-bottom: 1px solid #30363d;
    display: flex;
    align-items: center;
    gap: 8px;
  }}
  .summary-header h2 {{ font-size: 0.9375rem; color: #e6edf3; }}
  .summary-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
    gap: 1px;
    background: #30363d;
  }}
  .sg-item {{
    background: #1c2128;
    padding: 16px 20px;
    display: flex;
    flex-direction: column;
    gap: 3px;
  }}
  .sg-val {{
    font-size: 1.75rem;
    font-weight: 700;
    line-height: 1;
    color: #e6edf3;
  }}
  .sg-lbl {{
    font-size: 0.6875rem;
    text-transform: uppercase;
    letter-spacing: 0.07em;
    color: #6e7681;
    font-weight: 600;
  }}
  .sg-pass {{ color: #3fb950; }}
  .sg-fail {{ color: #f85149; }}
  .sg-skip {{ color: #8b949e; }}
  .sg-healed {{ color: #a371f7; }}

  /* ── Badges ─────────────────────────────────────── */
  .badge {{
    display: inline-flex;
    align-items: center;
    font-size: 0.6875rem;
    font-weight: 700;
    letter-spacing: 0.05em;
    text-transform: uppercase;
    padding: 2px 8px;
    border-radius: 20px;
    border: 1px solid transparent;
    white-space: nowrap;
  }}
  .badge-pass    {{ background: rgba(63,185,80,.15);   color: #3fb950; border-color: rgba(63,185,80,.3); }}
  .badge-fail    {{ background: rgba(248,81,73,.15);   color: #f85149; border-color: rgba(248,81,73,.3); }}
  .badge-skipped {{ background: rgba(139,148,158,.15); color: #8b949e; border-color: rgba(139,148,158,.3); }}
  .badge-healed  {{ background: rgba(163,113,247,.15); color: #a371f7; border-color: rgba(163,113,247,.3); }}
  .badge-conf-high   {{ background: rgba(63,185,80,.12);   color: #3fb950; }}
  .badge-conf-medium {{ background: rgba(210,153,34,.12);  color: #d29922; }}
  .badge-conf-low    {{ background: rgba(139,148,158,.12); color: #8b949e; }}

  /* ── Test-case card ─────────────────────────────── */
  .tc-card {{
    background: #161b22;
    border: 1px solid #30363d;
    border-radius: 10px;
    overflow: hidden;
    margin-bottom: 12px;
  }}
  .tc-card:last-child {{ margin-bottom: 0; }}
  .tc-summary {{
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 13px 18px;
    cursor: pointer;
    user-select: none;
    list-style: none;
    background: #1c2128;
    transition: background .15s;
  }}
  .tc-summary::-webkit-details-marker {{ display: none; }}
  .tc-summary:hover {{ background: #21262d; }}
  details[open] > .tc-summary {{ background: #21262d; border-bottom: 1px solid #30363d; }}
  .tc-chevron {{
    font-size: 0.65rem;
    color: #6e7681;
    transition: transform .2s;
  }}
  details[open] > .tc-summary .tc-chevron {{ transform: rotate(90deg); }}
  .tc-id {{
    font-size: 0.9375rem;
    font-weight: 700;
    color: #e6edf3;
    font-family: 'SFMono-Regular', Consolas, monospace;
  }}
  .tc-badge {{ margin-left: 2px; }}
  .tc-meta {{
    margin-left: auto;
    font-size: 0.75rem;
    color: #6e7681;
  }}

  .tc-body {{ }}

  /* ── Mini stats ─────────────────────────────────── */
  .mini-stats {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(90px, 1fr));
    gap: 1px;
    background: #30363d;
    border-bottom: 1px solid #30363d;
  }}
  .ms-item {{
    background: #161b22;
    padding: 10px 14px;
    display: flex;
    flex-direction: column;
    gap: 1px;
  }}
  .ms-val  {{ font-size: 1.1rem; font-weight: 700; color: #e6edf3; }}
  .ms-lbl  {{ font-size: 0.6rem; text-transform: uppercase; letter-spacing: .07em; color: #6e7681; font-weight: 600; }}
  .ms-pass .ms-val {{ color: #3fb950; }}
  .ms-fail .ms-val {{ color: #f85149; }}
  .ms-skip .ms-val {{ color: #8b949e; }}
  .ms-healed .ms-val {{ color: #a371f7; }}

  /* ── Error block ────────────────────────────────── */
  .error-block {{
    margin: 12px 16px;
    padding: 10px 14px;
    background: rgba(248,81,73,.1);
    border: 1px solid rgba(248,81,73,.3);
    border-radius: 6px;
    color: #f85149;
    font-size: 0.8125rem;
    font-family: 'SFMono-Regular', Consolas, monospace;
    word-break: break-all;
  }}

  /* ── Table ──────────────────────────────────────── */
  .table-wrap {{ overflow-x: auto; }}
  table {{
    border-collapse: collapse;
    width: 100%;
    font-size: 0.8rem;
  }}
  th {{
    background: #1c2128;
    color: #6e7681;
    font-weight: 700;
    font-size: 0.6875rem;
    text-transform: uppercase;
    letter-spacing: 0.07em;
    padding: 9px 13px;
    text-align: left;
    border-bottom: 1px solid #30363d;
    white-space: nowrap;
  }}
  td {{
    padding: 8px 13px;
    border-bottom: 1px solid #21262d;
    color: #e6edf3;
    vertical-align: middle;
  }}
  tr:last-child td {{ border-bottom: none; }}
  tr.row-fail   {{ background: rgba(248,81,73,.04); }}
  tr.row-skip   {{ background: rgba(139,148,158,.04); }}
  tr.row-healed {{ background: rgba(163,113,247,.04); }}
  .col-num    {{ color: #6e7681; font-weight: 600; width: 36px; }}
  .col-action {{ font-weight: 500; }}
  .col-ts     {{ font-size: 0.75rem; white-space: nowrap; }}
  .col-center {{ text-align: center; }}
  .muted      {{ color: #6e7681; }}
  .error-text {{ color: #f85149; font-size: 0.75rem; }}

  /* ── Print ──────────────────────────────────────── */
  @media print {{
    .page-header {{ position: static; }}
    .tc-card {{ break-inside: avoid; }}
    details {{ open: true; }}
  }}
</style>
</head>
<body>

<header class="page-header">
  <span class="page-header-logo">🧠</span>
  <span class="page-header-title">Self-Healing Automation</span>
  <span class="page-header-sub">Execution Report</span>
  <span class="header-right">Generated: {generated_at}</span>
</header>

<main class="main">

  <!-- Summary -->
  <div class="summary-card">
    <div class="summary-header">
      <span>📊</span>
      <h2>Run Summary</h2>
    </div>
    <div class="summary-grid">
      <div class="sg-item"><span class="sg-val">{total}</span><span class="sg-lbl">Test Cases</span></div>
      <div class="sg-item"><span class="sg-val sg-pass">{passed}</span><span class="sg-lbl">Passed</span></div>
      <div class="sg-item"><span class="sg-val sg-fail">{failed}</span><span class="sg-lbl">Failed</span></div>
      <div class="sg-item"><span class="sg-val">{len(all_steps)}</span><span class="sg-lbl">Total Steps</span></div>
      <div class="sg-item"><span class="sg-val sg-pass">{steps_pass}</span><span class="sg-lbl">Steps Passed</span></div>
      <div class="sg-item"><span class="sg-val sg-fail">{steps_fail}</span><span class="sg-lbl">Steps Failed</span></div>
      <div class="sg-item"><span class="sg-val sg-skip">{steps_skip}</span><span class="sg-lbl">Steps Skipped</span></div>
      <div class="sg-item"><span class="sg-val sg-healed">{steps_healed}</span><span class="sg-lbl">Self-Healed</span></div>
    </div>
  </div>

  <!-- Per-test-case cards -->
  {tc_blocks}

</main>
</body>
</html>"""

        with open(filename, "w", encoding="utf-8") as f:
            f.write(html)

        print(f"[REPORT] HTML report saved: {filename}")
        return filename
