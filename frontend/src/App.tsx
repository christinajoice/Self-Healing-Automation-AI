import { useState } from "react";
import "./App.css";
import UploadForm from "./components/UploadForm";
import ExecutionStatus from "./components/ExecutionStatus";
import Reports from "./components/Reports";

export default function App() {
  const [executionId, setExecutionId] = useState<string | null>(null);

  return (
    <>
      <header className="app-header">
        <div className="app-header-inner">
          <span className="app-logo">🧠</span>
          <span className="app-title">Self-Healing Automation</span>
          <span className="app-subtitle">AI-powered test execution</span>
          <div className="header-spacer" />
          <span className="app-version">v1.0</span>
        </div>
      </header>

      <main className="app-main">
        <UploadForm onExecutionStart={setExecutionId} />
        {executionId && <ExecutionStatus executionId={executionId} />}
        <Reports />
      </main>
    </>
  );
}
