import { useEffect, useMemo, useRef, useState } from "react";
import { ImagePlus, Plus, X } from "lucide-react";

export const MAX_EXTRACTION_IMAGES = 20;
const imageTypes = new Set(["image/png", "image/jpeg", "image/webp"]);
const identity = (file: File) => `${file.name}:${file.size}:${file.lastModified}`;

export function ExtractionImagePicker({ files, disabled, onChange, onError }: {
  files: File[]; disabled: boolean; onChange: (files: File[]) => void; onError: (message: string) => void;
}) {
  const input = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const previews = useMemo(() => files.map(file => URL.createObjectURL(file)), [files]);
  useEffect(() => () => previews.forEach(url => URL.revokeObjectURL(url)), [previews]);
  useEffect(() => { if (disabled) setDragging(false); }, [disabled]);
  const add = (incoming: File[]) => {
    if (disabled || !incoming.length) return;
    for (const file of incoming) {
      if (!imageTypes.has(file.type)) { onError(`${file.name}：请选择 PNG、JPEG 或 WebP 图片。`); return; }
      if (!file.size || file.size > 4 * 1024 * 1024) { onError(`${file.name}：图片不能为空，每张最多 4 MiB。`); return; }
    }
    const seen = new Set(files.map(identity));
    const added = incoming.filter(file => { const key = identity(file); if (seen.has(key)) return false; seen.add(key); return true; });
    if (files.length + added.length > MAX_EXTRACTION_IMAGES) {
      onError(`最多 20 张，当前已选 ${files.length} 张。本次未添加，请减少图片后再试。`); return;
    }
    onError("");
    if (added.length) onChange([...files, ...added]);
  };
  return <div className="extraction-image-picker">
    <div className={`extraction-dropzone${dragging ? " is-dragging" : ""}${disabled ? " is-disabled" : ""}`}
      role="group" aria-label="参考图片上传区" onDragOver={event => {
        event.preventDefault(); event.dataTransfer.dropEffect = disabled ? "none" : "copy";
        if (!disabled && event.dataTransfer.types.includes("Files")) setDragging(true);
      }} onDragLeave={event => {
        if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setDragging(false);
      }} onDrop={event => { event.preventDefault(); setDragging(false); add(Array.from(event.dataTransfer.files)); }}>
      <ImagePlus className="extraction-upload-icon" size={28} aria-hidden="true" />
      <div className="extraction-upload-copy"><strong>{dragging ? "松开，添加这些图片" : "把同类作品放在一起"}</strong>
        <span>拖入图片，或多选文件 · 最多 20 张，每张 4 MiB</span></div>
      <input ref={input} className="sr-only" tabIndex={-1} type="file" aria-label="参考图片"
        accept="image/png,image/jpeg,image/webp" multiple disabled={disabled}
        onChange={event => { add(Array.from(event.currentTarget.files ?? [])); event.currentTarget.value = ""; }} />
      <button type="button" className="extraction-choose-images" disabled={disabled} onClick={() => input.current?.click()}>
        <Plus size={17} aria-hidden="true" />{files.length ? "继续添加" : "选择图片"}
      </button>
    </div>
    {!!files.length && <>
      <div className="extraction-selection-count" role="status">已选 {files.length} / 20 张<span>点击图片放大</span></div>
      <div className="extraction-image-grid" aria-label="已选图片">
        {files.map((file, index) => <figure key={identity(file)}>
          <a href={previews[index]} target="_blank" rel="noopener noreferrer" aria-label={`预览 ${file.name}`}>
            <img src={previews[index]} alt={file.name} />
          </a>
          <button type="button" disabled={disabled} className="extraction-remove-image" aria-label={`移除 ${file.name}`}
            onClick={() => { onError(""); onChange(files.filter((_, position) => position !== index)); }}><X size={16} /></button>
          <figcaption title={file.name}>{file.name}</figcaption>
        </figure>)}
      </div>
    </>}
  </div>;
}
