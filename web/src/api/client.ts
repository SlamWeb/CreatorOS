import type {
  BatchProductionResult,
  CreatorView,
  CreatorDirectoryItem,
  CreatorTaskList,
  CreatorCreateInput,
  HealthView,
  OverviewView,
  OperationConfirmInput,
  OperationPreviewInput,
  OperationProposeInput,
  OperationEditInput,
  PageResponse,
  PendingOperationView,
  ProducerSkillItem,
  ProducerSkillFiles,
  ProducerSkillText,
  ProducerSkillAssetResult,
  ProducerSkillDeleteResult,
  SkillVersionHistory,
  SkillSwitchInspection,
  SkillExtractionJob,
  SkillExtractionEvent,
  SkillExtractionEventPage,
  SkillExtractionMode,
  SkillCalibration,
  SkillCalibrationInput,
  RunDetail,
  DiscussionEntry,
  RunCancelInput,
  RunStartInput,
  RunSummary,
  RunEventView,
  SeriesComposeInput,
  SeriesCreateInput,
  SeriesView,
  SeriesWriteResult,
  TopicView,
  ProductionTaskList,
  ProductionTaskInput,
} from "./types";

const API_BASE = ((import.meta.env.VITE_API_BASE_URL as string | undefined) ?? "").replace(/\/$/, "");

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code = "request_failed",
    readonly runId?: string,
    readonly details?: unknown,
    readonly currentDigest?: string,
    readonly referencingFiles?: string[],
    readonly skillId?: string,
    readonly skillPath?: string,
    readonly outcomeUnknown = false,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/** Writes that lost a response must be checked, never automatically repeated. */
export function isUnknownSkillWrite(error: unknown): error is ApiError {
  return error instanceof ApiError && (error.outcomeUnknown || error.code === "studio_outcome_unknown" || error.status === 0 || error.status >= 500
    || (error.status === 409 && error.code === "request_failed"));
}

const objectValue = (value: unknown): value is Record<string, unknown> => !!value && typeof value === "object" && !Array.isArray(value);
const skillIdentity = (value: unknown) => typeof value === "string" && /^[a-z0-9-]+--[a-f0-9]{16}$/.test(value);
const skillDigest = (value: unknown) => typeof value === "string" && /^[a-f0-9]{64}$/.test(value);
const skillScope = (path: string, creatorId?: string) => creatorId
  ? `${path}${path.includes("?") ? "&" : "?"}creator_id=${encodeURIComponent(creatorId)}` : path;
async function skillWrite<T>(result: Promise<T>, valid: (value: unknown) => boolean): Promise<T> {
  try {
    const value = await result;
    if (!valid(value)) throw new SyntaxError("incomplete Skill write receipt");
    return value;
  }
  catch (error) {
    if (!(error instanceof ApiError)) throw new ApiError("写入回执无法读取，结果待核对，请先查看当前状态。", 0, "studio_outcome_unknown");
    throw error;
  }
}

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers: { Accept: "application/json", ...(init?.body ? { "Content-Type": "application/json" } : {}), ...init?.headers },
    });
  } catch {
    throw new ApiError("无法连接本地 Studio API，请确认后端已启动。", 0, "network_error");
  }
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as { detail?: string; error?: {
      message?: string; code?: string; run_id?: string; details?: unknown;
      series?: { id: string; name: string }[]; current_digest?: string; referencing_files?: string[];
      skill_id?: string; path?: string; outcome_unknown?: boolean;
    } } | null;
    throw new ApiError(body?.error?.message ?? body?.detail ?? `请求失败（${response.status}）。`, response.status,
      body?.error?.code, body?.error?.run_id, body?.error?.series ?? body?.error?.details,
      body?.error?.current_digest, body?.error?.referencing_files,
      body?.error?.skill_id, body?.error?.path, body?.error?.outcome_unknown === true);
  }
  return (await response.json()) as T;
}

