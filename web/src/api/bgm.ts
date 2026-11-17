import type { QueryClient } from "@tanstack/react-query";
import { request } from "./client";

type AnalysisStatus = "not_started" | "queued" | "running" | "completed" | "failed" | "interrupted";

export interface BgmTrack {
  id: string;
  name: string;
  kind: "audio" | "video";
  duration_seconds: number;
  source_type: "upload" | "share" | "legacy";
  source_url: string | null;
  url: string;
  archived: boolean;
  analysis_status: AnalysisStatus;
  analysis_available: boolean;
  analysis_message?: string | null;
  reference_count: number;
}

export interface BgmImport {
  id: string;
  source_url: string;
  status: "running" | "completed" | "failed" | "interrupted";
  message?: string | null;
  track_id?: string | null;
}

interface AnalysisView {
  status: AnalysisStatus;
  analysis_available: boolean;
  message?: string | null;
  overview?: { bpm?: number | null };
  sections?: { label: string | null; start_seconds: number; end_seconds: number }[];
  beat_count?: number;
  onset_count?: number;
}

interface UsageView {
  reference_count: number;
  references: { kind: "playlist" | "production_task"; series_id: string; series_name: string; creator_id: string | null;
    creator_name: string | null; task_id?: string; task_title?: string }[];
}

const base = "/api/bgm";
const trackPath = (id: string) => base + "/" + encodeURIComponent(id);
const playlistPath = (id: string) => "/api/series/" + encodeURIComponent(id) + "/bgm";
const submit = <T,>(url: string, input: object = {}) => request<T>(url, { method: "POST", body: JSON.stringify(input) });

export const bgmApi = {
  list: (q = "", archived = false, signal?: AbortSignal) =>
    request<{ items: BgmTrack[] }>(base + "?" + new URLSearchParams({ q, archived: String(archived) }), { signal }),
  upload: (name: string, data_base64: string) => submit<BgmTrack>(base, { name, data_base64 }),
  imports: (signal?: AbortSignal) => request<{ items: BgmImport[] }>(base + "/imports", { signal }),
  import: (source_url: string) => submit<BgmImport>(base + "/imports", { source_url }),
  retryImport: (id: string) => submit<BgmImport>(base + "/imports/" + encodeURIComponent(id) + "/retry"),
  analysis: (id: string, signal?: AbortSignal) => request<AnalysisView>(trackPath(id) + "/analysis", { signal }),
  analyze: (id: string, force: boolean) => submit<AnalysisView>(trackPath(id) + "/analysis/run", { force }),
  usage: (id: string, signal?: AbortSignal) => request<UsageView>(trackPath(id) + "/usage", { signal }),
  archive: (id: string) => submit<BgmTrack>(trackPath(id) + "/archive"),
  restore: (id: string) => submit<BgmTrack>(trackPath(id) + "/restore"),
  playlist: async (id: string, signal?: AbortSignal) =>
    (await request<{ items: BgmTrack[] }>(playlistPath(id), { signal })).items,
  add: (seriesId: string, trackId: string) =>
    request<BgmTrack>(playlistPath(seriesId) + "/" + encodeURIComponent(trackId), { method: "PUT", body: "{}" }),
  remove: (seriesId: string, trackId: string) =>
    request<{ removed: boolean }>(playlistPath(seriesId) + "/" + encodeURIComponent(trackId), { method: "DELETE", body: "{}" }),
};

export const bgmListKey = (q = "", archived = false) => ["bgm-tracks", q, archived];
export const bgmPlaylistKey = (seriesId?: string) => ["bgm-playlist", seriesId];
export const bgmAnalysisKey = (trackId: string) => ["bgm-analysis", trackId];
export const bgmUsageKey = (trackId: string) => ["bgm-usage", trackId];

export function refreshBgm(client: QueryClient) {
  return Promise.all([["bgm-tracks"], ["bgm-playlist"], ["bgm-usage"]].map(queryKey => client.invalidateQueries({ queryKey })));
}
