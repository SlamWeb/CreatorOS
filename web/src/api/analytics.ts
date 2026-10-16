import { request } from "./client";

export type MetricKey = "likes" | "favorites" | "comments" | "views" | "shares";
export type MetricValues = Record<MetricKey, number | null>;
export interface MetricSample extends MetricValues { measured_at: string }
export interface AnalyticsNote {
  request_id: string;
  run_id: string;
  series_id: string | null;
  series_name: string;
  title: string;
  post_url: string;
  published_at: string;
  last_updated_at: string | null;
  next_refresh_at: string | null;
  error: string | null;
  samples: MetricSample[];
}
export interface AccountAnalyticsView {
  creator_id: string;
  enabled: boolean;
  checking: boolean;
  checked_at: string | null;
  last_updated_at: string | null;
  next_check_at: string | null;
  heartbeat_seconds: number;
  refresh_seconds: number;
  window_days: number;
  error: string | null;
  summary: MetricValues & { notes: number; sampled_notes: number };
  notes: AnalyticsNote[];
}
const base = (id: string) => `/api/creators/${encodeURIComponent(id)}/analytics`;
export const analyticsApi = {
  get: (id: string, signal?: AbortSignal) => request<AccountAnalyticsView>(base(id), { signal }),
  settings: (id: string, enabled: boolean) => request<AccountAnalyticsView>(`${base(id)}/settings`, { method: "PUT", body: JSON.stringify({ enabled }) }),
  refresh: (id: string) => request<AccountAnalyticsView>(`${base(id)}/refresh`, { method: "POST", body: "{}" }),
};