export const studioApi = {
  health: () => request<HealthView>("/api/health"),
  overview: () => request<OverviewView>("/api/overview"),
  creators: () => request<PageResponse<CreatorView>>("/api/creators?limit=100"),
  creatorDirectory: async (signal?: AbortSignal): Promise<{ items: CreatorDirectoryItem[] }> => {
    const result = await request<unknown>("/api/creator-directory", { signal });
    if (!objectValue(result) || !Array.isArray(result.items) || !result.items.every(item =>
      objectValue(item) && typeof item.id === "string" && !!item.id && typeof item.display_name === "string")) {
      throw new ApiError("账号目录返回格式无效，请重试。", 0, "invalid_response");
    }
    return result as { items: CreatorDirectoryItem[] };
  },
  creator: (id: string) => request<CreatorView>(`/api/creators/${encodeURIComponent(id)}`),
  creatorTasks: (id: string, seriesId?: string) => {
    const query = new URLSearchParams();
    if (seriesId) query.set("series_id", seriesId);
    return request<CreatorTaskList>(`/api/creators/${encodeURIComponent(id)}/tasks${query.size ? `?${query}` : ""}`);
  },
  createCreator: (input: CreatorCreateInput) => request<CreatorView>("/api/creators", { method: "POST", body: JSON.stringify(input) }),
  createSeries: (id: string, input: SeriesCreateInput) => request<SeriesView>(`/api/creators/${encodeURIComponent(id)}/series`, { method: "POST", body: JSON.stringify(input) }),
  series: (id: string) => request<SeriesView>(`/api/series/${encodeURIComponent(id)}`),
  topics: (id: string) => request<PageResponse<TopicView>>(`/api/series/${encodeURIComponent(id)}/topics?limit=100`),
  productionTasks: (topicId: string) => request<ProductionTaskList>(`/api/topics/${encodeURIComponent(topicId)}/production-tasks`),
  createProductionTasks: (topicId: string, input: { request_id: string; tasks: ProductionTaskInput[] }) =>
    request<ProductionTaskList>(`/api/topics/${encodeURIComponent(topicId)}/production-tasks`, { method: "POST", body: JSON.stringify(input) }),
  resumeProductionTask: (topicId: string, taskId: string) => request<ProductionTaskList>(`/api/topics/${encodeURIComponent(topicId)}/production-tasks/${encodeURIComponent(taskId)}/resume`, { method: "POST", body: "{}" }),
  cancelProductionTask: (topicId: string, taskId: string) => request<ProductionTaskList>(`/api/topics/${encodeURIComponent(topicId)}/production-tasks/${encodeURIComponent(taskId)}/cancel`, { method: "POST", body: "{}" }),
  removeProductionTask: (topicId: string, taskId: string) => request<ProductionTaskList>(`/api/topics/${encodeURIComponent(topicId)}/production-tasks/${encodeURIComponent(taskId)}/remove`, { method: "POST", body: "{}" }),
  previewOperation: (input: OperationPreviewInput) => request<PendingOperationView>("/api/operations/preview", { method: "POST", body: JSON.stringify(input) }),
  proposeOperation: (input: OperationProposeInput) => request<PendingOperationView>("/api/operations/propose", { method: "POST", body: JSON.stringify(input) }),
  operation: (id: string) => request<PendingOperationView>(`/api/operations/${encodeURIComponent(id)}`),
  operations: (offset = 0) => request<PageResponse<PendingOperationView>>(`/api/operations?offset=${offset}&limit=20`),
  editOperation: (id: string, input: OperationEditInput) => request<PendingOperationView>(`/api/operations/${encodeURIComponent(id)}/edit`, { method: "POST", body: JSON.stringify(input) }),
  cancelOperation: (id: string, input: { expected_version: number; expected_revision: number }) => request<PendingOperationView>(`/api/operations/${encodeURIComponent(id)}/cancel`, { method: "POST", body: JSON.stringify(input) }),
  confirmOperation: (id: string, input: OperationConfirmInput) => request<PendingOperationView>(`/api/operations/${encodeURIComponent(id)}/confirm`, { method: "POST", body: JSON.stringify(input) }),
  runs: () => request<PageResponse<RunSummary>>("/api/runs?limit=100"),
  runsPage: (offset: number, creatorId?: string) => request<PageResponse<RunSummary>>(`/api/runs?limit=50&offset=${offset}${creatorId ? `&creator_id=${encodeURIComponent(creatorId)}` : ""}`),
  run: (id: string) => request<RunDetail>(`/api/runs/${encodeURIComponent(id)}`),
  runDiscussions: (id: string) => request<{ items: DiscussionEntry[] }>(`/api/runs/${encodeURIComponent(id)}/discussion`),
  createRunDiscussion: (id: string, input: { request_id: string; revision_id: string; artifact_digest: string; message: string }) =>
    request<DiscussionEntry>(`/api/runs/${encodeURIComponent(id)}/discussion`, { method: "POST", body: JSON.stringify(input) }),
  startRun: (input: RunStartInput) => request<RunDetail>("/api/runs", { method: "POST", body: JSON.stringify(input) }),
  batchProduction: (topicIds: string[]) => request<BatchProductionResult>("/api/runs/batch", { method: "POST", body: JSON.stringify({ topic_ids: topicIds }) }),
  executeRun: (id: string, version: number, initialOnly = false) => request<RunDetail>(`/api/runs/${encodeURIComponent(id)}/execute`, { method: "POST", body: JSON.stringify({ expected_version: version, ...(initialOnly ? { initial_only: true } : {}) }) }),
  cancelRun: (id: string, input: RunCancelInput) => request<RunDetail>(`/api/runs/${encodeURIComponent(id)}/cancel`, { method: "POST", body: JSON.stringify(input) }),
  approveRun: (id: string, input: { expected_version: number; revision_id: string; artifact_digest: string }) => request<RunDetail>(`/api/runs/${encodeURIComponent(id)}/approve`, { method: "POST", body: JSON.stringify(input) }),
  reviseRun: (id: string, input: { expected_version: number; instruction: string }) => request<RunDetail>(`/api/runs/${encodeURIComponent(id)}/revisions`, { method: "POST", body: JSON.stringify(input) }),
  recordPublication: (id: string, input: { expected_version: number; revision_id: string; artifact_digest: string; post_url: string }) => request<RunDetail>(`/api/runs/${encodeURIComponent(id)}/publication`, { method: "POST", body: JSON.stringify(input) }),
  addPublicationMetrics: (id: string, input: { request_id: string; views?: number; likes?: number; favorites?: number; comments?: number; shares?: number }) => request<RunDetail>(`/api/runs/${encodeURIComponent(id)}/publication/metrics`, { method: "POST", body: JSON.stringify(input) }),
  events: (id: string, after = 0) => request<{ items: RunEventView[]; next_after_id: number }>(`/api/runs/${encodeURIComponent(id)}/events?after_id=${after}`),
  seriesAll: () => request<SeriesView[]>("/api/series"),
  producerSkills: () => request<{ items: ProducerSkillItem[] }>("/api/producer-skills"),
  productionTaskSkills: () => request<{ items: ProducerSkillItem[] }>("/api/producer-skills?include_versions=true"),
  producerSkillVersions: (id: string, signal?: AbortSignal, creatorId?: string) => request<SkillVersionHistory>(
    skillScope(`/api/producer-skills/${encodeURIComponent(id)}/versions`, creatorId), { signal }),
  createSkillVersion: (id: string, input: { expected_digest: string; request_id: string }, creatorId?: string) =>
    skillWrite(request<ProducerSkillItem>(skillScope(`/api/producer-skills/${encodeURIComponent(id)}/versions`, creatorId), { method: "POST", body: JSON.stringify(input) }),
      value => objectValue(value) && skillIdentity(value.id) && skillDigest(value.digest) && value.version_status === "candidate"),
  createSkillVersionTrial: (id: string, input: { expected_digest: string; request_id: string; series_id?: string; topic: string }) =>
    request<SkillExtractionJob>(`/api/producer-skills/${encodeURIComponent(id)}/version-trials`, { method: "POST", body: JSON.stringify(input) }),
  publishSkillVersion: (id: string, input: { expected_digest: string; request_id: string }, creatorId?: string) =>
    skillWrite(request<ProducerSkillItem>(skillScope(`/api/producer-skills/${encodeURIComponent(id)}/publish-version`, creatorId), { method: "POST", body: JSON.stringify(input) }),
      value => objectValue(value) && value.id === id && value.digest === input.expected_digest && value.version_status === "released"),
  switchSeriesSkillVersion: (id: string, input: { skill_id: string; expected_revision: number; expected_digest?: string; request_id: string }, creatorId?: string) =>
    skillWrite(request<{ series_id: string; skill_id: string; revision: number }>(skillScope(`/api/series/${encodeURIComponent(id)}/skill-version`, creatorId), { method: "POST", body: JSON.stringify(input) }),
      value => objectValue(value) && value.series_id === id && value.skill_id === input.skill_id && value.revision === input.expected_revision + 1),
  inspectSeriesSkillVersion: (id: string, requestId: string, creatorId?: string) =>
    request<SkillSwitchInspection>(skillScope(`/api/series/${encodeURIComponent(id)}/skill-version?${new URLSearchParams({ request_id: requestId })}`, creatorId)),
  skillCalibrations: (seriesId?: string) => request<{ items: SkillCalibration[] }>(`/api/skill-calibrations${seriesId ? `?series_id=${encodeURIComponent(seriesId)}` : ""}`),
  skillCalibration: (id: string, signal?: AbortSignal) => request<SkillCalibration>(`/api/skill-calibrations/${encodeURIComponent(id)}`, { signal }),
  createSkillCalibration: (input: SkillCalibrationInput) => request<SkillCalibration>("/api/skill-calibrations", { method: "POST", body: JSON.stringify(input) }),
  applySkillCalibration: (id: string, input: { request_id: string; expected_digest: string; expected_skill_digest: string; trial_id: string }) => request<SkillCalibration>(`/api/skill-calibrations/${encodeURIComponent(id)}/apply`, { method: "POST", body: JSON.stringify(input) }),
  producerSkillFiles: (id: string, signal?: AbortSignal, creatorId?: string) => request<ProducerSkillFiles>(
    skillScope(`/api/producer-skills/${encodeURIComponent(id)}/files`, creatorId), { signal }),
  producerSkillFilePath: (id: string, path: string, creatorId?: string) =>
    skillScope(`/api/producer-skills/${encodeURIComponent(id)}/files/content?${new URLSearchParams({ path })}`, creatorId),
  producerSkillText: (id: string, path: string, signal?: AbortSignal, creatorId?: string) => request<ProducerSkillText>(
    studioApi.producerSkillFilePath(id, path, creatorId), { signal }),
  saveProducerSkillText: (id: string, input: { path: string; content: string; expected_digest: string; display_name?: string }, creatorId?: string) =>
    skillWrite(request<ProducerSkillText>(skillScope(`/api/producer-skills/${encodeURIComponent(id)}/files/content`, creatorId), {
      method: "PUT", body: JSON.stringify(input),
    }), value => objectValue(value) && skillIdentity(value.skill_id) && value.path === input.path
      && skillDigest(value.digest) && typeof value.content === "string"),
  saveProducerSkillAsset: (id: string, input: { path: string; data_base64: string; expected_digest: string; overwrite?: boolean }, creatorId?: string) =>
    skillWrite(request<ProducerSkillAssetResult>(skillScope(`/api/producer-skills/${encodeURIComponent(id)}/assets`, creatorId), {
      method: "PUT", body: JSON.stringify(input),
    }), value => objectValue(value) && skillIdentity(value.skill_id) && value.path === input.path && skillDigest(value.digest)),
  deleteProducerSkillAsset: (id: string, input: { path: string; expected_digest: string }, creatorId?: string) =>
    skillWrite(request<ProducerSkillAssetResult>(skillScope(`/api/producer-skills/${encodeURIComponent(id)}/assets`, creatorId), {
      method: "DELETE", body: JSON.stringify(input),
    }), value => objectValue(value) && skillIdentity(value.skill_id) && value.path === input.path && skillDigest(value.digest)),
  deleteProducerSkill: (id: string, input: { expected_digest: string }, creatorId?: string) =>
    request<ProducerSkillDeleteResult>(skillScope(`/api/producer-skills/${encodeURIComponent(id)}`, creatorId), {
      method: "DELETE", body: JSON.stringify(input),
    }),
  skillExtractions: () => request<{ items: SkillExtractionJob[] }>("/api/skill-extractions"),
  skillExtraction: (id: string) => request<SkillExtractionJob>(`/api/skill-extractions/${encodeURIComponent(id)}`),
  skillExtractionEvents: (id: string, beforeId = 0, limit = 50) => request<SkillExtractionEventPage>(
    `/api/skill-extractions/${encodeURIComponent(id)}/events?before_id=${beforeId}&limit=${limit}`),
  skillExtractionEvent: (id: string, eventId: number, streamId: string) => {
    const query = new URLSearchParams({ stream_id: streamId });
    return request<SkillExtractionEvent>(`/api/skill-extractions/${encodeURIComponent(id)}/events/${eventId}?${query}`);
  },
  uploadSkillExtractionImage: (input: { name: string; data_base64: string }) => request<{ id: string; name: string; url: string }>("/api/skill-extractions/uploads", { method: "POST", body: JSON.stringify(input) }),
  createSkillExtraction: (input: { request_id: string; upload_ids: string[]; source_text: string; mode: SkillExtractionMode; instruction: string }) => request<SkillExtractionJob>("/api/skill-extractions", { method: "POST", body: JSON.stringify(input) }),
  skillExtractionFile: async (id: string, role: string, path: string) => {
    const query = new URLSearchParams({ role, path });
    const response = await fetch(`${API_BASE}/api/skill-extractions/${encodeURIComponent(id)}/files?${query}`, { headers: { Accept: "text/plain" } });
    if (!response.ok) {
      const body = (await response.json().catch(() => null)) as { error?: { message?: string } } | null;
      throw new ApiError(body?.error?.message ?? `文件读取失败（${response.status}）。`, response.status);
    }
    return response.text();
  },
  saveSkillExtractionDraft: (id: string, input: { expected_digest: string; skills: { name: string; role: "mind" | "production" | "legacy_end_to_end"; skill_md: string; output_kind: "image-carousel" | "text" }[] }) => request<SkillExtractionJob>(`/api/skill-extractions/${encodeURIComponent(id)}/draft`, { method: "POST", body: JSON.stringify(input) }),
  reviseSkillExtraction: (id: string, input: { request_id: string; expected_digest: string; instruction: string }) => request<SkillExtractionJob>(`/api/skill-extractions/${encodeURIComponent(id)}/revise`, { method: "POST", body: JSON.stringify(input) }),
  createSkillExtractionTrial: (id: string, input: { request_id: string; expected_digest: string; topic: string }) => request<SkillExtractionJob>(`/api/skill-extractions/${encodeURIComponent(id)}/trials`, { method: "POST", body: JSON.stringify(input) }),
  saveSkillExtraction: (id: string, expected_digest: string) => request<SkillExtractionJob>(`/api/skill-extractions/${encodeURIComponent(id)}/save`, { method: "POST", body: JSON.stringify({ expected_digest }) }),
  cancelSkillExtraction: (id: string) => request<SkillExtractionJob>(`/api/skill-extractions/${encodeURIComponent(id)}/cancel`, { method: "POST", body: "{}" }),
  composeSeries: (input: SeriesComposeInput) => request<SeriesWriteResult>("/api/series", { method: "POST", body: JSON.stringify(input) }),
  renameSeries: (id: string, input: { name: string; expected_revision: number; request_id: string }) => request<SeriesWriteResult>(`/api/series/${encodeURIComponent(id)}`, { method: "PATCH", body: JSON.stringify(input) }),
  deleteSeries: (id: string, input: { expected_revision: number; request_id: string }) => request<{ id: string; status: "deleted" | "archived"; deduplicated: boolean }>(`/api/series/${encodeURIComponent(id)}`, { method: "DELETE", body: JSON.stringify(input) }),
  mergeSkills: (input: { request_id: string; skill_ids: string[]; instruction: string }) => request<SkillExtractionJob>("/api/skill-extractions/merge", { method: "POST", body: JSON.stringify(input) }),
  assignSeries: (id: string, input: { creator_id: string | null; expected_revision: number; request_id: string }) => request<SeriesWriteResult>(`/api/series/${encodeURIComponent(id)}/assignment`, { method: "POST", body: JSON.stringify(input) }),
  editTopic: (id: string, input: { title?: string; brief?: string | null }) => request<{ ok: boolean }>(`/api/topics/${encodeURIComponent(id)}`, { method: "PATCH", body: JSON.stringify(input) }),
  deleteTopic: (id: string) => request<{ ok: boolean }>(`/api/topics/${encodeURIComponent(id)}/delete`, { method: "POST", body: "{}" }),
  removeTopic: (id: string, input: { request_id: string; batch_id?: string; candidate_id?: string }) =>
    request<{ id: string; status: "deleted" | "archived" | "dismissed"; deduplicated: boolean }>(
      `/api/topics/${encodeURIComponent(id)}/remove`, { method: "POST", body: JSON.stringify(input) }),
  reorderTopics: (seriesId: string, ordered_topic_ids: string[]) => request<{ ok: boolean }>(`/api/series/${encodeURIComponent(seriesId)}/reorder`, { method: "POST", body: JSON.stringify({ ordered_topic_ids }) }),
  queueTopics: (seriesId: string, topics: { title: string; brief?: string | null; source?: "research" | "manual" }[]) => request<{ topic_ids: string[] }>(`/api/series/${encodeURIComponent(seriesId)}/queue`, { method: "POST", body: JSON.stringify({ topics, request_id: crypto.randomUUID().replaceAll("-", "") }) }),
  deleteCreator: (id: string) => request<{ ok: boolean }>(`/api/creators/${encodeURIComponent(id)}/delete`, { method: "POST", body: "{}" }),
};

export const apiUrl = (path: string) => `${API_BASE}${path}`;

export async function downloadRunImages(runId: string, revisionId: string, digest: string, signal: AbortSignal) {
  let response: Response;
  try {
    response = await fetch(apiUrl(`/api/runs/${encodeURIComponent(runId)}/revisions/${encodeURIComponent(revisionId)}/download?digest=${encodeURIComponent(digest)}`), { signal });
  } catch (error) {
    if (signal.aborted) throw error;
    throw new ApiError("下载连接中断，请重试。", 0, "network_error");
  }
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new ApiError(body?.error?.message ?? `下载失败（${response.status}），请重试。`, response.status);
  }
  const extensions: Record<string, string> = { "image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "image/gif": "gif", "application/zip": "zip" };
  const extension = extensions[response.headers.get("Content-Type")?.split(";")[0] ?? ""];
  if (!extension) throw new ApiError("下载格式异常，请刷新详情后重试。", response.status);
  return { blob: await response.blob(), extension };
}
