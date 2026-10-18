import { request } from "./client";
import type { RunDetail } from "./types";

export type ReferenceImage = { id: string; name: string; url: string; mime_type: string };
const seriesPath = (id: string) => `/api/series/${encodeURIComponent(id)}`;

export const referenceApi = {
  upload: (seriesId: string, name: string, data_base64: string) => request<ReferenceImage>(`${seriesPath(seriesId)}/reference-images`, {
    method: "POST", body: JSON.stringify({ name, data_base64 }),
  }),
  create: (seriesId: string, image_ids: string[], instructions: string, reference_presentation: boolean) => request<{ id: string }>(`${seriesPath(seriesId)}/reference-inputs`, {
    method: "POST", body: JSON.stringify({ image_ids, instructions, reference_presentation }),
  }),
  research: (seriesId: string, count: number, instructions: string, reference_id?: string) => request<{ id: string }>(`${seriesPath(seriesId)}/topic-research`, {
    method: "POST", body: JSON.stringify({ count, instructions, ...(reference_id ? { reference_id } : {}) }),
  }),
  produce: (seriesId: string, reference_id: string, title: string, request_id: string) => request<RunDetail>(`${seriesPath(seriesId)}/reference-production`, {
    method: "POST", body: JSON.stringify({ reference_id, title, request_id }),
  }),
};
