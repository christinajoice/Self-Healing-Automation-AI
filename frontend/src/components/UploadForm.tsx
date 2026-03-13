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

  const submit = async () => {
    if (!file || !baseUrl) return alert("CSV & Base URL required");

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
      alert(err.response?.data?.detail || "Upload failed");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="card">
      <h2>Upload Test Case</h2>

      <input type="file" accept=".csv"
        onChange={e => setFile(e.target.files?.[0] || null)}
      />

      <input
        placeholder="Base URL"
        value={baseUrl}
        onChange={e => setBaseUrl(e.target.value)}
      />

      <input
        placeholder="Username (optional)"
        value={username}
        onChange={e => setUsername(e.target.value)}
      />

      <input
        placeholder="Password (optional)"
        type="password"
        value={password}
        onChange={e => setPassword(e.target.value)}
      />

      <button disabled={loading} onClick={submit}>
        {loading ? "Uploading..." : "Execute"}
      </button>
    </div>
  );
}
