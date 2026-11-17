import { request } from "../api/client";
import { imageBase64 } from "./AgentAttachments";

// The existing account chat audio flow carries account-scoped attachments.
export const QA_BGM_CREATOR = "creator-f2304a25ec244d8484d8";

export async function uploadBgm(creatorId: string, file: File) {
  if (!file.size || file.size > 64 * 1024 * 1024) throw new Error("请选择不超过 64 MiB 的音频文件。");
  return request<{ id: string; name: string }>(`/api/creators/${encodeURIComponent(creatorId)}/materials`, {
    method: "POST", body: JSON.stringify({ name: file.name, data_base64: await imageBase64(file), analyze: true }),
  });
}
