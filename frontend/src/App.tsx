import { useState } from "react";
import UploadForm from "./components/UploadForm";
import ExecutionStatus from "./components/ExecutionStatus";
import Reports from "./components/Reports";

export default function App() {
  const [executionId, setExecutionId] = useState<string | null>(null);

  return (
    <div style={{ padding: 20 }}>
      <h1>🧠 Self-Healing Automation</h1>

      <UploadForm onExecutionStart={setExecutionId} />

      {executionId && (
        <ExecutionStatus executionId={executionId} />
      )}

      <Reports />
    </div>
  );
}
