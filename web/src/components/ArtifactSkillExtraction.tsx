import { useEffect, useId, useRef, useState } from "react";
import { FileImage, FileText } from "lucide-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { apiUrl, studioApi } from "../api/client";
import type { ExtractedSkillDraft, ProducerSkillItem, SkillCalibration, SkillExtractionFile, SkillExtractionMode } from "../api/types";
import { ExtractionActivity } from "./ExtractionActivity";
import { AuthorSkillForm } from "./AuthorSkillForm";
import { ExtractionImagePicker, MAX_EXTRACTION_IMAGES } from "./ExtractionImagePicker";
import { useWorkInbox } from "./TaskInbox";
import "./artifact-skill-extraction.css";

const modeLabels: Record<SkillExtractionMode, string> = {
  single: "单一完整 Skill（默认）", pair: "Mind + Visualize", mind: "仅 Mind", visual: "仅 Visualize",
};
const statusLabels: Record<string, string> = {
  running: "进行中", ready: "待确认", saved: "已入库", failed: "失败", interrupted: "已中断",
};
const roleLabels: Record<string, string> = { mind: "Mind", production: "Visualize", legacy_end_to_end: "完整 Skill" };
const trialStatusLabels: Record<string, string> = { running: "试产中", completed: "试产完成", failed: "试产失败", interrupted: "试产已中断" };
const progressActivityLabels: Record<string, string> = {
  thinking: "正在处理", reading: "正在读取", searching: "正在搜索", tool_running: "工具运行中",
  responding: "正在回复", waiting: "等待中", completed: "阶段完成", failed: "阶段失败",
};
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

