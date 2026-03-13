export interface ExecutionStep {
  step: number;
  action: string;
  target?: string;
  data?: string;
  confidence: string;
  status: "PASS" | "FAIL";
  healed: boolean;
  error?: string;
  timestamp: string;
}

export interface ExecutionResult {
  testcase_id: string;
  status: "PASS" | "FAIL";
  start_time: string;
  end_time: string;
  steps: ExecutionStep[];
  error?: string | null;
}

export interface ExecutionStatus {
  execution_id: string;
  state: "QUEUED" | "RUNNING" | "COMPLETED" | "FAILED" | "UNKNOWN";
  message: string;
  progress?: number;
  error?: string;
  results?: ExecutionResult;
}
