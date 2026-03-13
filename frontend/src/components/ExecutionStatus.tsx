import { useEffect, useState } from "react";
import { fetchExecutionStatus } from "../api/client";
import type { ExecutionStatus as Status } from "../types/execution";

interface Props {
  executionId: string;
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

  return (
    <div className="card">
      <h3>Status: {status.state}</h3>
      <p>{status.message}</p>

      {status.progress !== undefined && (
        <progress value={status.progress} max={100} />
      )}

      {status.error && <pre style={{ color: "red" }}>{status.error}</pre>}

      {status.results && (
        <div>
          <h4>Testcase ID: {status.results.testcase_id}</h4>
          <p>Status: {status.results.status}</p>
          <p>Started: {status.results.start_time}</p>
          <p>Ended: {status.results.end_time}</p>

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
                <th>Timestamp</th>
              </tr>
            </thead>
            <tbody>
              {status.results.steps.map((step) => (
                <tr key={step.step}>
                  <td>{step.step}</td>
                  <td>{step.action}</td>
                  <td>{step.target || "-"}</td>
                  <td>{step.data || "-"}</td>
                  <td>{step.confidence}</td>
                  <td>{step.status}</td>
                  <td>{step.healed ? "✅" : "❌"}</td>
                  <td>{step.error || "-"}</td>
                  <td>{step.timestamp}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
