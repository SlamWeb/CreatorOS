import { request } from "../../api/client";

export interface Score {
  score: number | null;
  observed_score?: number | null;
  coverage: number | null;
  passed?: number;
  total?: number;
  missing?: number;
  status?: string;
  scored_cases?: number;
  planned_cases?: number;
  items?: {id: string; label?: string; question?: string; critical?: boolean; status: string; detail?: string}[];
}
export interface Scores {
  protocol?: string;
  program: Score;
  semantic: Score;
  critical_status?: string;
  combined_score?: null;
}
export interface BenchmarkCase {
  id: string;
  title: string;
  category: string;
  split: string;
  intent?: string;
  expected_outcome?: string | string[];
  evaluation_scope?: string;
  steps: {kind: string; text?: string; name?: string; details?: string}[];
  scoring?: {program_checks?: {id: string; label: string; critical?: boolean}[]; critical_checks?: string[]};
  rubric: {id: string; question: string; critical?: boolean}[];
  live_codex: boolean;
}
export interface BatchRef {
  id: string;
  dataset_id?: string;
  generated_at_utc?: string;
  planned?: number;
  overall_status?: string;
  scores?: Scores | null;
}
export interface BenchmarkRow {
  case_id: string;
  title: string;
  status: string;
  auto_status?: string | null;
  semantic_status?: string | null;
  scores?: Scores | null;
  record_paths: string[];
}
export interface Overview {
  dataset_id: string;
  schema_version: number;
  cases: BenchmarkCase[];
  batches: BatchRef[];
  errors: {batch_id: string; message: string}[];
}
export interface Batch {
  batch_id: string;
  score_note: string;
  summary: {
    dataset_id: string;
    cases: BenchmarkRow[];
    scores?: Scores | null;
    planned: number;
    with_results: number;
    passed: number;
    failed: number;
    needs_review: number;
    metrics?: unknown;
    codex?: unknown;
  };
}
export interface CaseDetail {
  batch_id: string;
  case_id: string;
  row: BenchmarkRow;
  frozen_definition: BenchmarkCase | null;
  frozen_error?: string | null;
  records: {record_id: string; report: {checks?: {id: string; label: string; status: string; detail?: string}[]; [key: string]: unknown}; evidence_files: string[]}[];
  errors: string[];
}
export interface Evidence {name: string; format: string; content: unknown}
const base = (batch: string) => `/api/eval/batches/${encodeURIComponent(batch)}`;
export const evalApi = {
  overview: (signal?: AbortSignal) => request<Overview>("/api/eval", {signal}),
  batch: (batch: string, signal?: AbortSignal) => request<Batch>(base(batch), {signal}),
  detail: (batch: string, id: string, signal?: AbortSignal) => request<CaseDetail>(`${base(batch)}/cases/${id}`, {signal}),
  evidence: (batch: string, id: string, record: string, name: string, signal?: AbortSignal) =>
    request<Evidence>(`${base(batch)}/cases/${id}/evidence/${record}?${new URLSearchParams({name})}`, {signal}),
};
