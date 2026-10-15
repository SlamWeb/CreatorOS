import { ImagePlus, X } from "lucide-react";
import { useRef } from "react";
import { apiUrl } from "../api/client";
import "./agentAttachments.css";

export type ChatAttachment = { id: string; name: string; mime_type: string; size: number; width: number; height: number; url: string };
export const MAX_CHAT_IMAGES = 6;
export function isSessionAttachment(value: unknown, sessionId: string): value is ChatAttachment {
  if (!value || typeof value !== "object") return false;
  const image = value as Partial<ChatAttachment>;
  return typeof image.id === "string" && typeof image.name === "string" &&
    image.url === `/api/agent/sessions/${encodeURIComponent(sessionId)}/attachments/${encodeURIComponent(image.id)}`;
}
export function readAttachmentDraft(sessionId: string | null): ChatAttachment[] {
  if (!sessionId) return [];
  try {
    const value: unknown = JSON.parse(sessionStorage.getItem(`creatoros.agent.images.v1:${sessionId}`) ?? "[]");
    return Array.isArray(value) ? value.filter(item => isSessionAttachment(item, sessionId)) : [];
  } catch { return []; }
}
export function writeAttachmentDraft(sessionId: string, items: ChatAttachment[]) {
  try {
    if (items.length) sessionStorage.setItem(`creatoros.agent.images.v1:${sessionId}`, JSON.stringify(items));
    else sessionStorage.removeItem(`creatoros.agent.images.v1:${sessionId}`);
  } catch { /* Uploads remain usable in memory if browser storage is full. */ }
}
export function imageFileError(file: File): string | null {
  if (!["image/jpeg", "image/png", "image/webp", "image/gif"].includes(file.type)) return `${file.name}：请选择 JPG、PNG、WebP 或 GIF 图片。`;
  if (file.size > 4 * 1024 * 1024) return `${file.name}：图片超过 4 MiB，请压缩后重试。`;
  if (!file.size) return `${file.name}：图片为空，请重新选择。`;
  return null;
}
export function imageBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(",", 2)[1]);
    reader.onerror = () => reject(new Error(`无法读取 ${file.name}，请重新选择。`));
    reader.readAsDataURL(file);
  });
}
export function ChatImages({ attachments, remove, disabled = false }: {
  attachments: ChatAttachment[]; remove?: (id: string) => void; disabled?: boolean;
}) {
  return <div className="chat-images" aria-label={remove ? "待发送图片" : "消息图片"}>
    {attachments.map((image, index) => <div className="chat-image" key={image.id}>
      <a href={apiUrl(image.url)} target="_blank" rel="noopener noreferrer" aria-label={`查看图片 ${index + 1}：${image.name}`}>
        <img src={apiUrl(image.url)} alt={image.name} width={image.width} height={image.height} loading="lazy" />
      </a>
      {remove && <button type="button" aria-label={`移除图片 ${image.name}`} disabled={disabled} onClick={() => remove(image.id)}><X size={15} /></button>}
    </div>)}
  </div>;
}
export function ImagePicker({ disabled, add }: { disabled: boolean; add: (files: File[]) => void }) {
  const input = useRef<HTMLInputElement>(null);
  return <>
    <input ref={input} className="sr-only" tabIndex={-1} type="file" accept="image/jpeg,image/png,image/webp,image/gif" multiple
      aria-label="选择图片文件" disabled={disabled} onChange={event => { add(Array.from(event.target.files ?? [])); event.target.value = ""; }} />
    <button className="chat-image-picker" type="button" aria-label="添加图片" title="添加图片，也可在输入框粘贴（最多 6 张，每张 4 MiB）"
      disabled={disabled} onClick={() => input.current?.click()}><ImagePlus size={19} aria-hidden="true" /></button>
  </>;
}
