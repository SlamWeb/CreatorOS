import { request } from "./client";

export interface ObservationNode {
  id: string;
  label: string;
  kind: string;
  status?: string | null;
  has_children: boolean;
}

export interface ObservationTree {
  items: ObservationNode[];
  next_offset: number | null;
  has_more: boolean;
}

export interface ObservationTimelineItem {
  id: string;
  label: string;
  kind: string;
  status?: string | null;
  at?: string | null;
  content?: unknown;
  node_id?: string;
}

export interface ObservationDetail {
  id: string;
  label: string;
  kind: string;
  status: string | null;
  sections: { title: string; content: unknown }[];
  links: { label: string; node_id?: string; href?: string }[];
  warnings: string[];
  active: boolean;
  breadcrumbs?: { id: string; label: string }[];
  ancestors?: { id: string; kind?: string; label: string }[];
  timeline?: ObservationTimelineItem[];
}

export const observationApi = {
  tree: (parent: string | undefined, offset = 0, signal?: AbortSignal) => {
    const query = new URLSearchParams({ offset: String(offset), limit: "100" });
    if (parent) query.set("parent", parent);
    return request<ObservationTree>(`/api/observation/tree?${query}`, { signal });
  },
  detail: (nodeId: string, signal?: AbortSignal) => request<ObservationDetail>(
    `/api/observation/detail?${new URLSearchParams({ node_id: nodeId })}`, { signal },
  ),
};
