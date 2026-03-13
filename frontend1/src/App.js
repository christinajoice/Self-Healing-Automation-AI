import React, { useState, useEffect } from "react";
import axios from "axios";

const API_BASE = "http://localhost:8000";

/* ======================
   UI Helpers
====================== */
const Badge = ({ type }) => {
  const normalized = type?.toUpperCase();

  const colors = {
    PASS: "#16a34a",
    FAIL: "#dc2626",
    HEALED: "#f59e0b",
    RUNNING: "#2563eb",
    COMPLETED: "#16a34a",
    FAILED: "#dc2626",
    STARTED: "#6b7280"
  };

  return (
    <span
      style={{
        backgroundColor: colors[normalized] || "#6b7280",
        color: "white",
        padding: "4px 8px",
        borderRadius: "6px",
        fontSize: "12px",
        fontWeight: "bold",
        marginRight: "8px"
      }}
    >
      {normalized}
    </span>
  );
};

const maskIfPassword = (action, target, value) => {
  if (action === "enter" && target?.toLowerCase().includes("password")) {
    return "••••••••";
  }
  return value || "-";
};

/* ======================
   Main App
====================== */
function App() {
  const [file, setFile] = useState(null);
  const [baseUrl, setBaseUrl] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");

  const [executionId, setExecutionId] = useState(null);
  const [status, setStatus] = useState(null);
  const [results, setResults] = useState(null);
  const [loading, setLoading] = useState(false);

  /* 🔁 Poll execution status */
  useEffect(() => {
    if (!executionId) return;

    const interval = setInterval(async () => {
      try {
        const res = await axios.get(
          `${API_BASE}/execution_status/${executionId}`
        );

        setStatus(res.data);

        const state = res.data.state?.toLowerCase();

        if (state === "completed" || state === "failed") {
          clearInterval(interval);
          setLoading(false);

          if (res.data.results) {
            setResults(res.data.results);
          }
        }
      } catch (err) {
        console.error("Status polling failed", err);
      }
    }, 2000); // ✅ true 2-second polling

    return () => clearInterval(interval);
  }, [executionId]);

  /* 🚀 Submit testcase */
  const handleSubmit = async (e) => {
    e.preventDefault();

    if (!file || !baseUrl) {
      alert("Please upload a CSV and enter Base URL");
      return;
    }

    const formData = new FormData();
    formData.append("file", file);
    formData.append("base_url", baseUrl);
    if (username) formData.append("username", username);
    if (password) formData.append("password", password);

    try {
      setLoading(true);
      setResults(null);
      setStatus(null);

      const res = await axios.post(
        `${API_BASE}/upload_testcase`,
        formData,
        { headers: { "Content-Type": "multipart/form-data" } }
      );

      setExecutionId(res.data.execution_id);
    } catch (err) {
      setLoading(false);
      alert("Failed to start execution");
      console.error(err);
    }
  };

  return (
    <div style={{ padding: "2rem", maxWidth: "900px", margin: "auto" }}>
      <h1>🧪 AI Test Automation Platform</h1>

      <form onSubmit={handleSubmit}>
        <div>
          <label>Test Case (CSV)</label><br />
          <input
            type="file"
            accept=".csv"
            onChange={(e) => setFile(e.target.files[0])}
          />
        </div>

        <div>
          <label>Base URL</label><br />
          <input
            type="text"
            value={baseUrl}
            onChange={(e) => setBaseUrl(e.target.value)}
            placeholder="https://example.com"
          />
        </div>

        <div>
          <label>Username (optional)</label><br />
          <input
            type="text"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
          />
        </div>

        <div>
          <label>Password (optional)</label><br />
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </div>

        <button type="submit" disabled={loading}>
          {loading ? "Running..." : "Run Test"}
        </button>
      </form>

      {/* 🔄 Live Status */}
      {status && (
        <div style={{ marginTop: "2rem" }}>
          <h3>Execution Status</h3>
          <Badge type={status.state} />
          <p>{status.message}</p>

          {status.progress !== null && (
            <progress
              value={status.progress}
              max="100"
              style={{ width: "100%" }}
            />
          )}

          {status.error && (
            <p style={{ color: "red" }}>
              <strong>Error:</strong> {status.error}
            </p>
          )}
        </div>
      )}

      {/* ✅ Beautiful Final Results */}
      {results && (
        <div style={{ marginTop: "2rem" }}>
          <h2>Execution Summary</h2>

          <div style={{ display: "flex", gap: "1rem", marginBottom: "1rem" }}>
            <Badge type={results.status} />
            <div><strong>Testcase:</strong> {results.testcase_id}</div>
            <div>
              <strong>Duration:</strong>{" "}
              {new Date(results.end_time) - new Date(results.start_time)} ms
            </div>
          </div>

          <table
            border="1"
            cellPadding="8"
            cellSpacing="0"
            width="100%"
            style={{ borderCollapse: "collapse" }}
          >
            <thead style={{ backgroundColor: "#f3f4f6" }}>
              <tr>
                <th>#</th>
                <th>Action</th>
                <th>Target</th>
                <th>Data</th>
                <th>Status</th>
                <th>Healed</th>
                <th>Error</th>
              </tr>
            </thead>

            <tbody>
              {results.steps.map((step) => (
                <tr key={step.step}>
                  <td>{step.step}</td>
                  <td>{step.action}</td>
                  <td>{step.target}</td>
                  <td>
                    {maskIfPassword(step.action, step.target, step.data)}
                  </td>
                  <td><Badge type={step.status} /></td>
                  <td>{step.healed ? <Badge type="HEALED" /> : "-"}</td>
                  <td style={{ color: "red", fontSize: "12px" }}>
                    {step.error || "-"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

export default App;
