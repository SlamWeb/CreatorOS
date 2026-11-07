import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { Image as ImageIcon, Package, Clapperboard, SlidersHorizontal, ShieldCheck, Layers } from "lucide-react";
import { studioApi, request } from "../api/client";
import { useCreatorDirectory } from "../api/useCreatorDirectory";
import type { ProducerSkillItem } from "../api/types";
import { ErrorState, LoadingState } from "../components/PageState";
import { ArtifactSkillExtraction } from "../components/ArtifactSkillExtraction";
import { SkillInspector } from "../components/SkillInspector";
import { Button } from "../components/ui/button";
import { WorkspaceDialog } from "../components/WorkspaceDialog";
import { SkillIterationHistory, SkillIterationPanel } from "../components/SkillIterationPanel";
import { UnseenDot, useWorkInbox } from "../components/TaskInbox";
import "./studio-space.css";

type DragPayload = { kind: "skill"; skill: ProducerSkillItem };
type SkillCategory = "content" | "visual" | "processing" | "rules";
type CategoryRecord = { family_id: string; categories: SkillCategory[]; revision: number };
type CategoryEditor = { skill: ProducerSkillItem; categories: SkillCategory[]; revision: number; needsCheck: boolean; latest?: CategoryRecord };
const categoryOptions: { value: SkillCategory; label: string }[] = [
  { value: "content", label: "内容创作" }, { value: "visual", label: "视觉表现" },
  { value: "processing", label: "素材处理" }, { value: "rules", label: "规则约束" },
];
function validCategoryRecord(value: unknown): value is CategoryRecord {
  if (!value || typeof value !== "object") return false;
  const record = value as Record<string, unknown>;
  return typeof record.family_id === "string" && !!record.family_id && Number.isInteger(record.revision)
    && (record.revision as number) >= 0 && Array.isArray(record.categories)
    && record.categories.every(item => categoryOptions.some(option => option.value === item))
    && new Set(record.categories).size === record.categories.length;
}
function emptyCategory(familyId: string): CategoryRecord { return { family_id: familyId, categories: [], revision: 0 }; }


const composerKey = "creatoros:skill-composer:v1";
type ComposePending = { signature: string; id: string };
type ComposerDraft = { ids: string[]; name: string; creatorId: string; instruction: string; mode: "reference" | "merge";
  composePending?: ComposePending; pending?: { request_id: string; skill_ids: string[]; instruction: string } };
function restoreComposer(): ComposerDraft {
  try {
    const data = JSON.parse(sessionStorage.getItem(composerKey) ?? "null");
    if (data && Array.isArray(data.ids) && data.ids.every((id: unknown) => typeof id === "string")) {
      return { ids: data.ids.slice(0, 8), name: String(data.name ?? ""), creatorId: String(data.creatorId ?? ""),
        instruction: String(data.instruction ?? ""), mode: data.mode === "merge" ? "merge" : "reference",
        pending: data.pending, composePending: data.composePending };
    }
  } catch { /* No stored draft. */ }
  return { ids: [], name: "", creatorId: "", instruction: "", mode: "reference" };
}

function directBinding(ids: string[], skills: ProducerSkillItem[]) {
  if (ids.some(id => !skills.some(skill => skill.id === id))) return null;
  if (ids.length === 1) return { skill_name: ids[0] };
  if (ids.length !== 2) return null;
  const mind = skills.find(skill => ids.includes(skill.id) && skill.role === "mind");
  const visual = skills.find(skill => ids.includes(skill.id) && skill.role === "production");
  return mind && visual ? { mind_skill_id: mind.id, production_skill_id: visual.id } : null;
}

function categoryIcon(categories: SkillCategory[]) {
  if (categories.length > 1) return <Layers size={17} strokeWidth={1.7} />;
  if (categories[0] === "content") return <Clapperboard size={17} strokeWidth={1.7} />;
  if (categories[0] === "visual") return <ImageIcon size={17} strokeWidth={1.7} />;
  if (categories[0] === "processing") return <SlidersHorizontal size={17} strokeWidth={1.7} />;
  if (categories[0] === "rules") return <ShieldCheck size={17} strokeWidth={1.7} />;
  return <Package size={17} strokeWidth={1.7} />;
}

function skillLabel(skill: ProducerSkillItem) { return skill.display_name ?? skill.name; }

