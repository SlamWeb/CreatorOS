import { useEffect, useRef, useState } from "react";
import { X } from "lucide-react";
import { apiUrl, request, studioApi } from "../api/client";
import type { SkillExtractionJob, SkillExtractionUpload } from "../api/types";
import { ExtractionImagePicker, MAX_EXTRACTION_IMAGES } from "./ExtractionImagePicker";
import "./author-skill-form.css";

const storageKey = "creatoros:authored-skill:unsaved";
type Role = "legacy_end_to_end" | "mind" | "production";
interface Draft { name: string; display_name: string; description: string; content: string; role: Role; request_id: string; uploads: SkillExtractionUpload[] }
const emptyDraft = (): Draft => ({ name: "", display_name: "", description: "", content: "", role: "legacy_end_to_end", request_id: "", uploads: [] });
function readDraft(): Draft {
  try {
    const value = JSON.parse(sessionStorage.getItem(storageKey) ?? "null") as Partial<Draft> | null;
    if (!value || !["legacy_end_to_end", "mind", "production"].includes(value.role ?? "")) return emptyDraft();
    if (["name", "display_name", "description", "content", "request_id"].some(key => typeof value[key as keyof Draft] !== "string")) return emptyDraft();
    return { ...emptyDraft(), ...value, uploads: (value.uploads ?? []).filter(upload => typeof upload.id === "string" && typeof upload.name === "string" && typeof upload.url === "string") } as Draft;
  } catch { return emptyDraft(); }
}
async function imageData(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error(`读取图片失败：${file.name}`));
    reader.onload = () => resolve(String(reader.result).split(",")[1]);
    reader.readAsDataURL(file);
  });
}

