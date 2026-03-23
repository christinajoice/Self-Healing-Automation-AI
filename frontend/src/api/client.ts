import axios from "axios";
import type { ExecutionStatus, ExecutionResult } from "../types/execution";

export const api = axios.create({
  baseURL: "/api",
});

export const uploadTestcase = async (formData: FormData) => {
  const res = await api.post<{ execution_id: string; status: string }>(
    "/upload_testcase",
    formData,
    { headers: { "Content-Type": "multipart/form-data" } }
  );
  return res.data;
};

export const fetchExecutionStatus = async (executionId: string) => {
  const res = await api.get<ExecutionStatus>(`/execution_status/${executionId}`);
  return res.data;
};

export const fetchReports = async () => {
  const res = await api.get<{ reports: ExecutionResult[] }>("/reports");
  return res.data;
};

export const cancelExecution = async (executionId: string) => {
  const res = await api.post<{ execution_id: string; status: string }>(
    `/cancel_execution/${executionId}`
  );
  return res.data;
};