export function SkillsPage() {
  const client = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const inbox = useWorkInbox();
  const [initial] = useState(restoreComposer);
  const skills = useQuery({ queryKey: ["producer-skills"], queryFn: studioApi.producerSkills });
  const creators = useCreatorDirectory();
  const categoryQuery = useQuery({ queryKey: ["skill-display-categories"], retry: false,
    queryFn: async () => {
      const value = await request<{ items: unknown[] }>("/api/skill-display-categories");
      if (!value || !Array.isArray(value.items) || !value.items.every(validCategoryRecord)
          || new Set(value.items.map(item => (item as CategoryRecord).family_id)).size !== value.items.length) {
        throw new Error("分类数据格式不正确，请重试读取。");
      }
      return { items: value.items as CategoryRecord[] };
    } });
  const [categoryEditor, setCategoryEditor] = useState<CategoryEditor | null>(null);
  const categorySave = useMutation({ retry: false,
    mutationFn: async (input: CategoryEditor) => {
      const result = await request<unknown>(`/api/skill-display-categories/${encodeURIComponent(input.skill.id)}`, {
        method: "PUT", body: JSON.stringify({ categories: input.categories, expected_revision: input.revision }),
      });
      const familyId = input.skill.family_id ?? input.skill.id;
      if (!validCategoryRecord(result) || result.family_id !== familyId || result.revision !== input.revision + 1
          || result.categories.length !== input.categories.length || !input.categories.every(category => result.categories.includes(category))) {
        throw new Error("保存结果无法确认，请先核对最新分类。");
      }
      return result;
    },
    onSuccess: result => {
      client.setQueryData<{ items: CategoryRecord[] }>(["skill-display-categories"], current => ({ items: [
        ...(current?.items ?? []).filter(item => item.family_id !== result.family_id), result,
      ] }));
      setCategoryEditor(null);
    },
    onError: () => setCategoryEditor(current => current ? { ...current, needsCheck: true, latest: undefined } : null),
  });
  const [selectedIds, setSelectedIds] = useState<string[]>(initial.ids);
  const [composerOpen, setComposerOpen] = useState(initial.ids.length > 0);
  const [name, setName] = useState(initial.name);
  const [creatorId, setCreatorId] = useState(initial.creatorId);
  const [mergeInstruction, setMergeInstruction] = useState(initial.instruction);
  const [mode, setMode] = useState(initial.mode);
  const [workbenchDirty, setWorkbenchDirty] = useState(false);
  const [storageError, setStorageError] = useState("");
  const mergeRequest = useRef<ComposerDraft["pending"]>(initial.pending);
  const composeRequest = useRef<ComposePending | null>(initial.composePending ?? null);
  const [notice, setNotice] = useState<{ kind: "ok" | "error"; text: string } | null>(null);
  const [inspected, setInspected] = useState<ProducerSkillItem | null>(null);
  const openedSkill = useRef<string | null>(null);
  const viewedSkill = useRef<string | null>(null);
  const verifiedSkills = useRef(new Set<string>());
  const requestedSkill = searchParams.get("skill");
  const requestedIteration = searchParams.get("iteration");
  const requestedVersioned = !!requestedSkill && requestedSkill.includes("--");
  const requestedVersions = useQuery({ queryKey: ["producer-skill-versions", requestedSkill, "global"],
    queryFn: ({ signal }) => studioApi.producerSkillVersions(requestedSkill!, signal),
    enabled: requestedVersioned && !requestedIteration, retry: false });
  useEffect(() => {
    if (!requestedSkill || openedSkill.current === requestedSkill) return;
    const item = requestedVersioned ? requestedVersions.data?.items.find(skill => skill.id === requestedSkill)
      : skills.data?.items.find(skill => skill.id === requestedSkill);
    if (item) { openedSkill.current = requestedSkill; setInspected(item); }
  }, [requestedSkill, requestedVersioned, skills.data, requestedVersions.data]);
  useEffect(() => {
    if (!inspected) { viewedSkill.current = null; return; }
    if (inbox.data && viewedSkill.current !== inspected.id) {
      viewedSkill.current = inspected.id;
      inbox.markObjectSeen("skill", inspected.id);
    }
  }, [inspected?.id, inbox.data]);
  const closeInspector = () => {
    setInspected(null);
    if (requestedSkill) setSearchParams(previous => { const next = new URLSearchParams(previous); next.delete("skill"); return next; });
    openedSkill.current = null;
  };
  const inspectSkill = (skill: ProducerSkillItem) => {
    openedSkill.current = skill.id;
    setInspected(skill);
    setSearchParams(previous => { const next = new URLSearchParams(previous); next.set("skill", skill.id); return next; }, { replace: true });
  };
  const [drag, setDrag] = useState<{ payload: DragPayload; x: number; y: number; target: string | null } | null>(null);
  const dragRef = useRef<{ payload: DragPayload; startX: number; startY: number; active: boolean } | null>(null);
  const dragCleanup = useRef<(() => void) | null>(null);
  const suppressClick = useRef(false);
  useEffect(() => () => dragCleanup.current?.(), []);
  const storeComposer = () => {
    try {
      sessionStorage.setItem(composerKey, JSON.stringify({ ids: selectedIds, name, creatorId, instruction: mergeInstruction,
        mode, pending: mergeRequest.current, composePending: composeRequest.current }));
      setStorageError("");
    } catch { setStorageError("浏览器无法暂存组合；离开或刷新会丢失尚未提交的选择。"); }
  };
  useEffect(storeComposer, [selectedIds, name, creatorId, mergeInstruction, mode]);
  const addSkill = (skill: ProducerSkillItem) => {
    setNotice(null);
    if (compose.isPending || merge.isPending) return;
    if (selectedIds.includes(skill.id)) return;
    if (selectedIds.length >= 8) { setNotice({ kind: "error", text: "一次最多选择 8 个 Skill。" }); return; }
    setSelectedIds(current => [...current, skill.id]);
  };

  const compose = useMutation({
    retry: false,
    mutationFn: () => {
      const binding = directBinding(selectedIds, skills.data?.items ?? []);
      if (!binding || !name.trim()) throw new Error("请选择一个完整 Skill 或 Mind＋Visualize，并填写栏目名称。");
      const input = { name: name.trim(), description: "", audience: "", creator_id: creatorId || null, ...binding };
      const signature = JSON.stringify(input);
      if (composeRequest.current?.signature !== signature) composeRequest.current = { signature, id: crypto.randomUUID().replaceAll("-", "") };
      storeComposer();
      return studioApi.composeSeries({ ...input, request_id: composeRequest.current!.id });
    },
    onSuccess: async (result) => {
      setSelectedIds([]); setName(""); setCreatorId(""); composeRequest.current = null;
      setNotice({ kind: "ok", text: `已创建「${result.series.name}」。` });
      await Promise.all([
        client.invalidateQueries({ queryKey: ["series-all"] }),
        client.invalidateQueries({ queryKey: ["creators"] }),
      ]);
    },
    onError: (error) => setNotice({ kind: "error", text: error.message }),
  });

  const merge = useMutation({
    retry: false,
    mutationFn: () => {
      if (workbenchDirty) throw new Error("请先保存或丢弃工作台中尚未保存的草稿。");
      if (selectedIds.length < 2) throw new Error("请选择至少两个 Skill。");
      const value = { skill_ids: selectedIds, instruction: mergeInstruction.trim() };
      if (JSON.stringify({ skill_ids: mergeRequest.current?.skill_ids, instruction: mergeRequest.current?.instruction }) !== JSON.stringify(value)) {
        mergeRequest.current = { ...value, request_id: crypto.randomUUID().replaceAll("-", "") };
      }
      storeComposer();
      return studioApi.mergeSkills(mergeRequest.current!);
    },
    onSuccess: async job => {
      client.setQueryData(["skill-extraction", job.id], job);
      setSearchParams(previous => { const next = new URLSearchParams(previous); next.set("extraction", job.id); return next; });
      mergeRequest.current = undefined; storeComposer();
      setNotice({ kind: "ok", text: "融合任务已提交。" });
      await client.invalidateQueries({ queryKey: ["skill-extractions"] });
    },
    onError: error => setNotice({ kind: "error", text: error.message }),
  });

  const [installUrl, setInstallUrl] = useState("");
  const [installRole, setInstallRole] = useState<"mind" | "production" | "">("");
  const [jobId, setJobId] = useState<string | null>(null);
  const install = useMutation({
    retry: false,
    mutationFn: () => request<{ id: string }>("/api/producer-skills/install", {
      method: "POST", body: JSON.stringify({ github_url: installUrl, role: installRole || null }),
    }),
    onSuccess: async (job) => {
      setJobId(job.id);
      await client.invalidateQueries({ queryKey: ["producer-skills"] });
    },
  });
  const job = useQuery({
    queryKey: ["skill-install", jobId], enabled: !!jobId, refetchInterval: q => ["queued", "installing"].includes(q.state.data?.status ?? "") ? 2000 : false,
    queryFn: () => request<{ status: string; message: string }>(`/api/producer-skills/jobs/${encodeURIComponent(jobId!)}`),
  });

  const validTarget = (_payload: DragPayload, target: string | null) => target === "skill-basket";

  const applyDrop = (payload: DragPayload, target: string) => {
    if (target === "skill-basket") addSkill(payload.skill);
  };

  const onDragStart = (payload: DragPayload) => (event: React.PointerEvent) => {
    if (event.button !== 0) return;
    dragCleanup.current?.();
    dragRef.current = { payload, startX: event.clientX, startY: event.clientY, active: false };
    let pointerX = event.clientX, pointerY = event.clientY;
    let scrollFrame: number | null = null;
    const cleanup = () => {
      if (scrollFrame !== null) window.cancelAnimationFrame(scrollFrame);
      scrollFrame = null;
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      window.removeEventListener("pointercancel", cancel);
      window.removeEventListener("keydown", esc);
      dragCleanup.current = null;
    };
    const updateDrag = () => {
      const state = dragRef.current;
      if (!state?.active) return;
      const target = document.elementFromPoint(pointerX, pointerY)?.closest("[data-drop]")?.getAttribute("data-drop") ?? null;
      setDrag({ payload: state.payload, x: pointerX, y: pointerY, target: validTarget(state.payload, target) ? target : null });
    };
    const scrollNearEdge = () => {
      scrollFrame = null;
      if (!dragRef.current?.active) return;
      const distance = pointerY < 48 ? -20 : pointerY > window.innerHeight - 48 ? 20 : 0;
      if (!distance) return;
      const before = window.scrollY;
      window.scrollBy(0, distance);
      if (window.scrollY !== before) {
        updateDrag();
        scrollFrame = window.requestAnimationFrame(scrollNearEdge);
      }
    };
    const move = (e: PointerEvent) => {
      const state = dragRef.current;
      if (!state) return;
      if (!state.active && Math.hypot(e.clientX - state.startX, e.clientY - state.startY) < 6) return;
      state.active = true;
      pointerX = e.clientX; pointerY = e.clientY;
      updateDrag();
      if (scrollFrame === null) scrollFrame = window.requestAnimationFrame(scrollNearEdge);
    };
    const up = (e: PointerEvent) => {
      cleanup();
      const state = dragRef.current;
      dragRef.current = null;
      setDrag(null);
      if (!state) return;
      if (state.active) {
        suppressClick.current = true;
        const target = document.elementFromPoint(e.clientX, e.clientY)?.closest("[data-drop]")?.getAttribute("data-drop") ?? null;
        if (target && validTarget(state.payload, target)) applyDrop(state.payload, target);
      }
    };
    const cancel = () => {
      cleanup();
      suppressClick.current = Boolean(dragRef.current?.active);
      dragRef.current = null;
      setDrag(null);
    };
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") cancel(); };
    dragCleanup.current = cleanup;
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    window.addEventListener("pointercancel", cancel);
    window.addEventListener("keydown", esc);
  };

  const requestedItem = requestedVersioned ? requestedVersions.data?.items.find(item => item.id === requestedSkill)
    : skills.data?.items.find(item => item.id === requestedSkill);
  const targetUnavailable = requestedVersioned
    ? requestedVersions.isError || requestedVersions.isFetching || !requestedItem
    : skills.isError || skills.isFetching || !requestedItem;
  useEffect(() => {
    if (requestedSkill && requestedItem && !targetUnavailable) verifiedSkills.current.add(requestedSkill);
  }, [requestedSkill, requestedItem, targetUnavailable]);
  // A manual selection updates both local state and the URL. Wait until they
  // agree before mounting, otherwise the inspector can mount twice while the
  // URL catches up and silently repeat a failed file request.
  const verifiedInspector = inspected && requestedSkill && inspected.id === requestedSkill
    && (requestedItem || verifiedSkills.current.has(requestedSkill)) ? inspected : null;
  const inspector = verifiedInspector && <SkillInspector key={verifiedInspector.id} skill={verifiedInspector} onClose={closeInspector}
    onSelectSkill={inspectSkill}
    scopeUnavailable={targetUnavailable} onScopeRetry={() => void (requestedVersioned ? requestedVersions.refetch() : skills.refetch())}
    onDeleted={() => { setSelectedIds(ids => ids.filter(id => id !== verifiedInspector.id)); closeInspector(); }}
    onAdd={() => { addSkill(verifiedInspector); closeInspector(); }} />;
  const targetFailed = !!requestedSkill && !verifiedInspector && (requestedVersioned
    ? requestedVersions.isError || (requestedVersions.isSuccess && !requestedItem)
    : !!skills.data && !requestedItem);
  const targetLoading = !!requestedSkill && !verifiedInspector && !targetFailed
    && (requestedVersioned ? requestedVersions.isPending : skills.isPending);
  const targetProgress = targetLoading && <p className="space-note" role="status">正在读取指定 Skill…</p>;
  const targetError = targetFailed && <div className="state-panel error-state" role="alert">
    <div><strong>无法读取指定 Skill</strong><p>{requestedVersions.error?.message ?? "未能确认链接中的版本，请重试。"}</p></div>
    <button type="button" className="button button-secondary" onClick={() => void (requestedVersioned ? requestedVersions.refetch() : skills.refetch())}>重试读取 Skill</button>
  </div>;

  if (requestedIteration) return <div className="space-page"><SkillIterationPanel key={requestedIteration} id={requestedIteration} /></div>;

  const allSkills = skills.data?.items ?? [];
  const queryText = searchParams.get("q") ?? "";
  const categoryParam = searchParams.get("category") ?? "all";
  const selectedCategory = ["all", "content", "visual", "processing", "rules", "uncategorized"].includes(categoryParam) ? categoryParam : "all";
  const categoriesAvailable = categoryQuery.isSuccess && !categoryQuery.isError;
  const categoryMap = new Map((categoryQuery.data?.items ?? []).map(item => [item.family_id, item]));
  const categoryFor = (skill: ProducerSkillItem) => categoryMap.get(skill.family_id ?? skill.id) ?? emptyCategory(skill.family_id ?? skill.id);
  const roleParam = searchParams.get("role") ?? "all";
  const selectedRole = ["all", "mind", "production", "other"].includes(roleParam) ? roleParam : "all";
  const visibleSkills = allSkills.filter(skill => {
    const roleMatch = selectedRole === "all" || (selectedRole === "other"
      ? skill.role === "legacy_end_to_end" || skill.role === null : skill.role === selectedRole);
    const term = queryText.trim().toLocaleLowerCase();
    const tags = categoryFor(skill).categories;
    const categoryMatch = selectedCategory === "all" || !categoriesAvailable || (selectedCategory === "uncategorized"
      ? tags.length === 0 : tags.includes(selectedCategory as SkillCategory));
    return roleMatch && categoryMatch && (!term || `${skillLabel(skill)} ${skill.name} ${skill.description ?? ""}`.toLocaleLowerCase().includes(term));
  });
  const setBrowseParam = (key: string, value: string) => setSearchParams(previous => {
    const next = new URLSearchParams(previous);
    if (!value || (["role", "category"].includes(key) && value === "all")) next.delete(key); else next.set(key, value);
    return next;
  }, { replace: true });
  const accounts = creators.items;
  const accountsLoading = creators.isPending || creators.isFetching;
  const accountsIncomplete = creators.isError;
  const selectedAccountMissing = !!creatorId && !accounts.some(account => account.id === creatorId);
  const accountSelectionUnavailable = creators.isPending || creators.isError;
  const selectedSkills = selectedIds.map(id => allSkills.find(skill => skill.id === id)).filter((skill): skill is ProducerSkillItem => Boolean(skill));
  const missingSkills = selectedSkills.length !== selectedIds.length;
  const busy = compose.isPending || merge.isPending;
  const binding = directBinding(selectedIds, allSkills);
  const isMerge = selectedIds.length > 1 && mode === "merge";
  const canCreate = !!binding && !!name.trim() && !busy && !missingSkills && !selectedAccountMissing && !accountSelectionUnavailable;
  const draggingSkill = drag?.payload.skill ?? null;

  const skillCard = (skill: ProducerSkillItem) => {
    const isSelected = selectedIds.includes(skill.id);
    const categories = categoriesAvailable ? categoryFor(skill).categories : [];
    const categoryNames = categories.map(value => categoryOptions.find(option => option.value === value)?.label ?? value);
    const categoryLabel = !categoriesAvailable ? "分类" : categoryNames.length ? `${categoryNames[0]}${categoryNames.length > 1 ? ` +${categoryNames.length - 1}` : ""}` : "待分类";
    return <div key={skill.id} className={`skill-card-wrap ${draggingSkill?.id === skill.id ? "drag-source" : ""}`}>
      <button type="button" className={`skill-card${busy ? " view-only" : ""}`} aria-label={skillLabel(skill)}
        onPointerDown={event => { suppressClick.current = false; if (!busy) onDragStart({ kind: "skill", skill })(event); }}
        onClick={event => { if (event.detail > 0 && suppressClick.current) { suppressClick.current = false; return; } inspectSkill(skill); }}
        aria-haspopup="dialog">
        <span className="skill-icon" aria-hidden="true">{categoryIcon(categories)}</span>
        <span className="skill-copy"><strong>{skillLabel(skill)}</strong>{skill.description && <span className="skill-description">{skill.description}</span>}</span>
        {inbox.hasUnseen("skill", skill.id) && <UnseenDot label="Skill 有未查看更新" />}
      </button>
      <Button type="button" variant="ghost" size="sm" className="skill-categorize" aria-label={`设置 ${skillLabel(skill)} 分类`} aria-description={categoryNames.join("、") || categoryLabel} title={categoryNames.join("、") || categoryLabel}
        disabled={!categoriesAvailable} onClick={() => {
          categorySave.reset();
          const current = categoryFor(skill);
          setCategoryEditor({ skill, categories: [...current.categories], revision: current.revision, needsCheck: false });
        }}>{categoryLabel}</Button>
      <Button type="button" variant="ghost" size="sm" className="skill-select" aria-label={`${isSelected ? "取消选择" : "加入"} ${skillLabel(skill)}`}
        aria-pressed={isSelected} disabled={busy} onClick={() => isSelected ? setSelectedIds(ids => ids.filter(id => id !== skill.id)) : addSkill(skill)}>
        {isSelected ? "已选" : "加入"}
      </Button>
    </div>;
  };

  return <div className="space-page skill-page">
    {targetProgress}
    {targetError}
    {skills.isPending ? <LoadingState label="正在读取 Skill 库…" />
      : skills.isError ? <ErrorState message={skills.error.message} onRetry={() => void skills.refetch()} /> : <>
    <header className="space-head"><h1>Skill 库</h1></header>
    <SkillIterationHistory />
    <ArtifactSkillExtraction onDirtyChange={setWorkbenchDirty} onUseSkill={skill => {
      setSelectedIds([skill.id]); setComposerOpen(true); setMode("reference"); setNotice({ kind: "ok", text: `已选择「${skillLabel(skill)}」，填写栏目名称后创建。` });
    }} />
    <details className="skill-install-disclosure"><summary>从 GitHub 安装</summary>
        <form className="skill-install" onSubmit={event => { event.preventDefault(); if (installUrl.trim()) install.mutate(); }}>
          <input aria-label="GitHub Skill 链接" type="url" required placeholder="https://github.com/…"
            value={installUrl} onChange={event => setInstallUrl(event.target.value)} />
          <div>
            <select aria-label="Skill 角色" value={installRole} onChange={event => setInstallRole(event.target.value as typeof installRole)}>
              <option value="">暂不分类</option>
              <option value="mind">Mind</option>
              <option value="production">Visualize</option>
            </select>
            <button className="button button-secondary" disabled={!installUrl.trim() || install.isPending}>安装</button>
          </div>
          {install.isError && <p className="form-error" role="alert">{install.error.message}</p>}
          {job.data && <p className="space-note" role="status">{job.data.status} · {job.data.message}</p>}
        </form>
    </details>
    <div className="space-columns">
      <section className="skill-catalog" aria-label="Skill 列表">
        <div className="skill-catalog-controls">
          <input type="search" aria-label="搜索 Skill" placeholder="搜索 Skill…" value={queryText}
            onChange={event => setBrowseParam("q", event.target.value)} />
          <div className="skill-category-filters" role="group" aria-label="Skill 分类">
            {[{ value: "all", label: "全部" }, ...categoryOptions, { value: "uncategorized", label: "待分类" }].map(option =>
              <button type="button" key={option.value} className={selectedCategory === option.value ? "is-current" : ""}
                aria-pressed={selectedCategory === option.value} disabled={option.value !== "all" && !categoriesAvailable}
                onClick={() => setBrowseParam("category", option.value)}>{option.label}</button>)}
          </div>
        </div>
        {categoryQuery.isPending && <p className="skill-category-status" role="status">正在读取分类…</p>}
        {categoryQuery.isError && <p className="skill-category-status" role="alert">分类读取失败，当前显示全部 Skill。 <button type="button" onClick={() => void categoryQuery.refetch()}>重试读取</button></p>}
        {selectedRole !== "all" && <button type="button" className="skill-legacy-filter" onClick={() => setBrowseParam("role", "all")}>
          旧版筛选：{selectedRole === "mind" ? "Mind" : selectedRole === "production" ? "Visualize" : "其他"} · 清除
        </button>}
        <p className="skill-catalog-count" aria-live="polite">{visibleSkills.length} 项</p>
        <div className="skill-catalog-list">{visibleSkills.map(skill => skillCard(skill))}</div>
        {!visibleSkills.length && <p className="skill-catalog-empty">{allSkills.length ? "没有匹配的 Skill。" : "还没有 Skill。"}</p>}
      </section>

      <section className={`space-col skill-compose${composerOpen ? "" : " is-collapsed"}`} aria-label="组合栏目">
        <div className="space-col-head"><h2>组合栏目</h2><button type="button" className="skill-compose-toggle" aria-expanded={composerOpen} aria-controls="skill-composer-content" onClick={() => setComposerOpen(value => !value)}>{composerOpen ? "收起" : "展开"}</button></div>
        <div className="composer" id="skill-composer-content">
          <div data-drop="skill-basket" aria-label="Skill 组合框"
            className={`skill-basket ${draggingSkill ? (drag?.target === "skill-basket" ? "drop-ok" : "can-drop") : ""}`}>
            {!selectedIds.length && <p>把任意 Skill 拖到这里，或在详情中加入</p>}
            {selectedSkills.map(skill => <div className="basket-item" key={skill.id}>
              <button type="button" className="basket-remove" disabled={busy} onClick={() => setSelectedIds(ids => ids.filter(id => id !== skill.id))} aria-label={`移除 ${skillLabel(skill)}`}>
                {categoryIcon(categoriesAvailable ? categoryFor(skill).categories : [])}<strong>{skillLabel(skill)}</strong><span aria-hidden="true">×</span>
              </button>
              <button type="button" className="skill-slot-view" onClick={() => inspectSkill(skill)} aria-label={`查看 ${skillLabel(skill)}`}>查看</button>
            </div>)}
            {missingSkills && <p role="alert">部分 Skill 已不在库中。<button type="button" onClick={() => setSelectedIds(selectedSkills.map(s => s.id))}>移除失效选择</button></p>}
          </div>
          <form className="composer-form" onSubmit={event => { event.preventDefault(); setNotice(null); if (busy) return; if (isMerge) merge.mutate(); else if (canCreate) compose.mutate(); }}>
            {selectedIds.length > 1 && <fieldset className="composer-mode" disabled={busy} aria-label="Skill 使用方式">
              <label><input type="radio" name="skill-mode" value="reference" checked={mode === "reference"} onChange={() => setMode("reference")} />直接使用</label>
              <label><input type="radio" name="skill-mode" value="merge" checked={mode === "merge"} onChange={() => setMode("merge")} />融合成新 Skill</label>
            </fieldset>}
            {isMerge ? <label>融合要求（可选）<textarea rows={3} maxLength={4000} value={mergeInstruction} onChange={event => setMergeInstruction(event.target.value)} placeholder="遇到冲突时，你更希望保留什么？" /></label> : <>
            {selectedIds.length === 2 && binding && <p className="space-note">保留两份原始 Skill，由同一个 Codex 生产会话参考使用。</p>}
            {selectedIds.length > 1 && !binding && !missingSkills && <p className="space-note" role="status">直接使用需选择一份 Mind＋一份 Visualize；其他组合可选择融合。</p>}
            <fieldset disabled={busy} className="composer-fields">
            <label>栏目名称<input required maxLength={120} value={name} onChange={event => setName(event.target.value)} placeholder="例如：AI 概念图解" /></label>
            <label>归属账号<select value={creatorId} disabled={accountSelectionUnavailable} onChange={event => setCreatorId(event.target.value)}>
              <option value="">暂不分配</option>
              {selectedAccountMissing && <option value={creatorId} disabled>{accountsLoading ? "正在查找已选账号…" : "已选账号不在目录中"}</option>}
              {accounts.map(account => <option key={account.id} value={account.id}>{account.display_name}</option>)}
            </select></label>
            {accountsLoading && <p className="space-note" role="status">正在更新账号列表…</p>}
            {accountsIncomplete && <p className="space-error" role="alert"><span>账号列表读取失败</span> <button type="button"
              onClick={() => void creators.refetch()}>重试加载账号</button></p>}
            </fieldset></>}
            <button className="composer-create" disabled={isMerge ? busy || missingSkills || workbenchDirty : !canCreate}>
              {merge.isPending ? "提交中…" : compose.isPending ? "创建中…" : isMerge ? "生成融合草稿" : "创建栏目"}
            </button>
            {isMerge && workbenchDirty && <p role="alert">请先保存上方工作台的草稿修改。</p>}
          </form>
          {storageError && <p className="space-error" role="alert">{storageError}</p>}
          {notice?.kind === "error" && <p className="space-error" role="alert">{notice.text}</p>}
          {notice?.kind === "ok" && <p className="space-ok" role="status">{notice.text}</p>}
        </div>
      </section>
    </div>
    {drag && <div className={`drag-ghost ${drag.target ? "on-target" : ""}`} style={{ left: drag.x, top: drag.y }} aria-hidden="true">
      {skillLabel(drag.payload.skill)}
    </div>}
    </>}
    {categoryEditor && <WorkspaceDialog title="设置 Skill 分类" busy={categorySave.isPending} onClose={() => setCategoryEditor(null)}>
      <form className="skill-category-editor" onSubmit={event => { event.preventDefault(); if (!categoryEditor.needsCheck && !categorySave.isPending) categorySave.mutate(categoryEditor); }}>
        <p>{skillLabel(categoryEditor.skill)}</p>
        <fieldset disabled={categorySave.isPending}><legend>可多选；不选择则为待分类</legend>
          {categoryOptions.map((option, index) => <label key={option.value}>
            <input type="checkbox" data-initial-focus={index === 0 ? true : undefined} checked={categoryEditor.categories.includes(option.value)}
              onChange={event => setCategoryEditor(current => current ? { ...current,
                categories: event.target.checked ? [...current.categories, option.value] : current.categories.filter(item => item !== option.value) } : null)} />
            {option.label}
          </label>)}
        </fieldset>
        {categorySave.isError && <p className="form-error" role="alert">{categorySave.error.message} 当前选择已保留，请核对后再保存。</p>}
        {categoryEditor.needsCheck && <div className="skill-category-conflict">
          <button type="button" className="button button-secondary" disabled={categoryQuery.isFetching} onClick={async () => {
            const result = await categoryQuery.refetch();
            if (result.isSuccess && result.data) setCategoryEditor(current => current ? { ...current,
              latest: result.data.items.find(item => item.family_id === (current.skill.family_id ?? current.skill.id))
                ?? emptyCategory(current.skill.family_id ?? current.skill.id) } : null);
          }}>{categoryQuery.isFetching ? "正在核对…" : "核对最新分类"}</button>
          {categoryQuery.isError && <p role="alert">读取最新分类失败，请重试。</p>}
          {categoryEditor.latest && <div><p>最新分类：{categoryEditor.latest.categories.length
            ? categoryEditor.latest.categories.map(value => categoryOptions.find(option => option.value === value)?.label).join("、") : "待分类"}</p>
            <button type="button" className="button button-secondary" onClick={() => {
              setCategoryEditor(current => current?.latest ? { ...current, revision: current.latest.revision, needsCheck: false, latest: undefined } : current);
              categorySave.reset();
            }}>保留当前选择并采用最新版本</button></div>}
        </div>}
        <div className="skill-category-actions"><Button type="button" variant="secondary" disabled={categorySave.isPending} onClick={() => setCategoryEditor(null)}>取消</Button>
          <Button type="submit" disabled={categorySave.isPending || categoryEditor.needsCheck}>{categorySave.isPending ? "保存中…" : "保存分类"}</Button></div>
      </form>
    </WorkspaceDialog>}
    {inspector}
  </div>;
}
