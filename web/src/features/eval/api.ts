import { request } from "../../api/client";

export type EvalStatus = "not_run" | "passed" | "failed" | "needs_review";
export interface EvalReadError { run_id: string; code: string; message: string }
export interface EvalCase {
  id: string;
  title: string;
  category: string;
  split: "dev" | "acceptance";
  status: EvalStatus;
  latest_run_id: string | null;
  run_count: number;
  definition?: {
    steps: { kind: string; text?: string; name?: string; details?: string }[];
    assertions: { id: string; pass: string; evidence: string[] }[];
    manual_checks: string[];
  };
}
export interface EvalReview { decision: "passed" | "failed"; note: string; reviewed_at: string }
export interface EvalRunSummary {
  run_id: string;
  case_id: string;
  dataset_id: string;
  started_at?: string;
  finished_at?: string;
  execution_mode: "live" | "controlled";
  entrypoint?: "browser";
  execution_status: "completed" | "failed";
  auto_status: EvalStatus;
  status: EvalStatus;
  model: { name: string | null; provider: string | null };
  usage: Record<string, number> | null;
  elapsed_seconds: number | null;
  review: EvalReview | null;
  error?: { kind: string; message: string } | null;
}
export interface EvalReport extends EvalRunSummary {
  checks: { id: string; label: string; status: string; detail: string; evidence: string[] }[];
  dimensions: Record<string, string>;
  manual_checks: string[];
  report_digest: string;
  evidence_files: { name: string; label: string }[];
}
export interface EvalOverview { dataset_id: string; cases: EvalCase[]; errors?: EvalReadError[] }
export interface EvalRuns { items: EvalRunSummary[]; errors?: EvalReadError[] }
export interface EvalEvidence { name: string; content: unknown; format: "json" | "text" }

export const evalApi = {
  overview: (signal?: AbortSignal) => request<EvalOverview>("/api/eval", { signal }),
  runs: (caseId: string, signal?: AbortSignal) => request<EvalRuns>(
    `/api/eval/runs?${new URLSearchParams({ case_id: caseId })}`, { signal }),
  report: (runId: string, signal?: AbortSignal) => request<EvalReport>(
    `/api/eval/runs/${encodeURIComponent(runId)}`, { signal }),
  evidence: (runId: string, name: string, signal?: AbortSignal) => request<EvalEvidence>(
    `/api/eval/runs/${encodeURIComponent(runId)}/evidence?${new URLSearchParams({ name })}`, { signal }),
  review: (runId: string, input: { expected_digest: string; decision: "passed" | "failed"; note: string }) =>
    request<EvalReport>(`/api/eval/runs/${encodeURIComponent(runId)}/review`, {
      method: "POST", body: JSON.stringify(input),
    }),
};
