import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { studioApi } from "../api/client";
import type { SkillExtractionMode } from "../api/types";
import "./artifact-skill-extraction.css";

const modeLabels: Record<SkillExtractionMode, string> = {
  pair: "Mind + Visualize（默认）",
  mind: "仅 Mind",
  visual: "仅 Visualize",
  single: "单一完整 Skill",
};
const statusLabels: Record<string, string> = {
  running: "进行中", ready: "待确认", saved: "已入库", failed: "失败", interrupted: "已中断",
};
const roleLabels: Record<string, string> = { mind: "Mind", production: "Visualize", legacy_end_to_end: "完整 Skill" };

async function toBase64(file: File) {
  const dataUrl = await new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error(`读取图片失败：${file.name}`));
    reader.onload = () => resolve(String(reader.result));
    reader.readAsDataURL(file);
  });
  return dataUrl.slice(dataUrl.indexOf(",") + 1);
}

export function ArtifactSkillExtraction() {
  const cache = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const selectedId = searchParams.get("extraction");
  const [files, setFiles] = useState<File[]>([]);
  const [mode, setMode] = useState<SkillExtractionMode>("pair");
  const [instruction, setInstruction] = useState("");
  const [panelError, setPanelError] = useState("");
  const [open, setOpen] = useState(false);
  const requestIdRef = useRef<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const uploadedFilesRef = useRef<File[]>([]);
  const uploadedIdsRef = useRef<string[]>([]);
  const resetAttempt = () => {
    requestIdRef.current = null;
    uploadedFilesRef.current = [];
    uploadedIdsRef.current = [];
  };

  const jobs = useQuery({
    queryKey: ["skill-extractions"],
    queryFn: studioApi.skillExtractions,
    refetchInterval: query => query.state.data?.items.some(item => item.status === "running") ? 2500 : false,
  });
  const selected = useQuery({
    queryKey: ["skill-extraction", selectedId],
    queryFn: () => studioApi.skillExtraction(selectedId!),
    enabled: Boolean(selectedId),
    refetchInterval: query => query.state.data?.status === "running" ? 2000 : false,
  });
  useEffect(() => { if (selectedId) setOpen(true); }, [selectedId]);

  const create = useMutation({
    retry: false,
    mutationFn: async () => {
      if (files.length < 1 || files.length > 6) throw new Error("请选择 1–6 张图片。");
      const allowed = new Set(["image/png", "image/jpeg", "image/webp"]);
      for (const file of files) {
        if (!allowed.has(file.type)) throw new Error(`${file.name} 不是 PNG、JPEG 或 WebP 图片。`);
        if (file.size > 4 * 1024 * 1024) throw new Error(`${file.name} 超过单张 4 MiB 限制。`);
      }
      const sameFiles = uploadedFilesRef.current.length === files.length && uploadedFilesRef.current.every((file, index) => file === files[index]);
      if (!sameFiles) {
        uploadedFilesRef.current = [...files];
        uploadedIdsRef.current = [];
      }
      requestIdRef.current ??= crypto.randomUUID().replaceAll("-", "");
      for (let index = uploadedIdsRef.current.length; index < files.length; index += 1) {
        const file = files[index];
        const uploaded = await studioApi.uploadSkillExtractionImage({ name: file.name, data_base64: await toBase64(file) });
        uploadedIdsRef.current.push(uploaded.id);
      }
      return studioApi.createSkillExtraction({
        request_id: requestIdRef.current, upload_ids: [...uploadedIdsRef.current], mode, instruction: instruction.trim(),
      });
    },
    onSuccess: async job => {
      setFiles([]); if (fileInputRef.current) fileInputRef.current.value = ""; resetAttempt(); setPanelError("");
      setSearchParams(previous => { const next = new URLSearchParams(previous); next.set("extraction", job.id); return next; });
      await Promise.all([
        cache.setQueryData(["skill-extraction", job.id], job),
        cache.invalidateQueries({ queryKey: ["skill-extractions"] }),
      ]);
    },
    onError: error => setPanelError(error.message),
  });
  const save = useMutation({
    retry: false,
    mutationFn: () => studioApi.saveSkillExtraction(selectedId!, selected.data!.digest!),
    onSuccess: async job => {
      cache.setQueryData(["skill-extraction", job.id], job);
      await Promise.all([
        cache.invalidateQueries({ queryKey: ["producer-skills"] }),
        cache.invalidateQueries({ queryKey: ["skill-extractions"] }),
        cache.invalidateQueries({ queryKey: ["skill-extraction", job.id] }),
      ]);
    },
  });
  const cancel = useMutation({
    retry: false,
    mutationFn: () => studioApi.cancelSkillExtraction(selectedId!),
    onSuccess: async job => {
      cache.setQueryData(["skill-extraction", job.id], job);
      await cache.invalidateQueries({ queryKey: ["skill-extractions"] });
    },
  });
  useEffect(() => { save.reset(); cancel.reset(); }, [selectedId]);
  const activeJob = selected.data;
  const fileSummary = useMemo(() => files.length ? `${files.length} 张图片 · ${(files.reduce((total, file) => total + file.size, 0) / 1024 / 1024).toFixed(1)} MiB` : "", [files]);
  const previews = useMemo(() => files.map(file => URL.createObjectURL(file)), [files]);
  useEffect(() => () => previews.forEach(url => URL.revokeObjectURL(url)), [previews]);

  return <section className="artifact-extraction">
    <button type="button" className="button button-secondary extraction-toggle" aria-expanded={open}
      onClick={() => setOpen(value => !value)}>{open ? "收起提炼" : "从作品提炼"}</button>
    {open && <div className="extraction-panel">
      <h2>从参考作品提炼 Skill</h2>
      <form onSubmit={event => { event.preventDefault(); setPanelError(""); create.mutate(); }}>
        <fieldset disabled={create.isPending}>
        <label className="extraction-label">参考图片
          <input ref={fileInputRef} aria-label="参考图片" type="file" accept="image/png,image/jpeg,image/webp" multiple
            onChange={event => {
              const selectedFiles = Array.from(event.currentTarget.files ?? []);
              resetAttempt(); setPanelError(selectedFiles.length > 6 ? "最多选择 6 张图片；请重新选择。" : "");
              setFiles(selectedFiles.slice(0, 6));
            }} />
        </label>
        <p className="extraction-help">PNG、JPEG、WebP；1–6 张，每张最多 4 MiB。{fileSummary && ` 已选 ${fileSummary}。`}</p>
        {files.length > 0 && <div className="extraction-thumbnails" aria-label="已选图片">
          {files.map((file, index) => <figure key={`${file.name}-${index}`}><img src={previews[index]} alt={file.name} /><figcaption>{file.name}</figcaption></figure>)}
        </div>}
        <label className="extraction-label">提炼方式
          <select aria-label="提炼方式" value={mode} onChange={event => { resetAttempt(); setMode(event.target.value as SkillExtractionMode); }}>
            {Object.entries(modeLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </select>
        </label>
        <label className="extraction-label">补充要求（可选）
          <textarea aria-label="补充要求" rows={3} maxLength={4000} value={instruction} onChange={event => { resetAttempt(); setInstruction(event.target.value); }} placeholder="例如：重点总结版式层级与配色原则" />
        </label>
        </fieldset>
        <button className="button button-primary" disabled={create.isPending || files.length === 0}>
          {create.isPending ? "上传并提交中…" : "开始提炼"}
        </button>
      </form>
      {panelError && <p role="alert" className="extraction-error">{panelError}</p>}
      {create.isError && !panelError && <p role="alert" className="extraction-error">{create.error.message}</p>}
      {jobs.isError && <p role="alert" className="extraction-error">任务列表读取失败：{jobs.error.message}</p>}
      {jobs.data?.items.length ? <div className="extraction-jobs">
        <h3>提炼任务</h3>
        {jobs.data.items.map(job => <button type="button" key={job.id} className={`extraction-job ${selectedId === job.id ? "selected" : ""}`}
          onClick={() => setSearchParams(previous => { const next = new URLSearchParams(previous); next.set("extraction", job.id); return next; })}>
          <span>{job.uploads.map(upload => upload.name).join("、") || "参考作品"}</span><strong>{statusLabels[job.status] ?? "状态更新"}</strong>
        </button>)}
      </div> : null}
      {selectedId && selected.isPending && <p role="status">正在恢复任务…</p>}
      {selected.isError && <p role="alert" className="extraction-error">任务读取失败：{selected.error.message}</p>}
      {activeJob && <div className="extraction-detail" aria-label="提炼任务详情">
        <div className="extraction-status"><strong>任务状态：{statusLabels[activeJob.status] ?? "状态更新"}</strong>{activeJob.note && <span>{activeJob.note}</span>}</div>
        {activeJob.progress && <p className="extraction-help">最近活动：{activeJob.progress.last_event}</p>}
        {activeJob.error && <p role="alert" className="extraction-error">{activeJob.error}</p>}
        {activeJob.status === "running" && activeJob.cancel_requested && <p role="status">正在取消任务…</p>}
        {activeJob.status === "running" && !activeJob.cancel_requested && <button type="button" className="button button-secondary" disabled={cancel.isPending}
          onClick={() => cancel.mutate()}>{cancel.isPending ? "正在取消…" : "取消提炼"}</button>}
        {cancel.isError && <p role="alert" className="extraction-error">{cancel.error.message}</p>}
        {activeJob.skills.map((skill, index) => <details className="extraction-skill" key={`${skill.role}-${index}`} open>
          <summary>{skill.name} · {roleLabels[skill.role] ?? "Skill"}</summary><pre>{skill.skill_md}</pre>
        </details>)}
        {activeJob.status === "ready" && <div className="extraction-save">
          <p>这是只读草稿预览；确认后加入 CreatorOS Skill 库。</p>
          <button type="button" className="button button-primary" disabled={!activeJob.digest || save.isPending} onClick={() => save.mutate()}>
            {save.isPending ? "保存中…" : "确认加入 Skill 库"}
          </button>
          {save.isError && <p role="alert" className="extraction-error">{save.error.message}</p>}
        </div>}
        {activeJob.status === "saved" && <div className="extraction-saved" role="status">
          <strong>已加入 Skill 库</strong>
          {activeJob.saved_skills.map(skill => <p key={skill.id}>{skill.name}{skill.local_path ? <> · 可在本地编辑：<code>{skill.local_path}</code></> : ""}</p>)}
        </div>}
      </div>}
    </div>}
  </section>;
}
