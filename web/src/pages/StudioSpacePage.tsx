import { useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BookOpen, Image as ImageIcon, Package, CircleHelp } from "lucide-react";
import { studioApi } from "../api/client";
import type { ProducerSkillItem, SeriesView } from "../api/types";
import { ErrorState, LoadingState } from "../components/PageState";
import "./studio-space.css";

type DragPayload = { kind: "skill"; skill: ProducerSkillItem } | { kind: "series"; series: SeriesView };

function skillIcon(role: ProducerSkillItem["role"]) {
  if (role === "mind") return <BookOpen size={15} strokeWidth={1.8} />;
  if (role === "production") return <ImageIcon size={15} strokeWidth={1.8} />;
  if (role === "legacy_end_to_end") return <Package size={15} strokeWidth={1.8} />;
  return <CircleHelp size={15} strokeWidth={1.8} />;
}

export function StudioSpacePage() {
  const client = useQueryClient();
  const creators = useQuery({ queryKey: ["creators"], queryFn: studioApi.creators });
  const skills = useQuery({ queryKey: ["producer-skills"], queryFn: studioApi.producerSkills });
  const seriesAll = useQuery({ queryKey: ["series-all"], queryFn: studioApi.seriesAll });
  const [mind, setMind] = useState<ProducerSkillItem | null>(null);
  const [production, setProduction] = useState<ProducerSkillItem | null>(null);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [audience, setAudience] = useState("");
  const [notice, setNotice] = useState<{ kind: "ok" | "error"; text: string } | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [drag, setDrag] = useState<{ payload: DragPayload; x: number; y: number; target: string | null } | null>(null);
  const dragRef = useRef<{ payload: DragPayload; startX: number; startY: number; active: boolean } | null>(null);

  const refresh = async () => {
    await Promise.all([
      client.invalidateQueries({ queryKey: ["series-all"] }),
      client.invalidateQueries({ queryKey: ["creators"] }),
      client.invalidateQueries({ queryKey: ["overview"] }),
    ]);
  };

  const compose = useMutation({
    retry: false,
    mutationFn: () => studioApi.composeSeries({
      name: name.trim(), description: description.trim(), audience: audience.trim(),
      mind_skill_id: mind!.id, production_skill_id: production!.id,
      request_id: crypto.randomUUID().replaceAll("-", ""),
    }),
    onSuccess: async (result) => {
      setMind(null); setProduction(null); setName(""); setDescription(""); setAudience("");
      setNotice({ kind: "ok", text: `已创建「${result.series.name}」。` });
      await refresh();
    },
    onError: (error) => setNotice({ kind: "error", text: error.message }),
  });

  const assign = useMutation({
    retry: false,
    mutationFn: (input: { series: SeriesView; creatorId: string }) => studioApi.assignSeries(input.series.id, {
      creator_id: input.creatorId,
      expected_revision: input.series.revision,
      request_id: crypto.randomUUID().replaceAll("-", ""),
    }),
    onSuccess: async (result) => {
      setNotice({ kind: "ok", text: `「${result.series.name}」已归属。` });
      await refresh();
    },
    onError: async (error) => {
      setNotice({ kind: "error", text: error.message });
      await refresh();
    },
  });

  const validTarget = (payload: DragPayload, target: string | null) => {
    if (!target) return false;
    if (payload.kind === "skill") return payload.skill.role === "mind" ? target === "slot-mind" : target === "slot-production";
    return target.startsWith("account-");
  };

  const applyDrop = (payload: DragPayload, target: string) => {
    if (payload.kind === "skill") {
      if (target === "slot-mind") setMind(payload.skill);
      if (target === "slot-production") setProduction(payload.skill);
    } else {
      assign.mutate({ series: payload.series, creatorId: target.slice("account-".length) });
    }
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
      } else if (state.payload.kind === "skill") {
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

  if (creators.isPending || skills.isPending || seriesAll.isPending) return <LoadingState label="正在读取创作空间…" />;
  if (creators.isError || skills.isError || seriesAll.isError) {
    const error = creators.error ?? skills.error ?? seriesAll.error;
    return <ErrorState message={error?.message ?? "读取失败"} onRetry={() => void Promise.all([creators.refetch(), skills.refetch(), seriesAll.refetch()])} />;
  }

  const allSkills = skills.data.items;
  const minds = allSkills.filter(s => s.role === "mind");
  const visuals = allSkills.filter(s => s.role === "production");
  const others = allSkills.filter(s => s.role === "legacy_end_to_end" || s.role === null);
  const accounts = creators.data.items;
  const allSeries = seriesAll.data;
  const unassigned = allSeries.filter(s => s.creator_id === null);
  const byCreator = new Map(accounts.map(c => [c.id, allSeries.filter(s => s.creator_id === c.id)]));
  const canCreate = Boolean(mind && production && name.trim()) && !compose.isPending;
  const draggingSkill = drag?.payload.kind === "skill" ? drag.payload.skill : null;
  const draggingSeries = drag?.payload.kind === "series" ? drag.payload.series : null;

  const skillCard = (skill: ProducerSkillItem, enabled: boolean) => {
    const open = expanded === skill.id;
    return <div key={skill.id} className={`skill-card-wrap ${drag?.payload.kind === "skill" && drag.payload.skill.id === skill.id ? "drag-source" : ""}`}>
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
    <header className="space-head"><h1>创作空间</h1></header>
    <div className="space-columns">
      <section className="space-col" aria-label="Mind">
        <div className="space-col-head"><h2>Mind</h2><span>{minds.length || ""}</span></div>
        {minds.map(skill => skillCard(skill, true))}
        {!minds.length && <div className="space-empty"><b>暂无</b></div>}
        {!!others.length && <div className="skill-group">
          <h3>其他</h3>
          {others.map(skill => skillCard(skill, false))}
        </div>}
      </section>

      <section className="space-col" aria-label="Visualize">
        <div className="space-col-head"><h2>Visualize</h2><span>{visuals.length || ""}</span></div>
        {visuals.map(skill => skillCard(skill, true))}
        {!visuals.length && <div className="space-empty"><b>暂无</b></div>}
      </section>

      <section className="space-col" aria-label="组合与未分配栏目">
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
            <button className="composer-create" disabled={!canCreate}>{compose.isPending ? "创建中…" : "创建栏目"}</button>
          </form>
          {notice?.kind === "error" && <p className="space-error" role="alert">{notice.text}</p>}
          {notice?.kind === "ok" && <p className="space-ok" role="status">{notice.text}</p>}
        </div>

        <div className="unassigned">
          <div className="space-col-head"><h2>未分配栏目</h2><span>{unassigned.length || ""}</span></div>
          {unassigned.map(series => <div className="series-row" key={series.id} onPointerDown={onDragStart({ kind: "series", series })}>
            <div className="series-row-head"><strong>{series.name}</strong>
              <span className="bind-tag">{series.skill_name ? "单 Skill" : "组合"}</span></div>
            <small>{series.topic_count} 个选题</small>
            <div className="series-assign" onPointerDown={event => event.stopPropagation()}>
              <select aria-label={`把 ${series.name} 分配给账号`} value="" disabled={assign.isPending}
                onChange={event => { if (event.target.value) assign.mutate({ series, creatorId: event.target.value }); }}>
                <option value="" disabled>分配给账号…</option>
                {accounts.map(account => <option key={account.id} value={account.id}>{account.display_name}</option>)}
              </select>
              <Link className="bind-tag" to={`/series/${series.id}`}>打开</Link>
            </div>
          </div>)}
        </div>
      </section>

      <section className="space-col accounts-col" aria-label="账号">
        <div className="space-col-head"><h2>账号</h2><span>{accounts.length || ""}</span></div>
        {!accounts.length && <div className="space-empty"><b>暂无</b><Link to="/creators">创建 →</Link></div>}
        {accounts.map(account => <div key={account.id} data-drop={`account-${account.id}`}
          className={`account-card ${draggingSeries ? (drag?.target === `account-${account.id}` ? "drop-ok" : "can-drop") : ""}`}>
          <div className="account-card-head">
            <span className="account-avatar">{account.display_name.slice(0, 1)}</span>
            <h3>{account.display_name}</h3>
          </div>
          {(byCreator.get(account.id) ?? []).map(series => <Link className="series-row" key={series.id} to={`/series/${series.id}`}>
            <div className="series-row-head"><strong>{series.name}</strong>
              <span className="bind-tag">{series.skill_name ? "单 Skill" : "组合"}</span></div>
            <small>{series.topic_count} 个选题{series.skill_name ? "" : " · 生产未接入"}</small>
          </Link>)}
        </div>)}
      </section>
    </div>
    {drag && <div className={`drag-ghost ${drag.target ? "on-target" : ""}`} style={{ left: drag.x, top: drag.y }} aria-hidden="true">
      {drag.payload.kind === "skill" ? drag.payload.skill.name : drag.payload.series.name}
    </div>}
  </div>;
}
