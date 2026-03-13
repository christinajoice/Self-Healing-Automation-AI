import { useEffect, useState } from "react";
import { fetchReports } from "../api/client";

export default function Reports() {
  const [reports, setReports] = useState<any[]>([]);

  useEffect(() => {
    fetchReports().then(res => setReports(res.reports || []));
  }, []);

  return (
    <div className="card">
      <h2>Reports</h2>
      {reports.map((r, i) => (
        <pre key={i}>{JSON.stringify(r, null, 2)}</pre>
      ))}
    </div>
  );
}
