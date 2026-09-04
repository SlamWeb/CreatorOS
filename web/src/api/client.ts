import type {
  CreatorView,
  CreatorCreateInput,
  HealthView,
  OverviewView,
  OperationConfirmInput,
  OperationPreviewInput,
  PageResponse,
  PendingOperationView,
  RunDetail,
  RunCancelInput,
  RunStartInput,
  RunSummary,
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
  confirmOperation: (id: string, input: OperationConfirmInput) => request<PendingOperationView>(`/api/operations/${encodeURIComponent(id)}/confirm`, { method: "POST", body: JSON.stringify(input) }),
  runs: () => request<PageResponse<RunSummary>>("/api/runs?limit=100"),
  run: (id: string) => request<RunDetail>(`/api/runs/${encodeURIComponent(id)}`),
  startRun: (input: RunStartInput) => request<RunDetail>("/api/runs", { method: "POST", body: JSON.stringify(input) }),
  executeRun: (id: string, version: number) => request<RunDetail>(`/api/runs/${encodeURIComponent(id)}/execute`, { method: "POST", body: JSON.stringify({ expected_version: version }) }),
  cancelRun: (id: string, input: RunCancelInput) => request<RunDetail>(`/api/runs/${encodeURIComponent(id)}/cancel`, { method: "POST", body: JSON.stringify(input) }),
};
