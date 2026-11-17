import { request } from "./client";
import type { CreatorView, PageResponse, SeriesView } from "./types";

export type WorkspaceObject = "creators" | "series";
export type RemovalStatus = "deleted" | "archived";
export interface RemovalAssessment {
  id: string;
  status: RemovalStatus | "blocked";
  reasons: { code: string; count: number }[];
}
const objectPath = (type: WorkspaceObject, id: string) => "/api/" + type + "/" + encodeURIComponent(id);

export const lifecycleApi = {
  preview: (type: WorkspaceObject, id: string, signal?: AbortSignal) =>
    request<RemovalAssessment>(objectPath(type, id) + "/removal", { signal }),
  accounts: (offset: number, signal?: AbortSignal) =>
    request<PageResponse<CreatorView>>("/api/creators?archived=true&limit=50&offset=" + offset, { signal }),
  series: (signal?: AbortSignal) => request<SeriesView[]>("/api/series?archived=true", { signal }),
  restore: (type: WorkspaceObject, id: string) =>
    request<{ id: string; status: "restored" }>(objectPath(type, id) + "/restore", { method: "POST", body: "{}" }),
};
