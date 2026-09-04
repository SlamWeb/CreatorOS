import type {
  CreatorView,
  CreatorCreateInput,
  HealthView,
  OverviewView,
  OperationConfirmInput,
  OperationPreviewInput,
  OperationProposeInput,
  OperationEditInput,
  PageResponse,
  PendingOperationView,
  RunDetail,
  RunCancelInput,
  RunStartInput,
  RunSummary,
  RunEventView,
  SeriesCreateInput,
  SeriesView,
  TopicView,
} from "./types";

const API_BASE = ((import.meta.env.VITE_API_BASE_URL as string | undefined) ?? "").replace(/\/$/, "");

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code = "request_failed",
    readonly runId?: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
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
    const body = (await response.json().catch(() => null)) as { error?: { message?: string; code?: string; run_id?: string } } | null;
    throw new ApiError(body?.error?.message ?? `请求失败（${response.status}）。`, response.status, body?.error?.code, body?.error?.run_id);
  }
  return (await response.json()) as T;
}

export const studioApi = {
  health: () => request<HealthView>("/api/health"),
  overview: () => request<OverviewView>("/api/overview"),
  creators: () => request<PageResponse<CreatorView>>("/api/creators?limit=100"),
  creator: (id: string) => request<CreatorView>(`/api/creators/${encodeURIComponent(id)}`),
  createCreator: (input: CreatorCreateInput) => request<CreatorView>("/api/creators", { method: "POST", body: JSON.stringify(input) }),
  createSeries: (id: string, input: SeriesCreateInput) => request<SeriesView>(`/api/creators/${encodeURIComponent(id)}/series`, { method: "POST", body: JSON.stringify(input) }),
  series: (id: string) => request<SeriesView>(`/api/series/${encodeURIComponent(id)}`),
  topics: (id: string) => request<PageResponse<TopicView>>(`/api/series/${encodeURIComponent(id)}/topics?limit=100`),
  previewOperation: (input: OperationPreviewInput) => request<PendingOperationView>("/api/operations/preview", { method: "POST", body: JSON.stringify(input) }),
  proposeOperation: (input: OperationProposeInput) => request<PendingOperationView>("/api/operations/propose", { method: "POST", body: JSON.stringify(input) }),
  operation: (id: string) => request<PendingOperationView>(`/api/operations/${encodeURIComponent(id)}`),
  operations: (offset = 0) => request<PageResponse<PendingOperationView>>(`/api/operations?offset=${offset}&limit=20`),
  editOperation: (id: string, input: OperationEditInput) => request<PendingOperationView>(`/api/operations/${encodeURIComponent(id)}/edit`, { method: "POST", body: JSON.stringify(input) }),
  cancelOperation: (id: string, input: { expected_version: number; expected_revision: number }) => request<PendingOperationView>(`/api/operations/${encodeURIComponent(id)}/cancel`, { method: "POST", body: JSON.stringify(input) }),
  confirmOperation: (id: string, input: OperationConfirmInput) => request<PendingOperationView>(`/api/operations/${encodeURIComponent(id)}/confirm`, { method: "POST", body: JSON.stringify(input) }),
  runs: () => request<PageResponse<RunSummary>>("/api/runs?limit=100"),
  run: (id: string) => request<RunDetail>(`/api/runs/${encodeURIComponent(id)}`),
  startRun: (input: RunStartInput) => request<RunDetail>("/api/runs", { method: "POST", body: JSON.stringify(input) }),
  executeRun: (id: string, version: number) => request<RunDetail>(`/api/runs/${encodeURIComponent(id)}/execute`, { method: "POST", body: JSON.stringify({ expected_version: version }) }),
  cancelRun: (id: string, input: RunCancelInput) => request<RunDetail>(`/api/runs/${encodeURIComponent(id)}/cancel`, { method: "POST", body: JSON.stringify(input) }),
  approveRun: (id: string, input: { expected_version: number; revision_id: string; artifact_digest: string }) => request<RunDetail>(`/api/runs/${encodeURIComponent(id)}/approve`, { method: "POST", body: JSON.stringify(input) }),
  reviseRun: (id: string, input: { expected_version: number; instruction: string }) => request<RunDetail>(`/api/runs/${encodeURIComponent(id)}/revisions`, { method: "POST", body: JSON.stringify(input) }),
  events: (id: string, after = 0) => request<{ items: RunEventView[]; next_after_id: number }>(`/api/runs/${encodeURIComponent(id)}/events?after_id=${after}`),
};

export const apiUrl = (path: string) => `${API_BASE}${path}`;
