/** Clipboard events only: never read clipboard history or fetch copied URLs. */
let pastedImageSequence = 0;
export const MAX_REFERENCE_IMAGES = 20;
const imageTypes = new Set(["image/png", "image/jpeg", "image/webp"]);
export const imageIdentity = (file: File) => `${file.name}:${file.size}:${file.lastModified}`;

export function clipboardImageFiles(data: Pick<DataTransfer, "items" | "files">): File[] {
  const images = Array.from(data.items).filter(item => item.kind === "file" && item.type.startsWith("image/"))
    .map(item => item.getAsFile()).filter((file): file is File => !!file);
  const candidates = images.length ? images : Array.from(data.files).filter(file => file.type.startsWith("image/"));
  return candidates.map(file => {
    const extension = file.type === "image/jpeg" ? "jpg" : file.type.split("/")[1].replace(/[^a-z0-9]/gi, "") || "image";
    // Browsers often call every copied image image.png, even unrelated works.
    return new File([file], `pasted-${++pastedImageSequence}.${extension}`, { type: file.type, lastModified: Date.now() });
  });
}

export function appendReferenceImages(current: File[], incoming: File[]): { files: File[]; error: string } {
  for (const file of incoming) {
    if (!imageTypes.has(file.type)) return { files: current, error: `${file.name}：请选择 PNG、JPEG 或 WebP 图片。` };
    if (!file.size || file.size > 4 * 1024 * 1024) return { files: current, error: `${file.name}：图片不能为空，每张最多 4 MiB。` };
  }
  const seen = new Set(current.map(imageIdentity));
  const added = incoming.filter(file => {
    const key = imageIdentity(file);
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
  if (current.length + added.length > MAX_REFERENCE_IMAGES) {
    return { files: current, error: `最多 20 张，当前已选 ${current.length} 张。本次未添加，请减少图片后再试。` };
  }
  return { files: added.length ? [...current, ...added] : current, error: "" };
}

export function commitReferenceSelection(current: File[], next: File[],
                                         onChange: (files: File[]) => void | boolean): File[] {
  // A parent may impose a stricter total including images uploaded earlier.
  return next === current || onChange(next) === false ? current : next;
}

export function pasteReferenceImages(event: Pick<ClipboardEvent, "clipboardData" | "defaultPrevented" | "preventDefault">,
                                     add: (files: File[]) => void, disabled = false): void {
  if (disabled || event.defaultPrevented || !event.clipboardData) return;
  const images = clipboardImageFiles(event.clipboardData);
  if (!images.length) return; // Keep text, links and copied HTML native; never fetch them.
  event.preventDefault();
  add(images);
}
