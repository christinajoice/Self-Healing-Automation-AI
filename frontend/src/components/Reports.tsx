import { useEffect, useState } from "react";
import { fetchReports } from "../api/client";

interface ReportStep {
  step: number;
  action: string;
  target?: string;
  status: string;
  confidence: string;
  healed: boolean;
  error?: string;
}

interface Report {
  testcase_id: string;
  status: string;
  start_time?: string;
  end_time?: string;
  steps?: ReportStep[];
  error?: string;
}

function formatDateTime(iso?: string) {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString([], {
      month: "short", day: "numeric",
      hour: "2-digit", minute: "2-digit", second: "2-digit",
    });
  } catch {
    return iso;
  }
}

function ReportCard({ report, index }: { report: Report; index: number }) {
  const [open, setOpen] = useState(index === 0);

  const steps   = report.steps ?? [];
  const passed  = steps.filter(s => s.status === "PASS" && !s.healed).length;
  const failed  = steps.filter(s => s.status === "FAIL").length;
  const skipped = steps.filter(s => s.status === "SKIPPED").length;
  const healed  = steps.filter(s => s.healed).length;

  const statusClass = report.status === "PASS" ? "badge-pass" :
                      report.status === "FAIL" ? "badge-fail" : "badge-skipped";

  return (
    <div className="report-item">
      <div className="report-item-header" onClick={() => setOpen(o => !o)}>
        <span className={`report-chevron ${open ? "open" : ""}`}>▶</span>
        <span className="report-item-title">{report.testcase_id}</span>
        <div className="report-item-meta">
          <span className={`badge ${statusClass}`}>{report.status}</span>
          <span>{steps.length} steps</span>
          <span>{formatDateTime(report.start_time)}</span>
        </div>
      </div>

      {open && (
        <div className="report-item-body">
          {/* Stats row */}
          <div className="report-stats">
            <div className="stat-grid">
              <div className="stat-item">
                <span className="stat-value">{steps.length}</span>
                <span className="stat-label">Total</span>
              </div>
              <div className="stat-item">
                <span className="stat-value" style={{ color: "var(--success)" }}>{passed}</span>
                <span className="stat-label">Passed</span>
              </div>
              <div className="stat-item">
                <span className="stat-value" style={{ color: "var(--danger)" }}>{failed}</span>
                <span className="stat-label">Failed</span>
              </div>
              <div className="stat-item">
                <span className="stat-value" style={{ color: "var(--skipped)" }}>{skipped}</span>
                <span className="stat-label">Skipped</span>
              </div>
              <div className="stat-item">
                <span className="stat-value" style={{ color: "var(--healed)" }}>{healed}</span>
                <span className="stat-label">Healed</span>
              </div>
              <div className="stat-item" style={{ gridColumn: "span 2" }}>
                <span className="stat-value" style={{ fontSize: "0.875rem" }}>
                  {formatDateTime(report.end_time)}
                </span>
                <span className="stat-label">Completed at</span>
              </div>
            </div>
          </div>

          {report.error && (
            <div className="error-block" style={{ margin: "12px 16px" }}>{report.error}</div>
          )}

          {/* Steps table */}
          {steps.length > 0 && (
            <div className="table-container">
              <table>
                <thead>
                  <tr>
                    <th>#</th>
                    <th>Action</th>
                    <th>Target</th>
                    <th>Confidence</th>
                    <th>Status</th>
                    <th>Healed</th>
                    <th>Error</th>
                  </tr>
                </thead>
                <tbody>
                  {steps.map((step) => {
                    const rowClass =
                      step.status === "FAIL"    ? "row-fail"   :
                      step.status === "SKIPPED" ? "row-skip"   :
                      step.healed               ? "row-healed" : "";
                    const statusBadge =
                      step.status === "PASS"    ? "badge-pass"    :
                      step.status === "FAIL"    ? "badge-fail"    : "badge-skipped";
                    return (
                      <tr key={step.step} className={rowClass}>
                        <td style={{ color: "var(--text-muted)", fontWeight: 600 }}>{step.step}</td>
                        <td style={{ fontWeight: 500 }}>{step.action}</td>
                        <td className="cell-muted" style={{ maxWidth: 160, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                          {step.target || "—"}
                        </td>
                        <td>
                          <span className={`badge badge-confidence-${step.confidence?.toLowerCase()}`}>
                            {step.confidence}
                          </span>
                        </td>
                        <td><span className={`badge ${statusBadge}`}>{step.status}</span></td>
                        <td style={{ textAlign: "center" }}>
                          {step.healed
                            ? <span className="badge badge-healed">healed</span>
                            : <span className="cell-muted">—</span>
                          }
                        </td>
                        <td className="cell-muted" style={{ maxWidth: 200, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                          {step.error || "—"}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default function Reports() {
  const [reports, setReports] = useState<Report[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchReports()
      .then(res => setReports(res.reports || []))
      .finally(() => setLoading(false));
  }, []);

  return (
    <div className="card">
      <div className="card-header">
        <span className="card-icon">📊</span>
        <h2>Execution Reports</h2>
        <div className="card-header-meta">
          {!loading && <span>{reports.length} report{reports.length !== 1 ? "s" : ""}</span>}
        </div>
      </div>

      <div className="card-body">
        {loading && (
          <div style={{ color: "var(--text-muted)", fontSize: "0.875rem" }}>
            Loading reports…
          </div>
        )}

        {!loading && reports.length === 0 && (
          <div className="empty-state">
            <div className="empty-state-icon">📭</div>
            No reports yet. Run a test case to see results here.
          </div>
        )}

        {!loading && reports.length > 0 && (
          <div>
            {reports.map((r, i) => (
              <ReportCard key={i} report={r} index={i} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