export function ArtifactSkillExtraction({ onUseSkill, onDirtyChange }: { onUseSkill?: (skill: ProducerSkillItem) => void; onDirtyChange?: (dirty: boolean) => void } = {}) {
  const cache = useQueryClient();
  const inbox = useWorkInbox();
  const [searchParams, setSearchParams] = useSearchParams();
  const selectedId = searchParams.get("extraction");
  const [files, setFiles] = useState<File[]>([]);
  const [sourceText, setSourceText] = useState("");
  const [mode, setMode] = useState<SkillExtractionMode>("single");
  const [instruction, setInstruction] = useState("");
  const [open, setOpen] = useState(false);
  const [authorOpen, setAuthorOpen] = useState(false);
  const [authorDirty, setAuthorDirty] = useState(false);
  const [authorBusy, setAuthorBusy] = useState(false);
  const [newWorkOpen, setNewWorkOpen] = useState(!selectedId);
  const [jobsOpen, setJobsOpen] = useState(false);
  const [activityOpen, setActivityOpen] = useState(false);
  const [noteOpen, setNoteOpen] = useState(false);
  const noteId = useId();
  const [diffOpen, setDiffOpen] = useState(false);
  const [applyOpen, setApplyOpen] = useState(false);
  const [trialToApply, setTrialToApply] = useState("");
  const newWorkId = useId();
  const [panelError, setPanelError] = useState("");
  const [draftSkills, setDraftSkills] = useState<ExtractedSkillDraft[]>([]);
  const [draftBaseDigest, setDraftBaseDigest] = useState("");
  const [draftDirty, setDraftDirty] = useState(false);
  useEffect(() => { onDirtyChange?.(draftDirty || authorDirty); }, [draftDirty, authorDirty, onDirtyChange]);
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
  const [uploadProgress, setUploadProgress] = useState("");
  const [referenceNotice, setReferenceNotice] = useState("");
  const createLock = useRef(false);
  const uploadedFilesRef = useRef<File[]>([]);
  const uploadedIdsRef = useRef<string[]>([]);

  const resetCreateAttempt = () => { requestIdRef.current = null; uploadedFilesRef.current = []; uploadedIdsRef.current = []; setReferenceNotice(""); };
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
    if (!open || !activeJob) return;
    const item = inbox.items.find(entry => entry.id === activeJob.id);
    if (item && inbox.isUnseen(item)) inbox.markSeen(item);
  }, [open, activeJob?.id, activeJob?.digest, activeJob?.status, inbox.items, inbox.isUnseen, inbox.markSeen]);
  const calibrationId = activeJob?.calibration_id;
  const isCalibration = activeJob?.task_kind === "calibrate" || Boolean(calibrationId);
  const activeJobRef = useRef(activeJob);
  activeJobRef.current = activeJob;
  const calibration = useQuery({ queryKey: ["skill-calibration", calibrationId], queryFn: async ({ signal }) => {
    const incoming = await studioApi.skillCalibration(calibrationId!, signal);
    const cached = cache.getQueryData<SkillCalibration>(["skill-calibration", calibrationId]);
    const currentJob = activeJobRef.current;
    // A slow GET cannot undo a confirmed apply or replace the already loaded
    // current draft with an earlier operation's response.
    if (cached && (cached.status === "applied" && incoming.status !== "applied"
      || (cached.job.revision ?? 0) > (incoming.job.revision ?? 0)
      || currentJob && currentJob.calibration_id === calibrationId && cached.job.digest === currentJob.digest && incoming.job.digest !== currentJob.digest)) return cached;
    return incoming;
  }, enabled: Boolean(calibrationId), retry: false,
    refetchInterval: query => {
      const job = query.state.data?.job;
      return activeJob?.status === "running" || activeJob?.operation || job?.operation
        || job?.digest !== activeJob?.digest || job?.revision !== activeJob?.revision ? 2000 : false;
    } });
  useEffect(() => {
    if (calibrationId) void cache.invalidateQueries({ queryKey: ["skill-calibration", calibrationId] }, { cancelRefetch: true });
  }, [calibrationId, activeJob?.digest, activeJob?.revision, activeJob?.operation, activeJob?.status, cache]);
  const calibrationSeries = useQuery({ queryKey: ["series", calibration.data?.series_id], queryFn: () => studioApi.series(calibration.data!.series_id), enabled: Boolean(calibration.data?.series_id), retry: false });
  const eligibleTrials = (activeJob?.trials ?? []).filter(item => item.status === "completed" && item.digest === activeJob?.digest && item.cards.length > 0);
  const selectedApplyTrial = eligibleTrials.find(item => item.id === trialToApply) ?? eligibleTrials[eligibleTrials.length - 1];
  const applyRequestStorageKey = calibration.data && activeJob?.digest && selectedApplyTrial
    ? `creatoros:skill-calibration:apply:${calibration.data.id}:${activeJob.digest}:${selectedApplyTrial.id}` : "";
  let currentApplyRequestId: string | null = null;
  try { if (applyRequestStorageKey) currentApplyRequestId = sessionStorage.getItem(applyRequestStorageKey); }
  catch { /* Without a verified stored request, never offer to replace its key. */ }
  const apply = useMutation({
    retry: false,
    mutationFn: () => {
      if (!calibration.data || !activeJob?.digest || draftDirty || isStaleDraft || !selectedApplyTrial) throw new Error("请先保存草稿，并检查这版草稿的成功试产结果。");
      const key = applyRequestStorageKey;
      let requestId = sessionStorage.getItem(key);
      if (!requestId) { requestId = crypto.randomUUID().replaceAll("-", ""); sessionStorage.setItem(key, requestId); }
      return studioApi.applySkillCalibration(calibration.data.id, { request_id: requestId, expected_digest: activeJob.digest, expected_skill_digest: calibration.data.base_skill_digest, trial_id: selectedApplyTrial.id });
    },
    onSuccess: async result => {
      cache.setQueryData(["skill-calibration", result.id], result); cache.setQueryData(["skill-extraction", result.job_id], result.job);
      clearStoredDraft(result.job_id); setApplyOpen(false); setPanelError("");
      await Promise.all([cache.invalidateQueries({ queryKey: ["producer-skills"] }), cache.invalidateQueries({ queryKey: ["producer-skill-files"] }), cache.invalidateQueries({ queryKey: ["producer-skill-file"] }), cache.invalidateQueries({ queryKey: ["skill-extractions"] })]);
    },
    onError: () => { if (calibrationId) void cache.invalidateQueries({ queryKey: ["skill-calibration", calibrationId] }); },
  });
  useEffect(() => {
    if (calibration.data?.status === "applied" && activeJob?.status !== "saved") {
      cache.setQueryData(["skill-extraction", calibration.data.job_id], calibration.data.job);
      setApplyOpen(false); apply.reset();
    }
  }, [calibration.data?.status, calibration.data?.job_id, activeJob?.status, cache]);
  useEffect(() => { setActivityOpen(false); setNoteOpen(false); setDiffOpen(false); setApplyOpen(false); setTrialToApply(""); apply.reset(); }, [selectedId]);

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
      if (files.length > MAX_EXTRACTION_IMAGES) throw new Error("最多选择 20 张图片。");
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
        setUploadProgress(`正在上传 ${index + 1} / ${files.length}…`);
        const uploaded = await studioApi.uploadSkillExtractionImage({ name: file.name, data_base64: await toBase64(file) });
        uploadedIdsRef.current.push(uploaded.id);
      }
      setUploadProgress("正在提交提炼…");
      const uploadIds = [...new Set(uploadedIdsRef.current)];
      const duplicates = uploadedIdsRef.current.length - uploadIds.length;
      setReferenceNotice(duplicates ? `已合并 ${duplicates} 张重复图片，使用 ${uploadIds.length} 张参考图。` : "");
      return studioApi.createSkillExtraction({
        request_id: requestIdRef.current, upload_ids: uploadIds, source_text: text,
        mode, instruction: instruction.trim(),
      });
    },
    onSuccess: async job => {
      setFiles([]); setSourceText("");
      requestIdRef.current = null; uploadedFilesRef.current = []; uploadedIdsRef.current = []; setPanelError("");
      setNewWorkOpen(false);
      setSearchParams(previous => { const next = new URLSearchParams(previous); next.set("extraction", job.id); return next; });
      await Promise.all([cache.setQueryData(["skill-extraction", job.id], job), cache.invalidateQueries({ queryKey: ["skill-extractions"] })]);
    },
    onError: error => setPanelError(error.message),
    onSettled: () => { createLock.current = false; setUploadProgress(""); },
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
      if (job.calibration_id) void cache.invalidateQueries({ queryKey: ["skill-calibration", job.calibration_id] });
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
  const trialEligible = activeJob?.status === "ready" && (Boolean(calibrationId) || draftSkills.some(skill => skill.output_kind === "image-carousel" && skill.role !== "mind"));
  const isStaleDraft = Boolean(activeJob?.digest && draftBaseDigest && draftBaseDigest !== activeJob.digest);

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
  const resolveReference = (value: string | undefined) => {
    if (!value || value.startsWith("#") || /^(?:[a-z][a-z\d+.-]*:|\/\/)/i.test(value) || !selectedFile) return undefined;
    try {
      const path = decodeURIComponent(new URL(value, `https://skill.local/${selectedFile.path}`).pathname).slice(1);
      return jobFiles.find(file => file.role === selectedFile.role && file.path === path);
    } catch { return undefined; }
  };

  return <section className="artifact-extraction">
    <div className="extraction-entry-actions">
    <button type="button" className="button button-secondary extraction-toggle" aria-expanded={open} disabled={authorBusy}
      onClick={() => { setAuthorOpen(false); setOpen(value => !value); }}>{open ? "收起提炼" : "从作品提炼"}</button>
    <button type="button" className="button button-secondary extraction-toggle" aria-expanded={authorOpen} disabled={authorBusy || create.isPending}
      onClick={() => { if (!authorOpen) setOpen(false); setAuthorOpen(value => !value); }}>{authorOpen ? "收起编写" : "自己写 Skill"}</button>
    </div>
    <AuthorSkillForm visible={authorOpen} blocked={draftDirty || create.isPending} onDirtyChange={setAuthorDirty} onBusyChange={setAuthorBusy} onCreated={job => {
      cache.setQueryData(["skill-extraction", job.id], job);
      void cache.invalidateQueries({ queryKey: ["skill-extractions"] });
      setAuthorOpen(false); setOpen(true); setNewWorkOpen(false); setPanelError("");
      setSearchParams(previous => { const next = new URLSearchParams(previous); next.set("extraction", job.id); return next; });
    }} />
    {open && <div className="extraction-panel">
      {activeJob?.task_kind === "merge" && <h2>Skill 融合草稿</h2>}
      <div className="extraction-new-work">
        <button type="button" className="extraction-new-trigger" aria-expanded={newWorkOpen} aria-controls={newWorkId}
          onClick={() => setNewWorkOpen(value => !value)}>新作品<span>{newWorkOpen ? "收起" : "添加"}</span></button>
        <form id={newWorkId} aria-label="从作品提炼" hidden={!newWorkOpen} onSubmit={event => {
          event.preventDefault(); if (createLock.current) return;
          createLock.current = true; setPanelError(""); create.mutate();
        }}>
        <fieldset disabled={create.isPending}>
          <ExtractionImagePicker files={files} disabled={create.isPending} onError={setPanelError}
            onChange={next => { if (!createLock.current) { resetCreateAttempt(); setFiles(next); } }} />
          <label className="extraction-label">参考文案（可选）
            <textarea aria-label="参考文案" rows={5} maxLength={20000} value={sourceText}
              onChange={event => { resetCreateAttempt(); setSourceText(event.target.value); }} placeholder="粘贴作品文案" />
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
          {create.isPending ? uploadProgress || "上传并提交中…" : "开始提炼"}
        </button>
        </form>
      </div>
      {referenceNotice && <p role="status" className="extraction-help">{referenceNotice}</p>}
      {panelError && <p role="alert" className="extraction-error">{panelError}</p>}
      {create.isError && !panelError && <p role="alert" className="extraction-error">{create.error.message}</p>}
      {jobs.isError && <p role="alert" className="extraction-error">任务列表读取失败：{jobs.error.message}</p>}
      {jobs.data?.items.length ? <div className="extraction-jobs">
        <button type="button" className="extraction-section-toggle" aria-expanded={jobsOpen} onClick={() => setJobsOpen(value => !value)}>任务记录 · {jobs.data.items.length}<span>{jobsOpen ? "收起" : "展开"}</span></button>
        {jobsOpen && jobs.data.items.map(job => <button type="button" key={job.id} className={`extraction-job ${selectedId === job.id ? "selected" : ""}`}
          onClick={() => selectJob(job.id)}>
          <span>{job.task_kind === "calibrate" ? `校准：${job.skills[0]?.name || "Skill"}` : job.task_kind === "merge" ? `融合：${job.source_skills?.map(skill => skill.name).join(" + ") || "Skill"}` : job.task_kind === "author" ? `自写：${job.skills[0]?.name || "Skill 草稿"}` : job.uploads.map(upload => upload.name).join("、") || job.source_text?.slice(0, 45) || "参考作品"}</span><strong className={job.status === "running" ? "task-running-text" : undefined}>{statusLabels[job.status] ?? "状态更新"}</strong>
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
        {isCalibration && <section className="extraction-calibration" aria-label="Skill 校准对照">
          <div className="extraction-status"><strong>Skill 修改草稿</strong>{calibration.data?.status === "applied" ? <span>已确认更新</span> : <span>共享 Skill 尚未修改</span>}</div>
          {calibration.isError && <p role="alert" className="extraction-error">校准记录读取失败：{calibration.error.message}</p>}
          {calibration.data && <>
            <button className="extraction-section-toggle" type="button" aria-expanded={diffOpen} onClick={() => setDiffOpen(value => !value)}>修改差异与依据<span>{diffOpen ? "收起" : "查看"}</span></button>
            {diffOpen && <div className="extraction-calibration-evidence"><pre aria-label="Skill 修改差异">{calibration.data.diff || "草稿正在生成，暂没有差异。"}</pre>
              {calibration.data.artifacts.map((item, index) => <div key={`artifact-${index}`}><p><Link to={`/runs/${encodeURIComponent(item.run_id)}?revision=${encodeURIComponent(item.revision_id)}`}>{item.topic_title || "原产物"} · {item.order === null ? "整组" : `第 ${item.order} 张`}</Link> · {item.rating === "good" ? "好在哪里" : "需要改进"}：{item.comment}</p>
                {!!item.cards?.length && <div className="extraction-evidence-images">{item.cards.map(card => <a key={card.order} href={apiUrl(card.url)} target="_blank" rel="noreferrer"><img src={apiUrl(card.url)} alt={`原产物第 ${card.order} 张`} /></a>)}</div>}</div>)}
              {calibration.data.references.map((item, index) => <div key={`reference-${index}`}><p>参考作品 {index + 1} · {item.rating === "good" ? "好在哪里" : "需要改进"}：{item.comment}</p>{item.url && <div className="extraction-evidence-images"><a href={apiUrl(item.url)} target="_blank" rel="noreferrer"><img src={apiUrl(item.url)} alt={`参考作品 ${index + 1}`} /></a></div>}</div>)}
            </div>}
          </>}
        </section>}
        {draftStorageError && <p role="alert" className="extraction-error">{draftStorageError}</p>}
        <div className="extraction-status"><strong>任务状态：<span className={activeJob.status === "running" ? "task-running-text" : undefined}>{statusLabels[activeJob.status] ?? "状态更新"}</span></strong>
          {activeJob.operation && <span>当前操作：{activeJob.operation === "calibrate" ? "校准" : activeJob.operation === "extract" ? (activeJob.task_kind === "merge" ? "融合" : "提炼") : activeJob.operation === "revise" ? "改稿" : "试产"}</span>}
          {activeJob.note && activeJob.note.length <= 180 && <span>{activeJob.note}</span>}
        </div>
        {activeJob.note && activeJob.note.length > 180 && <div>
          <button type="button" className="extraction-new-trigger" aria-expanded={noteOpen} aria-controls={noteId}
            onClick={() => setNoteOpen(value => !value)}>修改说明<span>{noteOpen ? "收起" : "展开"}</span></button>
          <p id={noteId} className="extraction-help" hidden={!noteOpen}>{activeJob.note}</p>
        </div>}
        {activeJob.task_kind === "merge" && <p className="extraction-help">{activeJob.source_skills?.map(skill => skill.name).join(" + ")}</p>}
        {activeJob.observation_warning && <p role="status" className="extraction-error">{activeJob.observation_warning}</p>}
        {activeJob.progress && !activeJob.observation_warning && <p className="extraction-help">最近活动：{progressActivityLabels[activeJob.progress.activity] ?? "状态已更新"}</p>}
        {activeJob.error && <p role="alert" className="extraction-error">{activeJob.error}</p>}
        <button type="button" className="extraction-section-toggle" aria-expanded={activityOpen} onClick={() => setActivityOpen(value => !value)}>执行过程<span>{activityOpen ? "收起" : "展开"}</span></button>
        {activityOpen && <ExtractionActivity key={activeJob.id} jobId={activeJob.id}
          active={activeJob.status === "running" || Boolean(activeJob.operation)} />}
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
                  onClick={() => selectFile(file)}>{file.kind === "image" ? <FileImage size={14} /> : <FileText size={14} />}<span>{file.path}</span></button>) : <button type="button" className="extraction-file selected"
                  onClick={() => { setActiveFileKey(""); setViewerMode("preview"); }}><FileText size={14} /><span>SKILL.md</span></button>}
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
                  <label>Skill 名称
                    <input aria-label={`${roleLabels[selectedSkill.role]} Frontmatter 名称`} value={selectedSkill.name}
                      onChange={event => editSkill(selectedSkillIndex, {
                        name: event.target.value, skill_md: withFrontmatterName(selectedSkill.skill_md, event.target.value),
                      })} disabled={isCalibration || Boolean(activeJob.operation) || create.isPending} title={isCalibration ? "校准保留原 Skill 标识" : undefined} />
                  </label>
                  <label>目标产物
                    <select aria-label={`${roleLabels[selectedSkill.role]} 目标产物`} value={selectedSkill.output_kind ?? "text"} disabled={Boolean(activeJob.operation) || create.isPending}
                      onChange={event => editSkill(selectedSkillIndex, { output_kind: event.target.value as "image-carousel" | "text" })}>
                      <option value="image-carousel">图片轮播</option><option value="text">纯文本</option>
                    </select>
                  </label>
                </div>}
                {viewerMode === "edit" && activeJob.status === "ready" ? <textarea className="extraction-markdown-editor" aria-label="编辑 SKILL.md" disabled={Boolean(activeJob.operation) || create.isPending}
                  value={selectedSkill.skill_md} onChange={event => editSkill(selectedSkillIndex, { skill_md: event.target.value })} />
                  : <div className="extraction-markdown">
                    {selectedSkillDocument?.frontmatter && <pre className="extraction-frontmatter">{selectedSkillDocument.frontmatter}</pre>}
                    <Markdown remarkPlugins={[remarkGfm]} skipHtml components={{
                      img: ({ src, alt }) => {
                        const asset = resolveReference(src);
                        return asset?.kind === "image" ? <img className="extraction-markdown-image" src={apiUrl(asset.url)} alt={alt || asset.path} />
                          : <span>{alt || "图片"}（参考文件不可用）</span>;
                      },
                      a: ({ href, children }) => {
                        const file = resolveReference(href);
                        return file ? <button type="button" className="extraction-markdown-link" onClick={() => selectFile(file)}>{children}</button>
                          : href && /^https?:\/\//i.test(href) ? <a href={href} target="_blank" rel="noopener noreferrer">{children}</a> : <span>{children}</span>;
                      },
                    }}>{selectedSkillDocument?.body ?? selectedSkill.skill_md}</Markdown>
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
            {!isCalibration && <button type="button" className="button button-primary" disabled={!activeJob.digest || draftDirty || isStaleDraft || save.isPending || Boolean(activeJob.operation) || create.isPending}
              title={draftDirty ? "请先保存草稿" : undefined} onClick={() => save.mutate()}>{save.isPending ? "加入中…" : "确认加入 Skill 库"}</button>
            }
            {isCalibration && calibration.data?.status !== "applied" && <button type="button" className="button button-primary" disabled={!selectedApplyTrial || draftDirty || isStaleDraft || Boolean(activeJob.operation) || apply.isPending || !calibration.data}
              title={!selectedApplyTrial ? "先试用当前草稿并检查结果" : undefined} onClick={() => setApplyOpen(true)}>确认更新当前 Skill</button>}
          </div>
          {updateDraft.isError && <p role="alert" className="extraction-error">草稿保存失败：{updateDraft.error.message}</p>}
          {save.isError && <p role="alert" className="extraction-error">入库失败：{save.error.message}</p>}
          {isCalibration && applyOpen && <div className="extraction-apply-confirm" role="group" aria-label="确认更新共享 Skill">
            <strong>保存到原 Skill，以下栏目将使用新版</strong><p>{calibration.data?.affected_series.map(item => item.name).join("、") || "暂无绑定栏目"}</p>
            <p>旧 Skill 备份和已有产物保留。</p>
            {eligibleTrials.length > 1 && <label>验收的试产结果<select aria-label="验收的试产结果" value={selectedApplyTrial?.id || ""} onChange={event => setTrialToApply(event.target.value)}>{eligibleTrials.map((item, index) => <option key={item.id} value={item.id}>试产 {index + 1} · {item.topic}</option>)}</select></label>}
            <div><button type="button" className="button button-primary" disabled={apply.isPending || draftDirty || isStaleDraft || !selectedApplyTrial || Boolean(activeJob.operation)} onClick={() => apply.mutate()}>{apply.isPending ? "正在保存…" : "试用满意，确认保存"}</button><button type="button" className="button button-secondary" disabled={apply.isPending} onClick={() => setApplyOpen(false)}>先不保存</button></div>
          </div>}
          {apply.isError && <p role="alert" className="extraction-error">保存未确认：{apply.error.message} 请先刷新核对；原版已变化时，需要重新校准，不会自动覆盖。</p>}
          {apply.isError && calibration.data?.status === "ready" && calibration.data.apply_phase === "rolled_back" && currentApplyRequestId && calibration.data.apply_request_id === currentApplyRequestId && !activeJob.operation && <div className="extraction-rollback-retry" role="group" aria-label="重新确认已回滚的保存">
            <p>原 Skill 已恢复，可以重新确认保存。</p>
            <button type="button" className="button button-secondary" disabled={!applyRequestStorageKey || draftDirty || isStaleDraft} onClick={() => {
              try { sessionStorage.removeItem(applyRequestStorageKey); }
              catch { setPanelError("无法清除旧保存请求，请保留草稿并检查浏览器存储。"); return; }
              apply.reset(); setApplyOpen(true);
            }}>重新确认保存</button>
          </div>}
          <label className="extraction-label">要求 Codex 修改草稿
            <textarea aria-label="要求 Codex 修改草稿" rows={2} maxLength={4000} value={reviseInstruction} disabled={create.isPending} onChange={event => { setReviseInstruction(event.target.value); resetReviseAttempt(); }}
              placeholder="说明要保留或调整的规则" />
          </label>
          <button type="button" className="button button-secondary" disabled={draftDirty || isStaleDraft || revise.isPending || Boolean(activeJob.operation) || create.isPending || !reviseInstruction.trim()}
            title={draftDirty ? "请先保存草稿" : undefined} onClick={() => revise.mutate()}>{revise.isPending ? "改稿中…" : "让 Codex 改稿"}</button>
          {revise.isError && <p role="alert" className="extraction-error">改稿失败：{revise.error.message}</p>}
          <div className="extraction-trial-controls">
            {isCalibration && <p className="extraction-help">新线程试用，不进入正式队列。{draftSkills[0]?.role === "mind" ? "候选 Mind 搭配栏目原 Visualize。" : draftSkills[0]?.role === "production" && calibrationSeries.data?.mind_skill_id ? "候选 Visualize 搭配栏目原 Mind。" : ""}</p>}
            {isCalibration && !selectedApplyTrial && <p className="extraction-help">先检查当前草稿的试产结果，再确认更新。</p>}
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
          <strong>{isCalibration ? "已保存到原 Skill" : "已加入 CreatorOS Skill 库"}</strong>
          {activeJob.saved_skills.map(skill => <p key={skill.id}>{isCalibration
            ? <Link to={`/skills?skill=${encodeURIComponent(skill.id)}`}>{skill.display_name ?? skill.name}</Link>
            : <>{skill.name}{skill.local_path ? <> · 本地路径：<code>{skill.local_path}</code></> : ""}</>}</p>)}
          {!isCalibration && onUseSkill && activeJob.saved_skills.map(skill => <button type="button" className="button button-secondary" key={`use-${skill.id}`} onClick={() => onUseSkill(skill)}>用「{skill.name}」创建栏目</button>)}
        </div>}
        {(activeJob.trials ?? []).length > 0 && <div className="extraction-trials">
          <h3>试产结果</h3>
          {(activeJob.trials ?? []).map(item => <article className="extraction-trial" key={item.id}>
            <div className="extraction-status"><strong>{trialStatusLabels[item.status] ?? item.status}</strong><span>选题：{item.topic}</span>
              {item.digest !== activeJob.digest && <span className="extraction-old-digest">旧草稿结果</span>}</div>
            {item.progress && <p className="extraction-help">最近活动：{progressActivityLabels[item.progress.activity] ?? "状态已更新"}</p>}
            {item.error && <p role="alert" className="extraction-error">{item.error}</p>}
            {item.cards.length > 0 && <div className="extraction-trial-cards">{item.cards.map(card => <figure key={card.order}>
              <img src={apiUrl(card.url)} alt={`试产第 ${card.order} 张图片`} />
              <figcaption>第 {card.order} 张<TrialPrompt prompt={card.image_prompt} /></figcaption>
            </figure>)}</div>}
          </article>)}
        </div>}
      </div>}
    </div>}
  </section>;
}

function TrialPrompt({ prompt }: { prompt: string | null }) {
  const [open, setOpen] = useState(false);
  const contentId = useId();
  return <div className="extraction-trial-prompt">
    <button type="button" aria-expanded={open} aria-controls={contentId} onClick={() => setOpen(value => !value)}>{open ? "收起提示词" : "生图提示词"}</button>
    <pre id={contentId} hidden={!open}>{prompt || "无提示词记录"}</pre>
  </div>;
}
