import { request } from "./client";

export type SkillIterationAction = "pause" | "resume" | "continue";
export type SkillIteration = {
  id: string; creator_id: string; series_id: string; series_name: string; skill_id: string; skill_name: string;
  topic_id: string; topic_title: string;
  status: "queued" | "running" | "waiting_production" | "paused" | "completed" | "exhausted" | "failed" | "interrupted";
  stage: "rubric" | "prepare" | "waiting" | "judge" | "revise" | "finished";
  revision: number; round_number: number; max_rounds: number;
  judge: { version: number; threshold: number; critical_floor: number;
    criteria: { id: string; description: string; evidence: string; critical: boolean }[];
    history: { version: number; added_ids: string[]; reason: string; at: string }[] };
  rounds: { number: number; skill_id: string; version_number: number; digest: string; status: string;
    trial_job_id?: string; trial_id?: string; trial_url?: string;
    images: { order: number; url: string; alt: string }[]; score: number | null; passed: boolean;
    judge_version?: number; scores: { criterion_id: string; score: number; evidence: string }[];
    evaluations?: { judge_version: number; score: number; passed: boolean;
      scores: { criterion_id: string; score: number; evidence: string }[]; feedback: string; time: string }[];
    feedback?: string; changes?: string }[];
  best_skill_id?: string | null; best_round?: number | null; best_score?: number | null;
  error?: string | null; can_pause: boolean; can_resume: boolean; can_continue: boolean;
  usage: { input_tokens: number; output_tokens: number; calls: number; has_unknown_usage?: boolean };
  events: { time: string; kind: string; message: string; details?: unknown }[];
  url: string; created_at: string; updated_at: string;
};
export type SkillIterationActionInput = { action: SkillIterationAction; expected_revision: number; request_id: string };
export const skillIterationsApi = {
  list: (seriesId?: string) => request<{ items: SkillIteration[] }>(`/api/skill-iterations${seriesId ? `?series_id=${encodeURIComponent(seriesId)}` : ""}`),
  get: (id: string, signal?: AbortSignal) => request<SkillIteration>(`/api/skill-iterations/${encodeURIComponent(id)}`, { signal }),
  action: (id: string, input: SkillIterationActionInput) => request<SkillIteration>(`/api/skill-iterations/${encodeURIComponent(id)}/actions`, {
    method: "POST", body: JSON.stringify(input),
  }),
};
