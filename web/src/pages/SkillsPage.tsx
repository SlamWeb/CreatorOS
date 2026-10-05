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
import "./studio-space.css";

type DragPayload = { kind: "skill"; skill: ProducerSkillItem };

const composerKey = "creatoros:skill-composer:v1";
type ComposerDraft = { ids: string[]; name: string; description: string; audience: string; creatorId: string; instruction: string; pending?: { request_id: string; skill_ids: string[]; instruction: string } };
function restoreComposer(): ComposerDraft {
  try {
    const data = JSON.parse(sessionStorage.getItem(composerKey) ?? "null");
    if (data && Array.isArray(data.ids) && data.ids.every((id: unknown) => typeof id === "string")) {
      return { ...data, ids: data.ids.slice(0, 8), name: String(data.name ?? ""), description: String(data.description ?? ""), audience: String(data.audience ?? ""), creatorId: String(data.creatorId ?? ""), instruction: String(data.instruction ?? "") };
    }
  } catch { /* No stored draft. */ }
  return { ids: [], name: "", description: "", audience: "", creatorId: "", instruction: "" };
}

function skillIcon(role: ProducerSkillItem["role"]) {
  if (role === "mind") return <BookOpen size={15} strokeWidth={1.8} />;
  if (role === "production") return <ImageIcon size={15} strokeWidth={1.8} />;
  if (role === "legacy_end_to_end") return <Package size={15} strokeWidth={1.8} />;
  return <CircleHelp size={15} strokeWidth={1.8} />;
}

