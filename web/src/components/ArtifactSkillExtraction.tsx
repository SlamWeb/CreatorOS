import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { apiUrl, studioApi } from "../api/client";
import type { ExtractedSkillDraft, SkillExtractionFile, SkillExtractionMode } from "../api/types";
import "./artifact-skill-extraction.css";

const modeLabels: Record<SkillExtractionMode, string> = {
  single: "单一完整 Skill（默认）", pair: "Mind + Visualize", mind: "仅 Mind", visual: "仅 Visualize",
};
const statusLabels: Record<string, string> = {
  running: "进行中", ready: "待确认", saved: "已入库", failed: "失败", interrupted: "已中断",
};
const roleLabels: Record<string, string> = { mind: "Mind", production: "Visualize", legacy_end_to_end: "完整 Skill" };
const trialStatusLabels: Record<string, string> = { running: "试产中", completed: "试产完成", failed: "试产失败", interrupted: "试产已中断" };
const defaultTopic = "沿用参考作品的内容试做，保留内容与呈现特点。";

async function toBase64(file: File) {
  const dataUrl = await new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error(`读取图片失败：${file.name}`));
    reader.onload = () => resolve(String(reader.result));
    reader.readAsDataURL(file);
  });
  return dataUrl.slice(dataUrl.indexOf(",") + 1);
}

function roleFileKey(file: Pick<SkillExtractionFile, "role" | "path">) { return `${file.role}:${file.path}`; }

function withFrontmatterName(markdown: string, name: string) {
  const match = markdown.match(/^---\r?\n([\s\S]*?)\r?\n---(?:\r?\n|$)/);
  if (!match || !/^name\s*:/m.test(match[1])) return markdown;
  const frontmatter = match[1].replace(/^name\s*:.*/m, `name: ${JSON.stringify(name)}`);
  return markdown.replace(match[0], `---\n${frontmatter}\n---\n`);
}

function splitFrontmatter(markdown: string) {
  const match = markdown.match(/^(---\r?\n[\s\S]*?\r?\n---)(?:\r?\n)?([\s\S]*)$/);
  return match ? { frontmatter: match[1], body: match[2] } : { frontmatter: "", body: markdown };
}

function outputKindFor(skill: ExtractedSkillDraft): "image-carousel" | "text" {
  if (skill.output_kind) return skill.output_kind;
  const declaration = skill.skill_md.match(/^creatoros-output:\s*["']?([^\s"']+)["']?\s*$/m)?.[1];
  return declaration === "social-content-pack.image-carousel" ? "image-carousel" : "text";
}

function draftStorageKey(jobId: string) { return `creatoros:artifact-skill-extraction:${jobId}:draft`; }

function readStoredDraft(jobId: string): { digest: string; skills: ExtractedSkillDraft[] } | null {
  try {
    const raw = sessionStorage.getItem(draftStorageKey(jobId));
    if (!raw) return null;
    const value = JSON.parse(raw) as { digest?: unknown; skills?: unknown };
    if (typeof value.digest !== "string" || !Array.isArray(value.skills)) return null;
    const skills = value.skills.filter((skill): skill is ExtractedSkillDraft => Boolean(skill)
      && typeof skill === "object" && typeof skill.name === "string"
      && (skill.role === "mind" || skill.role === "production" || skill.role === "legacy_end_to_end")
      && typeof skill.skill_md === "string");
    return skills.length === value.skills.length ? { digest: value.digest, skills: skills.map(skill => ({ ...skill, output_kind: outputKindFor(skill) })) } : null;
  } catch { return null; }
}

function clearStoredDraft(jobId: string) {
  try { sessionStorage.removeItem(draftStorageKey(jobId)); } catch { /* The in-memory draft remains available. */ }
}

