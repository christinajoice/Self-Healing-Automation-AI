import { useEffect, useState } from "react";
import { fetchExecutionStatus } from "../api/client";
import type { ExecutionStatus as Status, ExecutionStep } from "../types/execution";

interface Props {
  executionId: string;
}

function stateBadge(state: string) {
  const map: Record<string, string> = {
    QUEUED:    "badge-queued",
    RUNNING:   "badge-running",
    COMPLETED: "badge-completed",
    FAILED:    "badge-failed",
    UNKNOWN:   "badge-skipped",
  };
  return map[state] ?? "badge-skipped";
}

function stepRowClass(step: ExecutionStep) {
  if (step.status === "FAIL") return "row-fail";
  if (step.status === "SKIPPED") return "row-skip";
  if (step.healed) return "row-healed";
  return "";
}

function StatusBadge({ status }: { status: string }) {
  const map: Record<string, string> = {
    PASS:    "badge-pass",
    FAIL:    "badge-fail",
    SKIPPED: "badge-skipped",
  };
  return <span className={`badge ${map[status] ?? "badge-skipped"}`}>{status}</span>;
}

function ConfidenceBadge({ level }: { level: string }) {
  const cls = `badge badge-confidence-${level?.toLowerCase()}`;
  return <span className={cls}>{level}</span>;
}

function formatTime(iso: string) {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  } catch {
    return iso;
  }
}

export default function ExecutionStatus({ executionId }: Props) {
  const [status, setStatus] = useState<Status | null>(null);

  useEffect(() => {
    const timer = setInterval(async () => {
      const res = await fetchExecutionStatus(executionId);
      setStatus(res);
      if (res.state === "COMPLETED" || res.state === "FAILED") {
        clearInterval(timer);
      }
    }, 2000);
    return () => clearInterval(timer);
  }, [executionId]);

  if (!status) return null;

  const steps = status.results?.steps ?? [];
  const passed  = steps.filter(s => s.status === "PASS" && !s.healed).length;
  const failed  = steps.filter(s => s.status === "FAIL").length;
  const skipped = steps.filter(s => s.status === "SKIPPED").length;
  const healed  = steps.filter(s => s.healed).length;
  const progress = status.progress ?? 0;

  const progressClass =
    status.state === "COMPLETED" ? "complete" :
    status.state === "FAILED"    ? "failed"   : "";

  return (
    <div className="card">
      {/* Header */}
      <div className="card-header">
        <span className="card-icon">⚡</span>
        <h2>Execution Status</h2>
        <div className="card-header-meta">
          <span
            className={`badge ${stateBadge(status.state)}`}
            style={{ display: "inline-flex", alignItems: "center", gap: 5 }}
          >
            {status.state === "RUNNING" && <span className="pulse-dot" />}
            {status.state}
          </span>
        </div>
      </div>

      {/* Progress */}
      <div className="progress-section">
        <div className="progress-label">
          <span>{status.message}</span>
          <span>{progress}%</span>
        </div>
        <div className="progress-track">
          <div
            className={`progress-fill ${progressClass}`}
            style={{ width: `${progress}%` }}
          />
        </div>
      </div>

      {/* Error */}
      {status.error && (
        <div className="error-block">{status.error}</div>
      )}

      {/* Results */}
      {status.results && (
        <>
          {/* Meta row */}
          <div className="execution-meta">
            <div className="meta-item">
              <span className="meta-label">Test Case</span>
              <span className="meta-value" style={{ fontFamily: "monospace" }}>
                {status.results.testcase_id}
              </span>
            </div>
            <div className="meta-item">
              <span className="meta-label">Result</span>
              <span className="meta-value">
                <StatusBadge status={status.results.status} />
              </span>
            </div>
            <div className="meta-item">
              <span className="meta-label">Started</span>
              <span className="meta-value">{formatTime(status.results.start_time)}</span>
            </div>
            <div className="meta-item">
              <span className="meta-label">Ended</span>
              <span className="meta-value">{formatTime(status.results.end_time)}</span>
            </div>
          </div>

          {/* Stats */}
          <div style={{ padding: "14px 20px", borderBottom: "1px solid var(--border)" }}>
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
            </div>
          </div>

          {/* Steps table */}
          {steps.length > 0 && (
            <div className="table-container">
              <table>
                <thead>
                  <tr>
                    <th>#</th>
                    <th>Action</th>
                    <th>Target</th>
                    <th>Data</th>
                    <th>Confidence</th>
                    <th>Status</th>
                    <th>Healed</th>
                    <th>Error</th>
                    <th>Time</th>
                  </tr>
                </thead>
                <tbody>
                  {steps.map((step) => (
                    <tr key={step.step} className={stepRowClass(step)}>
                      <td style={{ color: "var(--text-muted)", fontWeight: 600 }}>{step.step}</td>
                      <td style={{ fontWeight: 500 }}>{step.action}</td>
                      <td className="cell-muted" style={{ maxWidth: 180, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                        {step.target || "—"}
                      </td>
                      <td className="cell-muted">{step.data || "—"}</td>
                      <td><ConfidenceBadge level={step.confidence} /></td>
                      <td><StatusBadge status={step.status} /></td>
                      <td style={{ textAlign: "center" }}>
                        {step.healed
                          ? <span className="badge badge-healed">healed</span>
                          : <span className="cell-muted">—</span>
                        }
                      </td>
                      <td className="cell-muted" style={{ maxWidth: 220, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                        {step.error || "—"}
                      </td>
                      <td className="cell-muted">{formatTime(step.timestamp)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </div>
  );
}
