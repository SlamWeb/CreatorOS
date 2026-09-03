export type CreatorPlatform = "xiaohongshu" | "zhihu" | string;

export interface SeriesView {
  id: string;
  creator_id: string;
  name: string;
  description: string;
  audience: string;
  skill_name: string;
  is_active: boolean;
  topic_count: number;
  available_topic_count: number;
  latest_run_status: string | null;
}

export interface CreatorView {
  id: string;
  display_name: string;
  platform: CreatorPlatform;
  account_handle: string | null;
  timezone: string;
  daily_content_limit: number | null;
  is_active: boolean;
  series: SeriesView[];
}

export interface TopicView {
  id: string;
  series_id: string;
  title: string;
  brief: string | null;
  source: string;
  status: string;
  position: number;
  existing_run_id: string | null;
  existing_run_status: string | null;
  available_actions: string[];
}

export interface PageInfo {
  offset: number;
  limit: number;
  total: number;
}

export interface PageResponse<T> {
  items: T[];
  page: PageInfo;
}

export interface RunSummary {
  id: string;
  creator_id: string;
  creator_name: string;
  series_id: string;
  series_name: string;
  topic_id: string;
  topic_title: string;
  status: string;
  version: number;
  active_revision_number: number;
  updated_at: string;
  completed_at: string | null;
  retryable: boolean;
  error_stage: string | null;
  error_type: string | null;
  error_message: string | null;
  allowed_actions: string[];
}

export interface AttemptView {
  id: string;
  attempt_number: number;
  status: string;
  producer_thread_id: string | null;
  has_output: boolean;
  usage: Record<string, unknown> | null;
  trace_available: boolean;
  error_type: string | null;
  error_message: string | null;
  started_at: string;
  heartbeat_at: string | null;
  completed_at: string | null;
  duration_ms: number | null;
}

export interface RevisionView {
  id: string;
  revision_number: number;
  instruction: string | null;
  artifact_available: boolean;
  artifact_digest: string | null;
  validation: Record<string, unknown> | null;
  validated_at: string | null;
  approved_at: string | null;
  attempts: AttemptView[];
}

export interface RunDetail extends RunSummary {
  input_snapshot: Record<string, unknown>;
  producer_thread_id: string | null;
  revisions: RevisionView[];
  events_url: string;
}

export interface PendingOperationView {
  id: string;
  status: string;
  decision_status: string;
  revision: number;
  version: number;
  request_text: string;
  preview: Record<string, unknown> | null;
  message: string | null;
  confirmation_token: string | null;
  usage: Record<string, unknown> | null;
  updated_at: string;
}

export interface OverviewView {
  counts: {
    creator_count: number;
    active_creator_count: number;
    series_count: number;
    active_series_count: number;
    producing_count: number;
    awaiting_approval_count: number;
  };
  creators: CreatorView[];
  needs_attention: RunSummary[];
  producing: RunSummary[];
  awaiting_approval: RunSummary[];
  pending_operations: PendingOperationView[];
}

export interface HealthView {
  status: string;
  database: string;
  codex_available: boolean;
  writable_routes_enabled: boolean;
}