export function ArtifactSkillExtraction() {
  const cache = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const selectedId = searchParams.get("extraction");
  const [files, setFiles] = useState<File[]>([]);
  const [sourceText, setSourceText] = useState("");
  const [mode, setMode] = useState<SkillExtractionMode>("single");
  const [instruction, setInstruction] = useState("");
  const [open, setOpen] = useState(false);
  const [newWorkOpen, setNewWorkOpen] = useState(!selectedId);
  const [panelError, setPanelError] = useState("");
  const [draftSkills, setDraftSkills] = useState<ExtractedSkillDraft[]>([]);
  const [draftBaseDigest, setDraftBaseDigest] = useState("");
  const [draftDirty, setDraftDirty] = useState(false);
  const [viewerMode, setViewerMode] = useState<"preview" | "edit">("preview");
  const [activeFileKey, setActiveFileKey] = useState("");
  const [fileContent, setFileContent] = useState("");
  const [fileError, setFileError] = useState("");
  const [reviseInstruction, setReviseInstruction] = useState("");
  const [trialTopic, setTrialTopic] = useState("");
  const [selectionPrompt, setSelectionPrompt] = useState("");
  const [draftStorageError, setDraftStorageError] = useState("");
  const draftOwnerIdRef = useRef<string | null>(null);
  const trialTopicEditedRef = useRef(false);
  const requestIdRef = useRef<string | null>(null);
  const reviseRequestIdRef = useRef<string | null>(null);
  const trialRequestIdRef = useRef<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const uploadedFilesRef = useRef<File[]>([]);
  const uploadedIdsRef = useRef<string[]>([]);

  const resetCreateAttempt = () => { requestIdRef.current = null; uploadedFilesRef.current = []; uploadedIdsRef.current = []; };
  const resetReviseAttempt = () => { reviseRequestIdRef.current = null; };
  const resetTrialAttempt = () => { trialRequestIdRef.current = null; };

  const jobs = useQuery({
    queryKey: ["skill-extractions"], queryFn: studioApi.skillExtractions,
    refetchInterval: query => query.state.data?.items.some(item => item.status === "running" || item.operation) ? 2500 : false,
  });
  const selected = useQuery({
    queryKey: ["skill-extraction", selectedId], queryFn: () => studioApi.skillExtraction(selectedId!), enabled: Boolean(selectedId),
    refetchInterval: query => query.state.data && (query.state.data.status === "running" || query.state.data.operation) ? 2000 : false,
  });
  const activeJob = selected.data;

  useEffect(() => {
    if (selectedId && draftDirty && draftOwnerIdRef.current && selectedId !== draftOwnerIdRef.current) {
      setPanelError("页面切换已暂停：当前 Skill 草稿尚未保存。请先保存或明确丢弃修改。");
      setSearchParams(previous => { const next = new URLSearchParams(previous); next.set("extraction", draftOwnerIdRef.current!); return next; }, { replace: true });
      return;
    }
    if (selectedId) { setOpen(true); setNewWorkOpen(false); }
  }, [selectedId, draftDirty, setSearchParams]);
  useEffect(() => {
    if (!activeJob || draftDirty) return;
    const stored = activeJob.status === "ready" ? readStoredDraft(activeJob.id) : null;
    if (stored) {
      setDraftSkills(stored.skills);
      setDraftBaseDigest(stored.digest);
      setDraftDirty(true);
    } else {
      setDraftSkills(activeJob.skills.map(skill => ({ ...skill, output_kind: outputKindFor(skill) })));
      setDraftBaseDigest(activeJob.digest ?? "");
    }
    draftOwnerIdRef.current = activeJob.id;
    setViewerMode("preview");
  }, [activeJob?.id, activeJob?.digest, activeJob?.skills, draftDirty]);
  useEffect(() => {
    if (!draftDirty || !activeJob || draftOwnerIdRef.current !== activeJob.id) return;
    try {
      sessionStorage.setItem(draftStorageKey(activeJob.id), JSON.stringify({ digest: draftBaseDigest, skills: draftSkills }));
      setDraftStorageError("");
    } catch {
      setDraftStorageError("浏览器无法暂存本地草稿；离开或刷新前请先保存草稿。");
    }
  }, [activeJob?.id, draftBaseDigest, draftDirty, draftSkills]);
  useEffect(() => {
    if (!activeJob) return;
    trialTopicEditedRef.current = false;
    setTrialTopic(defaultTopic);
    setReviseInstruction(""); resetReviseAttempt(); resetTrialAttempt();
  }, [activeJob?.id]);
  useEffect(() => {
    if (!activeJob || trialTopicEditedRef.current) return;
    setTrialTopic(activeJob.suggested_topic?.trim() || defaultTopic);
  }, [activeJob?.id, activeJob?.suggested_topic]);

  const create = useMutation({
    retry: false,
    mutationFn: async () => {
      if (draftDirty) throw new Error("请先保存或明确丢弃当前草稿，再开始新的提炼任务。");
      const text = sourceText.trim();
      if (!files.length && !text) throw new Error("请上传至少一张图片，或粘贴参考文案。");
      if (text.length > 20000) throw new Error("参考文案最多 20,000 字符。");
      if (files.length > 6) throw new Error("最多选择 6 张图片。");
      const allowed = new Set(["image/png", "image/jpeg", "image/webp"]);
      for (const file of files) {
        if (!allowed.has(file.type)) throw new Error(`${file.name} 不是 PNG、JPEG 或 WebP 图片。`);
        if (file.size > 4 * 1024 * 1024) throw new Error(`${file.name} 超过单张 4 MiB 限制。`);
      }
      const sameFiles = uploadedFilesRef.current.length === files.length && uploadedFilesRef.current.every((file, index) => file === files[index]);
      if (!sameFiles) { uploadedFilesRef.current = [...files]; uploadedIdsRef.current = []; }
      requestIdRef.current ??= crypto.randomUUID().replaceAll("-", "");
      for (let index = uploadedIdsRef.current.length; index < files.length; index += 1) {
        const file = files[index];
        const uploaded = await studioApi.uploadSkillExtractionImage({ name: file.name, data_base64: await toBase64(file) });
        uploadedIdsRef.current.push(uploaded.id);
      }
      return studioApi.createSkillExtraction({
        request_id: requestIdRef.current, upload_ids: [...uploadedIdsRef.current], source_text: text,
        mode, instruction: instruction.trim(),
      });
    },
    onSuccess: async job => {
      setFiles([]); setSourceText(""); if (fileInputRef.current) fileInputRef.current.value = "";
      resetCreateAttempt(); setPanelError("");
      setNewWorkOpen(false);
      setSearchParams(previous => { const next = new URLSearchParams(previous); next.set("extraction", job.id); return next; });
      await Promise.all([cache.setQueryData(["skill-extraction", job.id], job), cache.invalidateQueries({ queryKey: ["skill-extractions"] })]);
    },
    onError: error => setPanelError(error.message),
  });

  const updateDraft = useMutation({
    retry: false,
    mutationFn: () => studioApi.saveSkillExtractionDraft(activeJob!.id, {
      expected_digest: draftBaseDigest,
      skills: draftSkills.map(skill => ({ ...skill, output_kind: outputKindFor(skill) })),
    }),
    onSuccess: job => {
      cache.setQueryData(["skill-extraction", job.id], job);
      setDraftSkills(job.skills.map(skill => ({ ...skill, output_kind: outputKindFor(skill) })));
      clearStoredDraft(job.id); setDraftBaseDigest(job.digest ?? ""); draftOwnerIdRef.current = job.id;
      setDraftDirty(false); setDraftStorageError(""); setPanelError(""); setSelectionPrompt("");
    },
  });
  const revise = useMutation({
    retry: false,
    mutationFn: () => {
      if (draftDirty) throw new Error("请先保存草稿，再请求 Codex 改稿。");
      if (draftBaseDigest !== activeJob?.digest) throw new Error("当前草稿摘要已变化，请重新载入后再改稿。");
      if (!reviseInstruction.trim()) throw new Error("请先写明希望怎样修改。");
      reviseRequestIdRef.current ??= crypto.randomUUID().replaceAll("-", "");
      return studioApi.reviseSkillExtraction(activeJob!.id, {
        request_id: reviseRequestIdRef.current, expected_digest: activeJob!.digest!, instruction: reviseInstruction.trim(),
      });
    },
    onSuccess: job => {
      cache.setQueryData(["skill-extraction", job.id], job); setReviseInstruction(""); resetReviseAttempt();
      void cache.invalidateQueries({ queryKey: ["skill-extractions"] });
    },
  });
  const trial = useMutation({
    retry: false,
    mutationFn: () => {
      if (draftDirty) throw new Error("请先保存草稿，再开始试产。");
      if (draftBaseDigest !== activeJob?.digest) throw new Error("当前草稿摘要已变化，请重新载入后再试产。");
      if (!trialEligible) throw new Error("当前 Skill 不具备图片试产能力。");
      if (!trialTopic.trim()) throw new Error("请先填写试产选题。");
      trialRequestIdRef.current ??= crypto.randomUUID().replaceAll("-", "");
      return studioApi.createSkillExtractionTrial(activeJob!.id, {
        request_id: trialRequestIdRef.current, expected_digest: activeJob!.digest!, topic: trialTopic.trim(),
      });
    },
    onSuccess: job => {
      cache.setQueryData(["skill-extraction", job.id], job); resetTrialAttempt();
      void cache.invalidateQueries({ queryKey: ["skill-extractions"] });
    },
  });
  const save = useMutation({
    retry: false,
    mutationFn: () => {
      if (draftDirty) throw new Error("请先保存草稿，再加入 Skill 库。");
      if (draftBaseDigest !== activeJob?.digest) throw new Error("当前草稿摘要已变化，请重新载入后再入库。");
      return studioApi.saveSkillExtraction(activeJob!.id, activeJob!.digest!);
    },
    onSuccess: async job => {
      clearStoredDraft(job.id);
      cache.setQueryData(["skill-extraction", job.id], job);
      await Promise.all([cache.invalidateQueries({ queryKey: ["producer-skills"] }), cache.invalidateQueries({ queryKey: ["skill-extractions"] })]);
    },
  });
  const cancel = useMutation({
    retry: false, mutationFn: () => studioApi.cancelSkillExtraction(activeJob!.id),
    onSuccess: async job => { cache.setQueryData(["skill-extraction", job.id], job); await cache.invalidateQueries({ queryKey: ["skill-extractions"] }); },
  });
  useEffect(() => {
    updateDraft.reset(); revise.reset(); trial.reset(); save.reset(); cancel.reset();
  }, [selectedId]);

  const jobFiles = activeJob?.files ?? [];
  const selectedFile = jobFiles.find(file => roleFileKey(file) === activeFileKey) ?? jobFiles.find(file => file.path.toLowerCase() === "skill.md") ?? jobFiles[0];
  const selectedSkillIndex = selectedFile ? activeJob?.skills.findIndex(skill => skill.role === selectedFile.role) ?? -1 : -1;
  const selectedSkill = selectedSkillIndex >= 0 ? draftSkills[selectedSkillIndex] : undefined;
  const selectedSkillDocument = selectedSkill ? splitFrontmatter(selectedSkill.skill_md) : null;
  const trialEligible = activeJob?.status === "ready" && draftSkills.some(skill => skill.output_kind === "image-carousel" && skill.role !== "mind");
  const isStaleDraft = Boolean(activeJob?.digest && draftBaseDigest && draftBaseDigest !== activeJob.digest);
  const imageSummary = files.length ? `${files.length} 张图片 · ${(files.reduce((total, file) => total + file.size, 0) / 1024 / 1024).toFixed(1)} MiB` : "";
  const previews = useMemo(() => files.map(file => URL.createObjectURL(file)), [files]);
  useEffect(() => () => previews.forEach(url => URL.revokeObjectURL(url)), [previews]);

  useEffect(() => {
    let current = true;
    setFileError(""); setFileContent("");
    if (!selectedFile || selectedFile.kind !== "text" || selectedFile.path.toLowerCase() === "skill.md") return;
    studioApi.skillExtractionFile(activeJob!.id, selectedFile.role, selectedFile.path)
      .then(text => { if (current) setFileContent(text); })
      .catch(error => { if (current) setFileError(error.message); });
    return () => { current = false; };
  }, [activeJob?.id, selectedFile?.url, selectedFile?.kind, selectedFile?.path]);

  const selectJob = (id: string) => {
    if (id === selectedId) return;
    if (draftDirty) { setSelectionPrompt(id); return; }
    setSearchParams(previous => { const next = new URLSearchParams(previous); next.set("extraction", id); return next; });
  };
  const editSkill = (index: number, update: Partial<ExtractedSkillDraft>) => {
    setDraftSkills(current => current.map((skill, position) => position === index ? { ...skill, ...update } : skill));
    setDraftDirty(true);
  };
  const reloadDraft = () => {
    if (!activeJob) return;
    setDraftSkills(activeJob.skills.map(skill => ({ ...skill, output_kind: outputKindFor(skill) })));
    clearStoredDraft(activeJob.id); setDraftBaseDigest(activeJob.digest ?? ""); draftOwnerIdRef.current = activeJob.id;
    setDraftDirty(false); setDraftStorageError(""); setPanelError("");
  };
  const selectFile = (file: SkillExtractionFile) => { setActiveFileKey(roleFileKey(file)); setViewerMode("preview"); };

  return <section className="artifact-extraction">
    <button type="button" className="button button-secondary extraction-toggle" aria-expanded={open}
      onClick={() => setOpen(value => !value)}>{open ? "收起提炼" : "从作品提炼"}</button>
    {open && <div className="extraction-panel">
      <h2>从作品提炼 Skill</h2>
      <details className="extraction-new-work" open={newWorkOpen} onToggle={event => setNewWorkOpen(event.currentTarget.open)}>
        <summary>新作品</summary>
        <form onSubmit={event => { event.preventDefault(); setPanelError(""); create.mutate(); }}>
        <fieldset disabled={create.isPending}>
          <label className="extraction-label">参考图片（可选）
            <input ref={fileInputRef} aria-label="参考图片" type="file" accept="image/png,image/jpeg,image/webp" multiple
              onChange={event => {
                const selectedFiles = Array.from(event.currentTarget.files ?? []);
                resetCreateAttempt(); setPanelError(selectedFiles.length > 6 ? "最多选择 6 张图片；请重新选择。" : "");
                setFiles(selectedFiles.slice(0, 6));
              }} />
          </label>
          <p className="extraction-help">PNG、JPEG、WebP；最多 6 张，每张 4 MiB。{imageSummary && ` 已选 ${imageSummary}。`}</p>
          {files.length > 0 && <div className="extraction-thumbnails" aria-label="已选图片">
            {files.map((file, index) => <figure key={`${file.name}-${index}`}><img src={previews[index]} alt={file.name} /><figcaption>{file.name}</figcaption></figure>)}
          </div>}
          <label className="extraction-label">参考文案（可选）
            <textarea aria-label="参考文案" rows={5} maxLength={20000} value={sourceText}
              onChange={event => { resetCreateAttempt(); setSourceText(event.target.value); }} placeholder="粘贴作品文案；可与图片一起提交。最多 20,000 字符。" />
          </label>
          <p className="extraction-help">{sourceText.length.toLocaleString()} / 20,000 字符</p>
          <label className="extraction-label">提炼方式
            <select aria-label="提炼方式" value={mode} onChange={event => { resetCreateAttempt(); setMode(event.target.value as SkillExtractionMode); }}>
              {Object.entries(modeLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
            </select>
          </label>
          <label className="extraction-label">提炼要求（可选）
            <textarea aria-label="提炼要求" rows={3} maxLength={4000} value={instruction}
              onChange={event => { resetCreateAttempt(); setInstruction(event.target.value); }} placeholder="例如：重点总结内容结构，视觉规则保持可替换。" />
          </label>
        </fieldset>
        <button className="button button-primary" disabled={create.isPending || draftDirty || (!files.length && !sourceText.trim())}
          title={draftDirty ? "请先保存草稿或明确丢弃修改" : undefined}>
          {create.isPending ? "上传并提交中…" : "开始提炼"}
        </button>
        </form>
      </details>
      {panelError && <p role="alert" className="extraction-error">{panelError}</p>}
      {create.isError && !panelError && <p role="alert" className="extraction-error">{create.error.message}</p>}
      {jobs.isError && <p role="alert" className="extraction-error">任务列表读取失败：{jobs.error.message}</p>}
      {jobs.data?.items.length ? <div className="extraction-jobs">
        <h3>提炼任务</h3>
        {jobs.data.items.map(job => <button type="button" key={job.id} className={`extraction-job ${selectedId === job.id ? "selected" : ""}`}
          onClick={() => selectJob(job.id)}>
          <span>{job.uploads.map(upload => upload.name).join("、") || job.source_text?.slice(0, 45) || "参考作品"}</span><strong>{statusLabels[job.status] ?? "状态更新"}</strong>
        </button>)}
      </div> : null}
      {selectionPrompt && <div className="extraction-confirm-switch" role="group" aria-label="放弃未保存编辑">
        <span>切换任务会丢弃当前未保存草稿。</span>
        <button type="button" className="button button-secondary" onClick={() => {
          if (draftOwnerIdRef.current) clearStoredDraft(draftOwnerIdRef.current);
          setDraftDirty(false); setDraftStorageError(""); setSelectionPrompt("");
          setSearchParams(previous => { const next = new URLSearchParams(previous); next.set("extraction", selectionPrompt); return next; });
        }}>丢弃并切换</button>
        <button type="button" className="button button-secondary" onClick={() => setSelectionPrompt("")}>留在当前任务</button>
      </div>}
      {selectedId && selected.isPending && <p role="status">正在恢复任务…</p>}
      {selected.isError && <p role="alert" className="extraction-error">任务读取失败：{selected.error.message}</p>}
      {activeJob && <div className="extraction-detail" aria-label="提炼任务详情">
        {draftStorageError && <p role="alert" className="extraction-error">{draftStorageError}</p>}
        <div className="extraction-status"><strong>任务状态：{statusLabels[activeJob.status] ?? "状态更新"}</strong>
          {activeJob.operation && <span>当前操作：{activeJob.operation === "extract" ? "提炼" : activeJob.operation === "revise" ? "改稿" : "试产"}</span>}
          {activeJob.note && <span>{activeJob.note}</span>}
        </div>
        {activeJob.progress && <p className="extraction-help">最近活动：{activeJob.progress.last_event}</p>}
        {activeJob.error && <p role="alert" className="extraction-error">{activeJob.error}</p>}
        {(activeJob.status === "running" || activeJob.operation) && activeJob.cancel_requested && <p role="status">正在取消当前操作…</p>}
        {(activeJob.status === "running" || activeJob.operation) && !activeJob.cancel_requested && <button type="button" className="button button-secondary" disabled={cancel.isPending}
          onClick={() => cancel.mutate()}>{cancel.isPending ? "正在取消…" : "取消当前操作"}</button>}
        {cancel.isError && <p role="alert" className="extraction-error">取消失败：{cancel.error.message}</p>}
        {activeJob.skills.length > 0 && <div className="extraction-workbench">
          <nav className="extraction-file-tree" aria-label="Skill 文件">
            {activeJob.skills.map(skill => {
              const skillFiles = jobFiles.filter(file => file.role === skill.role);
              return <div className="extraction-file-group" key={skill.role}>
                <strong>{skill.name} · {roleLabels[skill.role] ?? "Skill"}</strong>
                {skillFiles.length ? skillFiles.map(file => <button key={roleFileKey(file)} type="button"
                  className={`extraction-file ${selectedFile && roleFileKey(selectedFile) === roleFileKey(file) ? "selected" : ""}`}
                  onClick={() => selectFile(file)}>{file.kind === "image" ? "▧" : "▤"} {file.path}</button>) : <button type="button" className="extraction-file selected"
                  onClick={() => { setActiveFileKey(""); setViewerMode("preview"); }}>▤ SKILL.md</button>}
              </div>;
            })}
          </nav>
          <div className="extraction-file-viewer">
            <div className="extraction-viewer-heading">
              <strong>{selectedFile?.path ?? "SKILL.md"}</strong>
              {selectedFile?.path.toLowerCase() === "skill.md" && activeJob.status === "ready" && <div>
                <button type="button" className="button button-secondary" disabled={Boolean(activeJob.operation)} onClick={() => setViewerMode("preview")}>预览</button>
                <button type="button" className="button button-secondary" disabled={Boolean(activeJob.operation)} onClick={() => setViewerMode("edit")}>编辑</button>
              </div>}
            </div>
            {selectedFile?.kind === "image" ? <img className="extraction-asset-preview" src={apiUrl(selectedFile.url)} alt={selectedFile.path} />
              : selectedFile?.path.toLowerCase() === "skill.md" && selectedSkill ? <>
                {activeJob.status === "ready" && <div className="extraction-skill-fields">
                  <label>Frontmatter 名称
                    <input aria-label={`${roleLabels[selectedSkill.role]} Frontmatter 名称`} value={selectedSkill.name}
                      onChange={event => editSkill(selectedSkillIndex, {
                        name: event.target.value, skill_md: withFrontmatterName(selectedSkill.skill_md, event.target.value),
                      })} disabled={Boolean(activeJob.operation) || create.isPending} />
                  </label>
                  <label>目标产物
                    <select aria-label={`${roleLabels[selectedSkill.role]} 目标产物`} value={selectedSkill.output_kind ?? "text"} disabled={Boolean(activeJob.operation) || create.isPending}
                      onChange={event => editSkill(selectedSkillIndex, { output_kind: event.target.value as "image-carousel" | "text" })}>
                      <option value="image-carousel">图片轮播</option><option value="text">纯文本</option>
                    </select>
                  </label>
                  <p className="extraction-help">角色固定为 {roleLabels[selectedSkill.role]}；目标产物决定是否开放图片试产。</p>
                </div>}
                {viewerMode === "edit" && activeJob.status === "ready" ? <textarea className="extraction-markdown-editor" aria-label="编辑 SKILL.md" disabled={Boolean(activeJob.operation) || create.isPending}
                  value={selectedSkill.skill_md} onChange={event => editSkill(selectedSkillIndex, { skill_md: event.target.value })} />
                  : <div className="extraction-markdown">
                    {selectedSkillDocument?.frontmatter && <pre className="extraction-frontmatter">{selectedSkillDocument.frontmatter}</pre>}
                    <Markdown remarkPlugins={[remarkGfm]}>{selectedSkillDocument?.body ?? selectedSkill.skill_md}</Markdown>
                  </div>}
              </> : selectedFile?.kind === "text" ? <pre className="extraction-text-preview">{fileContent}</pre>
                : <p className="extraction-help">请选择左侧文件。</p>}
            {fileError && <p role="alert" className="extraction-error">{fileError}</p>}
          </div>
        </div>}
        {isStaleDraft && <div className="extraction-conflict" role="alert">服务器草稿已更新；你当前编辑基于旧摘要。重新载入会放弃本地未保存内容。
          <button type="button" className="button button-secondary" onClick={reloadDraft}>重新载入服务器草稿</button>
        </div>}
        {activeJob.status === "ready" && <div className="extraction-save">
          <div className="extraction-draft-actions">
            {draftDirty && <span role="status">有未保存的草稿修改</span>}
            <button type="button" className="button button-secondary" disabled={!draftDirty || isStaleDraft || updateDraft.isPending || Boolean(activeJob.operation) || create.isPending}
              onClick={() => updateDraft.mutate()}>{updateDraft.isPending ? "保存草稿中…" : "保存草稿"}</button>
            <button type="button" className="button button-primary" disabled={!activeJob.digest || draftDirty || isStaleDraft || save.isPending || Boolean(activeJob.operation) || create.isPending}
              title={draftDirty ? "请先保存草稿" : undefined} onClick={() => save.mutate()}>{save.isPending ? "加入中…" : "确认加入 Skill 库"}</button>
          </div>
          {updateDraft.isError && <p role="alert" className="extraction-error">草稿保存失败：{updateDraft.error.message}</p>}
          {save.isError && <p role="alert" className="extraction-error">入库失败：{save.error.message}</p>}
          <label className="extraction-label">要求 Codex 修改草稿
            <textarea aria-label="要求 Codex 修改草稿" rows={2} maxLength={4000} value={reviseInstruction} disabled={create.isPending} onChange={event => { setReviseInstruction(event.target.value); resetReviseAttempt(); }}
              placeholder="说明要保留或调整的规则" />
          </label>
          <button type="button" className="button button-secondary" disabled={draftDirty || isStaleDraft || revise.isPending || Boolean(activeJob.operation) || create.isPending || !reviseInstruction.trim()}
            title={draftDirty ? "请先保存草稿" : undefined} onClick={() => revise.mutate()}>{revise.isPending ? "改稿中…" : "让 Codex 改稿"}</button>
          {revise.isError && <p role="alert" className="extraction-error">改稿失败：{revise.error.message}</p>}
          <div className="extraction-trial-controls">
            <label className="extraction-label">试产选题
              <textarea aria-label="试产选题" rows={2} maxLength={4000} value={trialTopic} disabled={create.isPending} onChange={event => { trialTopicEditedRef.current = true; setTrialTopic(event.target.value); resetTrialAttempt(); }} />
            </label>
            {!trialEligible && <p className="extraction-help">当前草稿不含图片轮播目标的 Visualize 或完整 Skill，暂不能试产。</p>}
            <button type="button" className="button button-secondary" disabled={!trialEligible || draftDirty || isStaleDraft || trial.isPending || Boolean(activeJob.operation) || create.isPending}
              title={draftDirty ? "请先保存草稿" : undefined} onClick={() => trial.mutate()}>{trial.isPending ? "启动试产中…" : "主动开始试产"}</button>
            {trial.isError && <p role="alert" className="extraction-error">试产请求失败：{trial.error.message}</p>}
          </div>
        </div>}
        {activeJob.status === "saved" && <div className="extraction-saved" role="status">
          <strong>已加入 CreatorOS Skill 库</strong>
          {activeJob.saved_skills.map(skill => <p key={skill.id}>{skill.name}{skill.local_path ? <> · 本地路径：<code>{skill.local_path}</code></> : ""}</p>)}
        </div>}
        {(activeJob.trials ?? []).length > 0 && <div className="extraction-trials">
          <h3>试产结果</h3>
          {(activeJob.trials ?? []).map(item => <article className="extraction-trial" key={item.id}>
            <div className="extraction-status"><strong>{trialStatusLabels[item.status] ?? item.status}</strong><span>选题：{item.topic}</span>
              {item.digest !== activeJob.digest && <span className="extraction-old-digest">旧草稿结果</span>}</div>
            {item.progress && <p className="extraction-help">最近活动：{item.progress.last_event}</p>}
            {item.error && <p role="alert" className="extraction-error">{item.error}</p>}
            {item.cards.length > 0 && <div className="extraction-trial-cards">{item.cards.map(card => <figure key={card.order}>
              <img src={apiUrl(card.url)} alt={`试产第 ${card.order} 张图片`} />
              <figcaption>第 {card.order} 张 · <details><summary>生图提示词</summary><pre>{card.image_prompt || "无提示词记录"}</pre></details></figcaption>
            </figure>)}</div>}
          </article>)}
        </div>}
      </div>}
    </div>}
  </section>;
}
