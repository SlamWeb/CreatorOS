import { request } from "./client";

export interface PublicationDraft {
  title: string;
  body: string;
  tags: string[];
  visibility: string;
}
export interface PublicationRequest {
  id: string;
  run_id: string;
  revision_id: string;
  status: "draft" | "sending" | "verifying" | "unknown" | "published";
  digest: string;
  account: { id: string; name: string };
  payload: PublicationDraft & { images: { order: number; url: string; sha256: string }[] };
  can_confirm: boolean;
  blockers: string[];
  post_url: string | null;
  last_error: string | null;
  metrics: { measured_at?: string; views?: number | null; likes?: number | null; favorites?: number | null; comments?: number | null; shares?: number | null }[];
}
const base = (id: string) => `/api/publication-requests/${encodeURIComponent(id)}`;
// A server preview carries frozen images/account metadata; those are never
// editable draft inputs, even if a caller passes the whole payload at runtime.
export const publicationDraft = ({ title, body, tags, visibility }: PublicationDraft): PublicationDraft => ({ title, body, tags, visibility });
export const publicationApi = {
  forRun: (runId: string) => request<PublicationRequest | null>(`/api/runs/${encodeURIComponent(runId)}/publication-request`),
  prepare: (runId: string, draft: PublicationDraft) => request<PublicationRequest>(`/api/runs/${encodeURIComponent(runId)}/publication-request`, { method: "POST", body: JSON.stringify(publicationDraft(draft)) }),
  confirm: (id: string, digest: string) => request<PublicationRequest>(`${base(id)}/confirm`, { method: "POST", body: JSON.stringify({ expected_digest: digest }) }),
  reconcile: (id: string, postUrl?: string) => request<PublicationRequest>(`${base(id)}/reconcile`, { method: "POST", body: JSON.stringify(postUrl ? { post_url: postUrl } : {}) }),
  metrics: (id: string) => request<PublicationRequest>(`${base(id)}/metrics`, { method: "POST", body: "{}" }),
};
