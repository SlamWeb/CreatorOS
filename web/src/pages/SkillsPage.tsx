import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BookOpen, Image as ImageIcon, Package, CircleHelp } from "lucide-react";
import { studioApi, request } from "../api/client";
import { useCreators } from "../api/hooks";
import type { ProducerSkillItem } from "../api/types";
import { ErrorState, LoadingState } from "../components/PageState";
import "./studio-space.css";

type DragPayload = { kind: "skill"; skill: ProducerSkillItem };

function skillIcon(role: ProducerSkillItem["role"]) {
  if (role === "mind") return <BookOpen size={15} strokeWidth={1.8} />;
  if (role === "production") return <ImageIcon size={15} strokeWidth={1.8} />;
  if (role === "legacy_end_to_end") return <Package size={15} strokeWidth={1.8} />;
  return <CircleHelp size={15} strokeWidth={1.8} />;
}

export function SkillsPage() {
  const client = useQueryClient();
  const skills = useQuery({ queryKey: ["producer-skills"], queryFn: studioApi.producerSkills });
  const creators = useCreators();
  const [mind, setMind] = useState<ProducerSkillItem | null>(null);
  const [production, setProduction] = useState<ProducerSkillItem | null>(null);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [audience, setAudience] = useState("");
  const [creatorId, setCreatorId] = useState("");
  const [notice, setNotice] = useState<{ kind: "ok" | "error"; text: string } | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [drag, setDrag] = useState<{ payload: DragPayload; x: number; y: number; target: string | null } | null>(null);
  const dragRef = useRef<{ payload: DragPayload; startX: number; startY: number; active: boolean } | null>(null);

  const compose = useMutation({
    retry: false,
    mutationFn: () => studioApi.composeSeries({
      name: name.trim(), description: description.trim(), audience: audience.trim(),
      creator_id: creatorId || null,
      mind_skill_id: mind!.id, production_skill_id: production!.id,
      request_id: crypto.randomUUID().replaceAll("-", ""),
    }),
    onSuccess: async (result) => {
      setMind(null); setProduction(null); setName(""); setDescription(""); setAudience(""); setCreatorId("");
      setNotice({ kind: "ok", text: `已创建「${result.series.name}」。` });
      await Promise.all([
        client.invalidateQueries({ queryKey: ["series-all"] }),
        client.invalidateQueries({ queryKey: ["creators"] }),
      ]);
    },
    onError: (error) => setNotice({ kind: "error", text: error.message }),
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

  const validTarget = (payload: DragPayload, target: string | null) =>
    payload.skill.role === "mind" ? target === "slot-mind" : target === "slot-production";

  const applyDrop = (payload: DragPayload, target: string) => {
    if (target === "slot-mind") setMind(payload.skill);
    if (target === "slot-production") setProduction(payload.skill);
  };

  const onDragStart = (payload: DragPayload) => (event: React.PointerEvent) => {
    if (event.button !== 0) return;
    dragRef.current = { payload, startX: event.clientX, startY: event.clientY, active: false };
    const cleanup = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      window.removeEventListener("keydown", esc);
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
        const target = document.elementFromPoint(e.clientX, e.clientY)?.closest("[data-drop]")?.getAttribute("data-drop") ?? null;
        if (target && validTarget(state.payload, target)) applyDrop(state.payload, target);
      } else {
        const skillId = state.payload.skill.id;
        setExpanded(current => (current === skillId ? null : skillId));
      }
    };
    const esc = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      cleanup();
      dragRef.current = null;
      setDrag(null);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
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
  const canCreate = Boolean(mind && production && name.trim()) && !compose.isPending;
  const draggingSkill = drag?.payload.skill ?? null;

  const skillCard = (skill: ProducerSkillItem, enabled: boolean) => {
    const open = expanded === skill.id;
    return <div key={skill.id} className={`skill-card-wrap ${draggingSkill?.id === skill.id ? "drag-source" : ""}`}>
      <button type="button" className={`skill-card ${open ? "open" : ""}`} disabled={!enabled}
        onPointerDown={enabled ? onDragStart({ kind: "skill", skill }) : undefined}
        aria-expanded={open}>
        <span className="skill-icon">{skillIcon(skill.role)}</span>
        <span className="skill-copy"><strong>{skill.name}</strong></span>
      </button>
      {open && <div className="skill-detail">
        <p>{skill.description}</p>
        <button type="button" className="skill-add"
          onClick={() => { if (skill.role === "mind") setMind(skill); if (skill.role === "production") setProduction(skill); setExpanded(null); }}>加入组合</button>
      </div>}
    </div>;
  };

  return <div className="space-page">
    <header className="space-head"><h1>Skill 库</h1></header>
    <div className="space-columns">
      <section className="space-col" aria-label="Mind">
        <div className="space-col-head"><h2>Mind</h2><span>{minds.length || ""}</span></div>
        {minds.map(skill => skillCard(skill, true))}
        {!minds.length && <div className="space-empty"><b>暂无</b></div>}
        {!!others.length && <div className="skill-group">
          <h3>其他</h3>
          {others.map(skill => skillCard(skill, false))}
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
          <button type="button" data-drop="slot-mind"
            className={`slot ${mind ? "filled" : ""} ${draggingSkill?.role === "mind" ? (drag?.target === "slot-mind" ? "drop-ok" : "can-drop") : ""}`}
            onClick={() => setMind(null)} aria-label="Mind 槽">
            {mind ? <strong>{mind.name}</strong> : <span>Mind</span>}
            {mind && <span aria-hidden="true">×</span>}
          </button>
          <div className="slot-plus">+</div>
          <button type="button" data-drop="slot-production"
            className={`slot production-slot ${production ? "filled" : ""} ${draggingSkill?.role === "production" ? (drag?.target === "slot-production" ? "drop-ok" : "can-drop") : ""}`}
            onClick={() => setProduction(null)} aria-label="Visualize 槽">
            {production ? <strong>{production.name}</strong> : <span>Visualize</span>}
            {production && <span aria-hidden="true">×</span>}
          </button>
          <form className="composer-form" onSubmit={event => { event.preventDefault(); setNotice(null); compose.mutate(); }}>
            <label>栏目名称<input required maxLength={120} value={name} onChange={event => setName(event.target.value)} placeholder="例如：AI 概念图解" /></label>
            <label>栏目定位<input maxLength={10_000} value={description} onChange={event => setDescription(event.target.value)} placeholder="这个栏目讲什么" /></label>
            <label>目标受众<input maxLength={4_000} value={audience} onChange={event => setAudience(event.target.value)} placeholder="写给谁看" /></label>
            <label>归属账号<select value={creatorId} onChange={event => setCreatorId(event.target.value)}>
              <option value="">暂不分配</option>
              {accounts.map(account => <option key={account.id} value={account.id}>{account.display_name}</option>)}
            </select></label>
            <button className="composer-create" disabled={!canCreate}>{compose.isPending ? "创建中…" : "创建栏目"}</button>
          </form>
          {notice?.kind === "error" && <p className="space-error" role="alert">{notice.text}</p>}
          {notice?.kind === "ok" && <p className="space-ok" role="status">{notice.text}</p>}
        </div>
      </section>
    </div>
    {drag && <div className={`drag-ghost ${drag.target ? "on-target" : ""}`} style={{ left: drag.x, top: drag.y }} aria-hidden="true">
      {drag.payload.skill.name}
    </div>}
  </div>;
}
