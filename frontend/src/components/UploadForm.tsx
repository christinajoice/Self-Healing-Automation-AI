import { useState } from "react";
import { uploadTestcase } from "../api/client";

interface Props {
  onExecutionStart: (executionId: string) => void;
}

export default function UploadForm({ onExecutionStart }: Props) {
  const [file, setFile] = useState<File | null>(null);
  const [baseUrl, setBaseUrl] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async () => {
    setError(null);
    if (!file) return setError("Please select a CSV test case file.");
    if (!baseUrl) return setError("Base URL is required.");

    const formData = new FormData();
    formData.append("file", file);
    formData.append("base_url", baseUrl);
    if (username) formData.append("username", username);
    if (password) formData.append("password", password);

    setLoading(true);
    try {
      const res = await uploadTestcase(formData);
      onExecutionStart(res.execution_id);
    } catch (err: any) {
      setError(err.response?.data?.detail || "Upload failed. Please try again.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="card">
      <div className="card-header">
        <span className="card-icon">📂</span>
        <h2>Run Test Case</h2>
      </div>

      <div className="card-body">
        <div className="upload-grid">
          {/* File upload */}
          <div className="form-group span-full">
            <label className="form-label">Test Case File <span className="optional">.csv</span></label>
            <div className="file-drop-area">
              <input
                type="file"
                accept=".csv"
                onChange={e => setFile(e.target.files?.[0] || null)}
              />
              <span className="file-drop-icon">📄</span>
              <div className="file-drop-text">
                {file ? (
                  <strong className="file-selected-name">✓ {file.name}</strong>
                ) : (
                  <>
                    <strong>Click to browse or drop a file</strong>
                    <span>Supported format: CSV</span>
                  </>
                )}
              </div>
            </div>
          </div>

          {/* Base URL */}
          <div className="form-group span-full">
            <label className="form-label">Base URL</label>
            <input
              type="url"
              placeholder="https://your-app.example.com"
              value={baseUrl}
              onChange={e => setBaseUrl(e.target.value)}
            />
          </div>

          {/* Credentials */}
          <div className="form-group">
            <label className="form-label">
              Username <span className="optional">(optional)</span>
            </label>
            <input
              type="text"
              placeholder="username"
              value={username}
              onChange={e => setUsername(e.target.value)}
            />
          </div>

          <div className="form-group">
            <label className="form-label">
              Password <span className="optional">(optional)</span>
            </label>
            <input
              type="password"
              placeholder="••••••••"
              value={password}
              onChange={e => setPassword(e.target.value)}
            />
          </div>
        </div>

        {error && <div className="inline-error" style={{ marginTop: 14 }}>{error}</div>}

        <div className="form-actions" style={{ marginTop: 18 }}>
          <button
            className="btn btn-primary"
            disabled={loading}
            onClick={submit}
          >
            {loading ? (
              <>
                <span className="spinner" />
                Uploading…
              </>
            ) : (
              <>▶ Execute Tests</>
            )}
          </button>
        </div>
      </div>
    </div>
  );
}