export function AuthorSkillForm({ visible, blocked, onCreated, onDirtyChange, onBusyChange }: {
  visible: boolean; blocked: boolean; onCreated: (job: SkillExtractionJob) => void; onDirtyChange: (dirty: boolean) => void; onBusyChange: (busy: boolean) => void;
}) {
  const [draft, setDraft] = useState(readDraft);
  const [files, setFiles] = useState<File[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [progress, setProgress] = useState("");
  const [storageError, setStorageError] = useState(false);
  const lock = useRef(false);
  const mounted = useRef(true);
  const savedUploads = useRef(new Map<File, SkillExtractionUpload>());
  const firstInput = useRef<HTMLInputElement>(null);
  const dirty = Boolean(draft.name || draft.display_name || draft.description || draft.content || draft.uploads.length || files.length);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => { if (visible) firstInput.current?.focus(); }, [visible]);
  useEffect(() => { onDirtyChange(dirty); }, [dirty, onDirtyChange]);
  useEffect(() => { onBusyChange(busy); }, [busy, onBusyChange]);
  useEffect(() => {
    try { if (dirty) sessionStorage.setItem(storageKey, JSON.stringify(draft)); else sessionStorage.removeItem(storageKey); setStorageError(false); }
    catch { setStorageError(true); }
  }, [draft, dirty]);
  useEffect(() => {
    if (!dirty) return;
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);
  const edit = <K extends keyof Draft>(key: K, value: Draft[K]) => {
    setDraft(current => ({ ...current, [key]: value, request_id: "" })); setError("");
  };
  const save = async () => {
    if (lock.current || blocked) return;
    lock.current = true; setBusy(true); setError("");
    let current = draft;
    try {
      if (current.name.trim().length > 64 || !/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(current.name.trim())) throw new Error("英文标识请用小写字母或数字，单个连字符分隔，最多 64 字符。");
      if (!current.description.trim()) throw new Error("请填写一句用途说明。");
      if (!current.content.trim()) throw new Error("请粘贴提示词或 SKILL.md。");
      if (files.length + current.uploads.length > MAX_EXTRACTION_IMAGES) throw new Error("参考图片合计最多 20 张。");
      current = { ...current, request_id: current.request_id || crypto.randomUUID().replaceAll("-", "") };
      setDraft(current);
      const uploads = [...current.uploads];
      for (let index = 0; index < files.length; index += 1) {
        const file = files[index];
        setProgress(`正在上传 ${index + 1} / ${files.length}…`);
        let upload = savedUploads.current.get(file);
        if (!upload) { upload = await studioApi.uploadSkillExtractionImage({ name: file.name, data_base64: await imageData(file) }); savedUploads.current.set(file, upload); }
        if (!uploads.some(item => item.id === upload.id)) uploads.push(upload);
      }
      current = { ...current, uploads };
      setDraft(current); setFiles([]); savedUploads.current.clear();
      setProgress("正在保存草稿…");
      const job = await request<SkillExtractionJob>("/api/skill-extractions/author", {
        method: "POST", body: JSON.stringify({ request_id: current.request_id, name: current.name.trim(), ...(current.display_name.trim() ? { display_name: current.display_name.trim() } : {}),
          description: current.description.trim(), content: current.content, role: current.role, upload_ids: uploads.map(upload => upload.id) }),
      });
      try { sessionStorage.removeItem(storageKey); } catch { /* Saving to the server has already succeeded. */ }
      setDraft(emptyDraft()); if (mounted.current) onCreated(job);
    } catch (caught) { setError(caught instanceof Error ? caught.message : "保存失败，请保留输入后重试。"); }
    finally { lock.current = false; setBusy(false); setProgress(""); }
  };
  return <form className="author-skill-form" hidden={!visible} aria-label="自己写 Skill" onSubmit={event => { event.preventDefault(); void save(); }}>
    <h2>自己写 Skill</h2>
    <p className="extraction-help">直接保存你调好的提示词，不再经过提炼。参考图片会放入 assets。</p>
    <fieldset disabled={busy}>
      <div className="author-skill-fields">
        <label className="extraction-label">显示名称（可选）<input ref={firstInput} maxLength={80} value={draft.display_name} onChange={event => edit("display_name", event.target.value)} placeholder="例如：四格英语漫画" /></label>
        <label className="extraction-label">英文标识<input required maxLength={64} value={draft.name} onChange={event => edit("name", event.target.value)} placeholder="例如：english-comic" autoCapitalize="none" spellCheck={false} /></label>
        <label className="extraction-label">Skill 类型<select value={draft.role} onChange={event => edit("role", event.target.value as Role)}><option value="legacy_end_to_end">完整 Skill</option><option value="mind">Mind · 内容方法</option><option value="production">Visualize · 呈现方法</option></select></label>
      </div>
      <label className="extraction-label">用途说明<input required maxLength={1000} value={draft.description} onChange={event => edit("description", event.target.value)} placeholder="它能制作什么，什么时候使用" /></label>
      <label className="extraction-label">提示词或完整 SKILL.md<textarea required rows={12} maxLength={20000} value={draft.content} onChange={event => edit("content", event.target.value)} placeholder="粘贴你已经调好的提示词，或带 frontmatter 的完整 SKILL.md" /></label>
      <p className="extraction-help">{draft.content.length.toLocaleString()} / 20,000 字符 · 文字会在本标签页暂存，未上传图片刷新后需重新选择。</p>
      <label className="extraction-label">参考图片（可选）</label>
      <ExtractionImagePicker files={files} disabled={busy} onError={setError} onChange={next => {
        if (next.length + draft.uploads.length > MAX_EXTRACTION_IMAGES) { setError("参考图片合计最多 20 张。"); return; }
        setFiles(next); edit("request_id", "");
      }} />
      {!!draft.uploads.length && <div className="extraction-image-grid author-skill-uploaded" aria-label="已上传参考图片">{draft.uploads.map(upload => <figure key={upload.id}>
        <a href={apiUrl(upload.url)} target="_blank" rel="noopener noreferrer" aria-label={`预览 ${upload.name}`}><img src={apiUrl(upload.url)} alt={upload.name} /></a>
        <button type="button" className="extraction-remove-image" aria-label={`移除 ${upload.name}`} onClick={() => edit("uploads", draft.uploads.filter(item => item.id !== upload.id))}><X size={16} /></button><figcaption>{upload.name}</figcaption>
      </figure>)}</div>}
    </fieldset>
    {storageError && <p role="alert" className="extraction-error">浏览器无法暂存文字；离开前请先保存草稿。</p>}
    {error && <p role="alert" className="extraction-error">{error}</p>}
    {blocked && <p className="extraction-help">请先保存或丢弃下方任务的编辑，再创建新草稿。</p>}
    <button type="submit" className="button button-primary" disabled={busy || blocked || !draft.name.trim() || !draft.description.trim() || !draft.content.trim()}>{busy ? progress || "保存中…" : "保存为草稿"}</button>
  </form>;
}