export function SkillsPage() {
  const client = useQueryClient();
  const [, setSearchParams] = useSearchParams();
  const [initial] = useState(restoreComposer);
  const skills = useQuery({ queryKey: ["producer-skills"], queryFn: studioApi.producerSkills });
  const creators = useCreators();
  const [selectedIds, setSelectedIds] = useState<string[]>(initial.ids);
  const [name, setName] = useState(initial.name);
  const [description, setDescription] = useState(initial.description);
  const [audience, setAudience] = useState(initial.audience);
  const [creatorId, setCreatorId] = useState(initial.creatorId);
  const [mergeInstruction, setMergeInstruction] = useState(initial.instruction);
  const [workbenchDirty, setWorkbenchDirty] = useState(false);
  const [storageError, setStorageError] = useState("");
  const mergeRequest = useRef<ComposerDraft["pending"]>(initial.pending);
  const composeRequest = useRef<{ signature: string; id: string } | null>(null);
  const [notice, setNotice] = useState<{ kind: "ok" | "error"; text: string } | null>(null);
  const [inspected, setInspected] = useState<ProducerSkillItem | null>(null);
  const [drag, setDrag] = useState<{ payload: DragPayload; x: number; y: number; target: string | null } | null>(null);
  const dragRef = useRef<{ payload: DragPayload; startX: number; startY: number; active: boolean } | null>(null);
  const dragCleanup = useRef<(() => void) | null>(null);
  const suppressClick = useRef(false);
  useEffect(() => () => dragCleanup.current?.(), []);
  const storeComposer = () => {
    try {
      sessionStorage.setItem(composerKey, JSON.stringify({ ids: selectedIds, name, description, audience, creatorId, instruction: mergeInstruction, pending: mergeRequest.current }));
      setStorageError("");
    } catch { setStorageError("浏览器无法暂存组合；离开或刷新会丢失尚未提交的选择。"); }
  };
  useEffect(storeComposer, [selectedIds, name, description, audience, creatorId, mergeInstruction]);
  const addSkill = (skill: ProducerSkillItem) => {
    setNotice(null);
    if (selectedIds.includes(skill.id)) return;
    if (selectedIds.length >= 8) { setNotice({ kind: "error", text: "一次最多融合 8 个 Skill。" }); return; }
    setSelectedIds(current => [...current, skill.id]);
  };

  const compose = useMutation({
    retry: false,
    mutationFn: () => {
      if (selectedIds.length !== 1 || !name.trim()) throw new Error("请选择一个完整 Skill 并填写栏目名称。");
      const input = { name: name.trim(), description: description.trim(), audience: audience.trim(), creator_id: creatorId || null, skill_name: selectedIds[0] };
      const signature = JSON.stringify(input);
      if (composeRequest.current?.signature !== signature) composeRequest.current = { signature, id: crypto.randomUUID().replaceAll("-", "") };
      return studioApi.composeSeries({ ...input, request_id: composeRequest.current!.id });
    },
    onSuccess: async (result) => {
      setSelectedIds([]); setName(""); setDescription(""); setAudience(""); setCreatorId(""); composeRequest.current = null;
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
      setNotice({ kind: "ok", text: "融合任务已提交。请在上方工作台检查草稿，确认入库后再用于栏目。" });
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
    queryKey: ["skill-install", jobId], enabled: !!jobId, refetchInterval: q => q.state.data?.status === "installing" ? 2000 : false,
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
  const canCreate = selectedIds.length === 1 && !!name.trim() && !busy && !missingSkills;
  const draggingSkill = drag?.payload.skill ?? null;

  const skillCard = (skill: ProducerSkillItem, enabled: boolean) => {
    return <div key={skill.id} className={`skill-card-wrap ${draggingSkill?.id === skill.id ? "drag-source" : ""}`}>
      <button type="button" className={`skill-card ${enabled ? "" : "view-only"}`}
        onPointerDown={event => { suppressClick.current = false; if (enabled) onDragStart({ kind: "skill", skill })(event); }}
        onClick={event => { if (event.detail > 0 && suppressClick.current) { suppressClick.current = false; return; } setInspected(skill); }}
        aria-haspopup="dialog">
        <span className="skill-icon">{skillIcon(skill.role)}</span>
        <span className="skill-copy"><strong>{skill.name}</strong></span>
      </button>
    </div>;
  };

  return <div className="space-page">
    <header className="space-head"><h1>Skill 库</h1></header>
    <ArtifactSkillExtraction onDirtyChange={setWorkbenchDirty} onUseSkill={skill => {
      setSelectedIds([skill.id]); setNotice({ kind: "ok", text: `已选择「${skill.name}」，填写栏目名称后创建。` });
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
            <button className="button button-secondary" disabled={!installUrl.trim() || install.isPending || job.data?.status === "installing"}>安装</button>
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
              <button type="button" className="basket-remove" disabled={busy} onClick={() => setSelectedIds(ids => ids.filter(id => id !== skill.id))} aria-label={`移除 ${skill.name}`}>
                {skillIcon(skill.role)}<strong>{skill.name}</strong><span aria-hidden="true">×</span>
              </button>
              <button type="button" className="skill-slot-view" onClick={() => setInspected(skill)} aria-label={`查看 ${skill.name}`}>查看</button>
            </div>)}
            {missingSkills && <p role="alert">部分 Skill 已不在库中。<button type="button" onClick={() => setSelectedIds(selectedSkills.map(s => s.id))}>移除失效选择</button></p>}
          </div>
          <form className="composer-form" onSubmit={event => { event.preventDefault(); setNotice(null); if (selectedIds.length > 1) merge.mutate(); else compose.mutate(); }}>
            {selectedIds.length > 1 && <label>融合要求（可选）<textarea rows={3} maxLength={4000} value={mergeInstruction} onChange={event => setMergeInstruction(event.target.value)} placeholder="遇到冲突时，你更希望保留什么？" /></label>}
            <fieldset disabled={busy} className="composer-fields">
            <label>栏目名称<input required={selectedIds.length <= 1} maxLength={120} value={name} onChange={event => setName(event.target.value)} placeholder="例如：AI 概念图解" /></label>
            <label>栏目定位<input maxLength={10_000} value={description} onChange={event => setDescription(event.target.value)} placeholder="这个栏目讲什么" /></label>
            <label>目标受众<input maxLength={4_000} value={audience} onChange={event => setAudience(event.target.value)} placeholder="写给谁看" /></label>
            <label>归属账号<select value={creatorId} onChange={event => setCreatorId(event.target.value)}>
              <option value="">暂不分配</option>
              {accounts.map(account => <option key={account.id} value={account.id}>{account.display_name}</option>)}
            </select></label>
            </fieldset>
            <button className="composer-create" disabled={selectedIds.length > 1 ? busy || missingSkills || workbenchDirty : !canCreate}>
              {merge.isPending ? "提交中…" : compose.isPending ? "创建中…" : selectedIds.length > 1 ? "生成融合草稿" : "创建栏目"}
            </button>
            {selectedIds.length > 1 && <p className="space-note">先检查、修改融合草稿并确认入库，再创建栏目。原 Skill 保持不变。</p>}
            {selectedIds.length > 1 && workbenchDirty && <p role="alert">请先保存上方工作台的草稿修改。</p>}
          </form>
          {storageError && <p className="space-error" role="alert">{storageError}</p>}
          {notice?.kind === "error" && <p className="space-error" role="alert">{notice.text}</p>}
          {notice?.kind === "ok" && <p className="space-ok" role="status">{notice.text}</p>}
        </div>
      </section>
    </div>
    {drag && <div className={`drag-ghost ${drag.target ? "on-target" : ""}`} style={{ left: drag.x, top: drag.y }} aria-hidden="true">
      {drag.payload.skill.name}
    </div>}
    {inspected && <SkillInspector key={inspected.id} skill={inspected} onClose={() => setInspected(null)}
      onAdd={() => {
        addSkill(inspected);
        setInspected(null);
      }} />}
  </div>;
}
