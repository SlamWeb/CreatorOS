import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { BookOpen, Image as ImageIcon, Package, CircleHelp } from "lucide-react";
import { studioApi, request } from "../api/client";
import { useCreators } from "../api/hooks";
import type { ProducerSkillItem } from "../api/types";
import { ErrorState, LoadingState } from "../components/PageState";
import { ArtifactSkillExtraction } from "../components/ArtifactSkillExtraction";
import { SkillInspector } from "../components/SkillInspector";
import { UnseenDot, useWorkInbox } from "../components/TaskInbox";
import "./studio-space.css";

type DragPayload = { kind: "skill"; skill: ProducerSkillItem };

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

function skillIcon(role: ProducerSkillItem["role"]) {
  if (role === "mind") return <BookOpen size={15} strokeWidth={1.8} />;
  if (role === "production") return <ImageIcon size={15} strokeWidth={1.8} />;
  if (role === "legacy_end_to_end") return <Package size={15} strokeWidth={1.8} />;
  return <CircleHelp size={15} strokeWidth={1.8} />;
}

function skillLabel(skill: ProducerSkillItem) { return skill.display_name ?? skill.name; }

export function SkillsPage() {
  const client = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const inbox = useWorkInbox();
  const [initial] = useState(restoreComposer);
  const skills = useQuery({ queryKey: ["producer-skills"], queryFn: studioApi.producerSkills });
  const creators = useCreators();
  const [selectedIds, setSelectedIds] = useState<string[]>(initial.ids);
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
  const requestedSkill = searchParams.get("skill");
  useEffect(() => {
    if (!requestedSkill || openedSkill.current === requestedSkill) return;
    const item = skills.data?.items.find(skill => skill.id === requestedSkill);
    if (item) { openedSkill.current = requestedSkill; setInspected(item); }
  }, [requestedSkill, skills.data]);
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
    const cleanup = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      window.removeEventListener("pointercancel", cancel);
      window.removeEventListener("keydown", esc);
      dragCleanup.current = null;
    };
    const move = (e: PointerEvent) => {
      const state = dragRef.current;
      if (!state) return;
      if (!state.active && Math.hypot(e.clientX - state.startX, e.clientY - state.startY) < 6) return;
      state.active = true;
      const target = document.elementFromPoint(e.clientX, e.clientY)?.closest("[data-drop]")?.getAttribute("data-drop") ?? null;
      setDrag({ payload: state.payload, x: e.clientX, y: e.clientY, target: validTarget(state.payload, target) ? target : null });
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

  if (skills.isPending || creators.isPending) return <LoadingState label="正在读取 Skill 库…" />;
  if (skills.isError || creators.isError) {
    const error = skills.error ?? creators.error;
    return <ErrorState message={error?.message ?? "读取失败"} onRetry={() => void Promise.all([skills.refetch(), creators.refetch()])} />;
  }

  const allSkills = skills.data.items;
  const minds = allSkills.filter(s => s.role === "mind");
  const visuals = allSkills.filter(s => s.role === "production");
  const others = allSkills.filter(s => s.role === "legacy_end_to_end" || s.role === null);
  const accounts = creators.data.items;
  const selectedSkills = selectedIds.map(id => allSkills.find(skill => skill.id === id)).filter((skill): skill is ProducerSkillItem => Boolean(skill));
  const missingSkills = selectedSkills.length !== selectedIds.length;
  const busy = compose.isPending || merge.isPending;
  const binding = directBinding(selectedIds, allSkills);
  const isMerge = selectedIds.length > 1 && mode === "merge";
  const canCreate = !!binding && !!name.trim() && !busy && !missingSkills;
  const draggingSkill = drag?.payload.skill ?? null;

  const skillCard = (skill: ProducerSkillItem, enabled: boolean) => {
    return <div key={skill.id} className={`skill-card-wrap ${draggingSkill?.id === skill.id ? "drag-source" : ""}`}>
      <button type="button" className={`skill-card ${enabled ? "" : "view-only"}`}
        onPointerDown={event => { suppressClick.current = false; if (enabled) onDragStart({ kind: "skill", skill })(event); }}
        onClick={event => { if (event.detail > 0 && suppressClick.current) { suppressClick.current = false; return; } setInspected(skill); }}
        aria-haspopup="dialog">
        <span className="skill-icon">{skillIcon(skill.role)}</span>
        <span className="skill-copy"><strong>{skillLabel(skill)}</strong></span>
        {inbox.hasUnseen("skill", skill.id) && <UnseenDot label="Skill 有未查看更新" />}
      </button>
    </div>;
  };

  return <div className="space-page">
    <header className="space-head"><h1>Skill 库</h1></header>
    <ArtifactSkillExtraction onDirtyChange={setWorkbenchDirty} onUseSkill={skill => {
      setSelectedIds([skill.id]); setMode("reference"); setNotice({ kind: "ok", text: `已选择「${skillLabel(skill)}」，填写栏目名称后创建。` });
    }} />
    <div className="space-columns">
      <section className="space-col" aria-label="Mind">
        <div className="space-col-head"><h2>Mind</h2><span>{minds.length || ""}</span></div>
        {minds.map(skill => skillCard(skill, true))}
        {!minds.length && <div className="space-empty"><b>暂无</b></div>}
        {!!others.length && <div className="skill-group">
          <h3>其他</h3>
          {others.map(skill => skillCard(skill, true))}
        </div>}
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
      </section>

      <section className="space-col" aria-label="Visualize">
        <div className="space-col-head"><h2>Visualize</h2><span>{visuals.length || ""}</span></div>
        {visuals.map(skill => skillCard(skill, true))}
        {!visuals.length && <div className="space-empty"><b>暂无</b></div>}
      </section>

      <section className="space-col" aria-label="组合栏目">
        <div className="space-col-head"><h2>组合栏目</h2></div>
        <div className="composer">
          <div data-drop="skill-basket" aria-label="Skill 组合框"
            className={`skill-basket ${draggingSkill ? (drag?.target === "skill-basket" ? "drop-ok" : "can-drop") : ""}`}>
            {!selectedIds.length && <p>把任意 Skill 拖到这里，或在详情中加入</p>}
            {selectedSkills.map(skill => <div className="basket-item" key={skill.id}>
              <button type="button" className="basket-remove" disabled={busy} onClick={() => setSelectedIds(ids => ids.filter(id => id !== skill.id))} aria-label={`移除 ${skillLabel(skill)}`}>
                {skillIcon(skill.role)}<strong>{skillLabel(skill)}</strong><span aria-hidden="true">×</span>
              </button>
              <button type="button" className="skill-slot-view" onClick={() => setInspected(skill)} aria-label={`查看 ${skillLabel(skill)}`}>查看</button>
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
            <label>归属账号<select value={creatorId} onChange={event => setCreatorId(event.target.value)}>
              <option value="">暂不分配</option>
              {accounts.map(account => <option key={account.id} value={account.id}>{account.display_name}</option>)}
            </select></label>
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
    {inspected && <SkillInspector key={inspected.id} skill={inspected} onClose={closeInspector}
      onDeleted={() => { setSelectedIds(ids => ids.filter(id => id !== inspected.id)); closeInspector(); }}
      onAdd={() => {
        addSkill(inspected);
        closeInspector();
      }} />}
  </div>;
}
