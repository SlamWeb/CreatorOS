import { request } from "../../api/client";

export interface EvalRound {
  id: string;
  title: string;
  change_summary: string;
  code_version: string;
  branch: string;
  merge_status: string;
  parent_round_id: string | null;
  created_at: string;
  changes: string[];
}

export interface EvalResult {
  case_id: string;
  status: string;
  summary: string;
  metrics: Record<string, unknown>;
  evidence: Record<string, unknown>;
  case_definition_hash: string | null;
}

export interface EvalRun {
  id: string;
  round_id: string;
  suite_id: string;
  suite_version: string;
  status: string;
  display_status?: string;
  heartbeat_at?: string | null;
  liveness?: string;
  planned_cases: string[];
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  metadata: Record<string, unknown>;
  results: EvalResult[];
}

export interface EvalRoundDetail {round: EvalRound; runs: EvalRun[]}

export interface EvalComparison {
  baseline_run_id: string;
  candidate_run_id: string;
  contract_compatible: boolean;
  contract_differences: {field: string; reason: string}[];
  baseline_planned: number;
  candidate_planned: number;
  common_planned: number;
  comparable_cases: number;
  outcomes: {paired_decidable: number; baseline_passed: number; candidate_passed: number};
  metrics: Record<string, {paired_cases: number; baseline_mean: number | null; candidate_mean: number | null}>;
  cases: {case_id: string; comparable: boolean; reason: string | null; baseline_status: string | null; candidate_status: string | null}[];
}

export const registryApi = {
  rounds: (signal?: AbortSignal) => request<{items: EvalRound[]}>("/api/eval/v1/rounds", {signal}),
  round: (id: string, signal?: AbortSignal) => request<EvalRoundDetail>(`/api/eval/v1/rounds/${encodeURIComponent(id)}`, {signal}),
  compare: (baseline: string, candidate: string, signal?: AbortSignal) => request<EvalComparison>(`/api/eval/v1/compare?${new URLSearchParams({baseline_run: baseline, candidate_run: candidate})}`, {signal}),
};
