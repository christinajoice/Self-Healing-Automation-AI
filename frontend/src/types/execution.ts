export interface ExecutionStep {
  step: number;
  action: string;
  target?: string;
  data?: string;
  confidence: string;
  status: "PASS" | "FAIL" | "SKIPPED";
  healed: boolean;
  error?: string;
  timestamp: string;
}

export interface ExecutionResult {
  testcase_id: string;
  status: "PASS" | "FAIL" | "CANCELLED";
  start_time: string;
  end_time: string;
  steps: ExecutionStep[];
  error?: string;
}

export interface ExecutionStatus {
  execution_id: string;
  state: "QUEUED" | "RUNNING" | "COMPLETED" | "FAILED" | "CANCELLED" | "UNKNOWN";
  message: string;
  progress?: number;
  error?: string;
  results?: ExecutionResult;
}
