import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ImagePlus, Plus, X } from "lucide-react";
import { appendReferenceImages, commitReferenceSelection, imageIdentity as identity, MAX_REFERENCE_IMAGES, pasteReferenceImages } from "./clipboardImages";

export const MAX_EXTRACTION_IMAGES = MAX_REFERENCE_IMAGES;

export function ExtractionImagePicker({ files, disabled, onChange, onError }: {
  files: File[]; disabled: boolean; onChange: (files: File[]) => void | boolean; onError: (message: string) => void;
}) {
  const input = useRef<HTMLInputElement>(null);
  const root = useRef<HTMLDivElement>(null);
  const selection = useRef(files);
  selection.current = files;
  const [dragging, setDragging] = useState(false);
  const previews = useMemo(() => files.map(file => URL.createObjectURL(file)), [files]);
  useEffect(() => () => previews.forEach(url => URL.revokeObjectURL(url)), [previews]);
  useEffect(() => { if (disabled) setDragging(false); }, [disabled]);
  const add = useCallback((incoming: File[]) => {
    if (disabled || !incoming.length) return;
    const next = appendReferenceImages(selection.current, incoming);
    onError(next.error);
    selection.current = commitReferenceSelection(selection.current, next.files, onChange);
  }, [disabled, onChange, onError]);
  useEffect(() => {
    // Form-local bubbling also supports pasting while writing the requirements,
    // without capturing clipboard images from another form or Agent chat.
    const scope: HTMLElement | null = root.current?.closest("form") ?? root.current;
    if (!scope) return;
    const paste = (event: ClipboardEvent) => {
      if (event.target instanceof Element
        && event.target.closest("dialog, [role='dialog']") !== root.current?.closest("dialog, [role='dialog']")) return;
      pasteReferenceImages(event, add, disabled);
    };
    scope.addEventListener("paste", paste);
    return () => scope.removeEventListener("paste", paste);
  }, [add, disabled]);
  return <div ref={root} className="extraction-image-picker" tabIndex={disabled ? -1 : 0}
    aria-label="参考图片，可按 Ctrl+V 连续粘贴" aria-disabled={disabled}>
    <div className={`extraction-dropzone${dragging ? " is-dragging" : ""}${disabled ? " is-disabled" : ""}`}
      role="group" aria-label="参考图片上传区" onDragOver={event => {
        event.preventDefault(); event.dataTransfer.dropEffect = disabled ? "none" : "copy";
        if (!disabled && event.dataTransfer.types.includes("Files")) setDragging(true);
      }} onDragLeave={event => {
        if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setDragging(false);
      }} onDrop={event => { event.preventDefault(); setDragging(false); add(Array.from(event.dataTransfer.files)); }}>
      <ImagePlus className="extraction-upload-icon" size={28} aria-hidden="true" />
      <div className="extraction-upload-copy"><strong>{dragging ? "松开，添加这些图片" : "把同类作品放在一起"}</strong>
        <span>拖入、Ctrl+V 连续粘贴，或多选 · 最多 20 张，每张 4 MiB</span></div>
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
