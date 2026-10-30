export type CreatorPlatform = "xiaohongshu" | "zhihu" | string;

export interface SeriesView {
  id: string;
  creator_id: string | null;
  name: string;
  description: string;
  audience: string;
  skill_name: string | null;
  mind_skill_id: string | null;
  production_skill_id: string | null;
  revision: number;
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

export type DiscussionStatus = "queued" | "running" | "completed" | "failed" | "interrupted";
export interface DiscussionEventView { id: number; kind: string; text: string; at: string }
export interface DiscussionImageContext { order: number; sha256: string }
export interface DiscussionSkillContext { role: string; name: string; digest: string }
export interface DiscussionContext {
  revision_number: number;
  image_count: number;
  images: DiscussionImageContext[];
  skills: DiscussionSkillContext[];
  includes: string[];
  excludes: string[];
  history_mode: "forked_production" | "explicit_snapshot" | "continued_discussion";
}
export interface DiscussionEntry {
  id: string;
  request_id: string;
  run_id: string;
  revision_id: string;
  message: string;
  status: DiscussionStatus;
  reply: string | null;
  error: string | null;
  thread_id: string | null;
  source_thread_id: string | null;
  created_at: string;
  updated_at: string;
  events: DiscussionEventView[];
  context: DiscussionContext;
}
export interface CreatorTaskItem {
  id: string;
  kind: "research" | "production" | "discussion" | "production_task" | "skill_iteration";
  task_kind?: "image" | "video";
  topic_url?: string;
  topic_title?: string;
  collection_url?: string | null;
  collection_name?: string | null;
  title: string;
  status: string;
  series_id: string | null;
  run_id: string | null;
  url: string | null;
  updated_at: string;
  last_activity_at: string | null;
}
export interface CreatorTaskList {
  items: CreatorTaskItem[];
  summary: { active: number; awaiting_approval: number; failed: number; needs_attention?: number };
  as_of: string;
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
  existing_run_version: number | null;
  production_queue_status?: string | null;
  production_queue_position?: number | null;
  available_actions: string[];
  cover_url?: string | null;
  card_count?: number | null;
  production_state?: "unproduced" | "produced";
  skill_usage?: TopicSkillUsage;
  preview_run_id?: string | null;
  preview_run_status?: string | null;
  production_task_count?: number;
  production_task_active_count?: number;
  production_collection_id?: string | null;
}

export interface TopicSkillUsage {
  source: "series" | "run" | "external";
  items: { id: string; name: string; version_number: number | null; version_status: string | null }[];
}

export interface BatchProductionResult {
  accepted_count: number;
  items: {
    topic_id: string;
    topic_title?: string;
    run_id?: string | null;
    status: string;
    accepted: boolean;
    error?: string | null;
    error_code?: string | null;
    url?: string | null;
  }[];
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

export interface RunSummary {  id: string;
  task_kind?: TaskKind;
  latest_run_id?: string | null;
  task_id?: string | null;
  production_collection_id?: string | null;
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
  heartbeat_at: string | null;
  lease_expires_at: string | null;
  retryable: boolean;
  error_stage: string | null;
  error_type: string | null;
  error_message: string | null;
  allowed_actions: string[];
  cover_url: string | null;
  card_count: number | null;
}

export type SkillOverrides = { skill_id?: string; mind_skill_id?: string; production_skill_id?: string; expected_digests: Record<string, string> };
export type TaskKind = "image" | "video";
export interface TaskSkill { skill_id: string; digest: string; role: "primary" | "supporting" }
export interface ProductionTaskInput {
  title: string; brief: string; kind?: TaskKind; skill_ids?: string[]; skills?: TaskSkill[]; skill_overrides?: SkillOverrides;
  inputs?: { collection_id: string; export_id?: string; bgm_material_id?: string; bgm_reference?: string }; depends_on?: string[];
}
export interface ProductionTaskView extends ProductionTaskInput {
  id: string; topic_id: string; position: number; run_id: string | null; run_version: number | null;
  status: string; skill_summary: string; dispatch_state?: string; last_error?: string | null;
}
export interface ProductionTaskList { topic_id: string; series_id?: string; collection_id: string | null; items: ProductionTaskView[] }
export type RunStartInput = ({ topic_id: string; mode?: "start"; request_id?: string } | {
  topic_id: string; mode: "fresh"; source_run_id: string; expected_version: number; request_id: string;
}) & { skill_overrides?: SkillOverrides };

export interface RunCancelInput {
  expected_version: number;
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
  artifact_error: string | null;
  content_summary: string | null;
  review_digest: string | null;
  cards: CardView[];
  video?: { url: string; path: string; duration_seconds: number; width: number; height: number; byte_size: number; sha256: string; has_audio: boolean } | null;
  publish_copy: { title: string; body: string; hashtags: string[] } | null;
  sources: { title: string; url: string | null }[];
}

export interface CardView {
  order: number;
  headline: string;
  url: string;
  width: number;
  height: number;
  page_spec?: string | null;
  image_prompt?: string | null;
}

export interface RunEventView {
  id: number;
  run_id: string;
  revision_id: string | null;
  attempt_id: string | null;
  event_type: string;
  from_status: string | null;
  to_status: string | null;
  created_at: string;
}

export interface RunDetail extends RunSummary {
  production_queue?: { status: string; position: number | null; submitted_at: string; last_error?: string | null; blocked_reason?: string | null } | null;
  input_snapshot: Record<string, unknown>;
  producer_thread_id: string | null;
  revisions: RevisionView[];
  events_url: string;
  production_progress?: ProductionProgressView | null;
  partial_cards?: PartialCardView[];
  publication: ManualPublicationView | null;
}

export interface PartialCardView {
  order: number;
  image_url: string;
  warnings: string[];
}

export interface ProductionProgressView {
  stage: "mind" | "visual" | "production";
  status: "running" | "completed" | "failed" | "interrupted";
  started_at: string;
  last_activity_at: string;
  last_event: string;
  activity: "thinking" | "reading" | "searching" | "tool_running" | "responding" | "waiting" | "completed" | "failed";
  completed_tool_calls: number;
  total_pages: number | null;
  phase: "planning" | "rendering" | "assembling" | null;
  current_page: number | null;
  completed_pages: number;
  page_attempt: number;
}

export interface ManualPublicationView {
  id: string;
  platform: string;
  post_url: string;
  published_at: string;
  recorded_at: string;
  metrics: PublicationMetricView[];
}

export interface PublicationMetricView {
  id: string;
  views: number | null;
  likes: number | null;
  favorites: number | null;
  comments: number | null;
  shares: number | null;
  measured_at: string;
}

export interface PreviewTopic { topic_id: string; title: string; brief: string | null }
export interface PreviewChange {
  action: "add_topics" | "reorder_topics"; series_id: string;
  creator_name: string | null; series_name: string | null;
  before_order: string[]; after_order: string[];
  before_topics: PreviewTopic[]; after_topics: PreviewTopic[];
}
export interface PendingOperationView {
  id: string;
  status: "awaiting_approval" | "needs_clarification" | "unsupported" | "stale" | "succeeded" | "cancelled" | "failed";
  decision_status: "ready" | "needs_clarification" | "unsupported";
  revision: number;
  version: number;
  request_text: string;
  scope_series_id: string | null;
  research_selection?: boolean;
  preview: { changes: PreviewChange[] } | null;
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

export interface OperationPlanInput {
  schema_version: 1;
  operations: Array<
    | { action: "add_topics"; series_id: string; topics: Array<{ topic_id: string; title: string; brief?: string; source: "manual" | "research" }> }
    | { action: "reorder_topics"; series_id: string; ordered_topic_ids: string[] }
  >;
}

export interface OperationPreviewInput {
  request_text: string;
  plan: OperationPlanInput;
  series_id?: string | null;
}

export interface OperationProposeInput {
  request_text: string;
  series_id?: string | null;
}

export interface OperationEditInput {
  instruction: string;
  expected_version: number;
  expected_revision: number;
}

export interface OperationConfirmInput {
  expected_version: number;
  expected_revision: number;
  confirmation_token: string;
}

export interface CreatorCreateInput {
  display_name: string;
  account_handle?: string;
  daily_content_limit?: number;
}

export interface SeriesCreateInput {
  name: string;
  description: string;
  audience: string;
}

export interface HealthView {
  operation_parser_configured: boolean;
  status: string;
  database: string;
  codex_available: boolean;
  writable_routes_enabled: boolean;
}

export type SkillRole = "mind" | "production" | "legacy_end_to_end" | null;

export interface ProducerSkillItem {
  id: string;
  name: string;
  display_name?: string;
  description: string;
  carousel_compatible: boolean;
  compatibility_note: string;
  commit: string | null;
  github_url: string | null;
  role: SkillRole;
  producible: boolean;
  editable?: boolean;
  local_path?: string | null;
  digest?: string;
  family_id?: string;
  parent_id?: string | null;
  version_number?: number;
  version_status?: "candidate" | "released";
  latest_trial_job_id?: string | null;
  version_trial_job_id?: string | null;
  outputs?: TaskKind[];
  capabilities?: { outputs: TaskKind[] };
}

export interface SkillLibraryView {
  creator_id: string; series_id?: string; version: number; items: ProducerSkillItem[];
  available_versions?: ProducerSkillItem[];
  defaults: Record<TaskKind, { skill_id: string; role: "primary" | "supporting" }[]>;
}

export interface SkillVersionBinding {
  series_id: string;
  series_name: string;
  role: "single" | "mind" | "production";
  skill_id: string;
  revision: number;
}

export interface SkillVersionHistory {
  family_id: string;
  items: ProducerSkillItem[];
  bindings: SkillVersionBinding[];
}

export type ProducerSkillFileKind = "markdown" | "text" | "image" | "unsupported";
export interface ProducerSkillFile {
  path: string;
  kind: ProducerSkillFileKind;
  size: number;
}
export interface ProducerSkillFiles {
  id: string;
  name: string;
  display_name?: string;
  description: string;
  role: SkillRole;
  editable?: boolean;
  digest: string;
  files: ProducerSkillFile[];
}
export interface ProducerSkillText {
  skill_id?: string;
  path: string;
  kind: "markdown" | "text";
  content: string;
  digest: string;
  editable: boolean;
  name?: string;
  display_name?: string;
  description?: string;
}
export interface ProducerSkillAssetResult {
  skill_id: string;
  path: string;
  kind: "image";
  digest: string;
  editable: true;
  operation: "add" | "replace" | "delete";
}

export interface ProducerSkillDeleteResult {
  skill_id: string;
  operation: "remove";
  removed: true;
  recovery_record: string;
}

export type SkillExtractionMode = "pair" | "mind" | "visual" | "single";
export type SkillExtractionStatus = "queued" | "running" | "ready" | "saved" | "failed" | "interrupted";
export type SkillExtractionOperation = "extract" | "revise" | "trial" | "calibrate" | null;

export interface SkillExtractionUpload {
  id: string;
  name: string;
  url: string;
}

export interface ExtractedSkillDraft {
  name: string;
  role: "mind" | "production" | "legacy_end_to_end";
  skill_md: string;
  output_kind?: "image-carousel" | "text";
}

export interface SkillExtractionFile {
  role: "mind" | "production" | "legacy_end_to_end";
  path: string;
  kind: "text" | "image";
  url: string;
}

export interface SkillExtractionTrialCard {
  order: number;
  url: string;
  image_prompt: string | null;
}

export interface SkillExtractionTrial {
  id: string;
  status: "queued" | "running" | "completed" | "failed" | "interrupted";
  digest: string;
  topic: string;
  error: string | null;
  cards: SkillExtractionTrialCard[];
  progress: ProductionProgressView | null;
  thread_id: string | null;
}

export type SkillExtractionEventKind = "message" | "tool" | "error" | "status";
export type SkillExtractionEventStatus = "running" | "completed" | "failed" | "interrupted";

export interface SkillExtractionEvent {
  id: number;
  kind: SkillExtractionEventKind;
  title: string;
  status: SkillExtractionEventStatus;
  at: string;
  updated_at: string;
  text: string;
  truncated: boolean;
  redacted: boolean;
  item_type?: string;
  item_id?: string;
}

export interface SkillExtractionEventPage {
  stream_id: string;
  items: SkillExtractionEvent[];
  has_more: boolean;
}

export interface SkillExtractionJob {
  queue_state?: "queued" | "running" | "completed" | "failed" | "interrupted" | null;
  observation_warning?: string | null;
  task_kind?: "extract" | "merge" | "author" | "calibrate" | "skill_version";
  calibration_id?: string;
  version_candidate_id?: string;
  version_candidate_digest?: string;
  version_series_revision?: number | null;
  version_series_id?: string | null;
  source_skills?: { id: string; name: string; role: ProducerSkillItem["role"]; digest: string }[];
  id: string;
  request_id: string;
  mode: SkillExtractionMode;
  instruction: string;
  source_text?: string;
  suggested_topic?: string | null;
  revision?: number;
  operation?: SkillExtractionOperation;
  status: SkillExtractionStatus;
  cancel_requested?: boolean;
  created_at: string;
  updated_at: string;
  uploads: SkillExtractionUpload[];
  files?: SkillExtractionFile[];
  trials?: SkillExtractionTrial[];
  thread_id: string | null;
  error: string | null;
  note: string | null;
  skills: ExtractedSkillDraft[];
  digest: string | null;
  saved_skills: ProducerSkillItem[];
  progress: ProductionProgressView | null;
}

export type SkillCalibrationRating = "good" | "bad";
export interface SkillCalibrationArtifact {
  run_id: string;
  revision_id: string;
  artifact_digest: string;
  order: number | null;
  rating: SkillCalibrationRating;
  comment: string;
  topic_title?: string;
  cards?: { order: number; url: string }[];
}
export interface SkillCalibrationReference {
  upload_id: string;
  rating: SkillCalibrationRating;
  comment: string;
  url?: string;
}
export interface SkillCalibrationInput {
  request_id: string;
  series_id: string;
  skill_id: string;
  expected_skill_digest: string;
  artifacts: SkillCalibrationArtifact[];
  references: SkillCalibrationReference[];
  instruction?: string;
}
export interface SkillCalibration {
  id: string;
  request_id: string;
  series_id: string;
  skill_id: string;
  base_skill_digest: string;
  status: string;
  job_id: string;
  job: SkillExtractionJob;
  base_skill_text: string;
  diff: string;
  affected_series: { id: string; name: string }[];
  applied_digest: string | null;
  apply_phase: string | null;
  apply_request_id: string | null;
  error: string | null;
  artifacts: SkillCalibrationArtifact[];
  references: SkillCalibrationReference[];
}

export interface SeriesComposeInput {
  name: string;
  description: string;
  audience: string;
  creator_id?: string | null;
  skill_name?: string | null;
  mind_skill_id?: string | null;
  production_skill_id?: string | null;
  request_id: string;
}

export interface SeriesWriteResult {
  request_id: string;
  deduplicated: boolean;
  series: SeriesView;
}
