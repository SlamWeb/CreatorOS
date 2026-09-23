import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BookOpen, Image as ImageIcon, Package, CircleHelp, Plus } from "lucide-react";
import { studioApi } from "../api/client";
import type { ProducerSkillItem, SeriesView } from "../api/types";
import { ErrorState, LoadingState } from "../components/PageState";
import "./studio-space.css";

const ROLE_LABEL: Record<string, string> = {
  mind: "内容", production: "制作", legacy_end_to_end: "端到端",
};

function skillIcon(role: ProducerSkillItem["role"]) {
  if (role === "mind") return <BookOpen size={16} strokeWidth={1.8} />;
  if (role === "production") return <ImageIcon size={16} strokeWidth={1.8} />;
  if (role === "legacy_end_to_end") return <Package size={16} strokeWidth={1.8} />;
  return <CircleHelp size={16} strokeWidth={1.8} />;
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
      setNotice({ kind: "ok", text: `栏目「${result.series.name}」已创建（未分配账号）。` });
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
      setNotice({ kind: "ok", text: `「${result.series.name}」已归属账号。` });
      await refresh();
    },
    onError: async (error) => {
      setNotice({ kind: "error", text: `${error.message} 已为你刷新最新状态，请重新操作。` });
      await refresh();
    },
  });

  if (creators.isPending || skills.isPending || seriesAll.isPending) return <LoadingState label="正在读取创作空间…" />;
  if (creators.isError || skills.isError || seriesAll.isError) {
    const error = creators.error ?? skills.error ?? seriesAll.error;
    return <ErrorState message={error?.message ?? "读取失败"} onRetry={() => void Promise.all([creators.refetch(), skills.refetch(), seriesAll.refetch()])} />;
  }

  const allSkills = skills.data.items;
  const composable = allSkills.filter(s => s.role === "mind" || s.role === "production");
  const others = allSkills.filter(s => s.role === "legacy_end_to_end" || s.role === null);
  const accounts = creators.data.items;
  const allSeries = seriesAll.data;
  const unassigned = allSeries.filter(s => s.creator_id === null);
  const byCreator = new Map(accounts.map(c => [c.id, allSeries.filter(s => s.creator_id === c.id)]));
  const canCreate = Boolean(mind && production && name.trim()) && !compose.isPending;

  const pick = (skill: ProducerSkillItem) => {
    setNotice(null);
    if (skill.role === "mind") setMind(current => (current?.id === skill.id ? null : skill));
    if (skill.role === "production") setProduction(current => (current?.id === skill.id ? null : skill));
  };

  return <div className="space-page">
    <header className="space-head">
      <div>
        <p className="space-eyebrow">01 · Compose / 创作空间</p>
        <h1>组合出你的栏目</h1>
        <p>内容 Skill 负责"讲什么、怎么讲懂"，制作 Skill 负责"怎么呈现"。两个组合成一个栏目；栏目可以先不分配账号，生产前再归属。</p>
      </div>
    </header>
    <div className="space-columns">
      <section className="space-col" aria-label="Skill 库">
        <div className="space-col-head"><h2>Skill 库</h2><span>{allSkills.length} installed</span></div>
        <div className="skill-group">
          <h3>内容 / 制作</h3>
          {!composable.length && <div className="space-empty"><b>还没有可组合的 Skill</b>从 GitHub 安装时声明内容或制作角色。</div>}
          {composable.map(skill => {
            const picked = (skill.role === "mind" ? mind : production)?.id === skill.id;
            return <button key={skill.id} type="button" aria-pressed={picked}
              className={`skill-card ${picked ? "picked" : ""} ${skill.role === "production" ? "production-role" : ""}`}
              onClick={() => pick(skill)}>
              <span className={`skill-icon ${skill.role === "production" ? "production-role" : ""}`}>{skillIcon(skill.role)}</span>
              <span className="skill-copy"><strong>{skill.name}</strong><small>{skill.description}</small></span>
              <span className="skill-tag">{ROLE_LABEL[skill.role ?? ""]}</span>
            </button>;
          })}
        </div>
        {!!others.length && <div className="skill-group">
          <h3>端到端 / 未分类</h3>
          {others.map(skill => <button key={skill.id} type="button" className="skill-card" disabled
            title="端到端 Skill 不参与组合；未分类 Skill 需在安装时声明角色">
            <span className="skill-icon legacy-role">{skillIcon(skill.role)}</span>
            <span className="skill-copy"><strong>{skill.name}</strong><small>{skill.description}</small></span>
            <span className="skill-tag">{skill.role === null ? "未分类" : ROLE_LABEL[skill.role ?? ""]}</span>
          </button>)}
        </div>}
        <p className="space-note">安装新 Skill：打开任意栏目页 → 生产 Skill 面板；声明角色后才能参与组合。</p>
      </section>

      <section className="space-col" aria-label="组合与未分配栏目">
        <div className="space-col-head"><h2>组合栏目</h2><span>mind + production</span></div>
        <div className="composer">
          <button type="button" className={`slot ${mind ? "filled" : ""}`} onClick={() => setMind(null)} aria-label="内容 Skill 槽">
            <small>MIND · 内容 Skill</small>
            {mind ? <strong>{mind.name}</strong> : <span>从左侧选择一个内容 Skill</span>}
            {mind && <span>点击移除</span>}
          </button>
          <div className="slot-plus">+</div>
          <button type="button" className={`slot production-slot ${production ? "filled" : ""}`} onClick={() => setProduction(null)} aria-label="制作 Skill 槽">
            <small>PRODUCTION · 制作 Skill</small>
            {production ? <strong>{production.name}</strong> : <span>从左侧选择一个制作 Skill</span>}
            {production && <span>点击移除</span>}
          </button>
          <form className="composer-form" onSubmit={event => { event.preventDefault(); setNotice(null); compose.mutate(); }}>
            <label>栏目名称<input required maxLength={120} value={name} onChange={event => setName(event.target.value)} placeholder="例如：AI 概念图解" /></label>
            <label>栏目定位<input maxLength={10_000} value={description} onChange={event => setDescription(event.target.value)} placeholder="这个栏目讲什么、怎么讲" /></label>
            <label>目标受众<input maxLength={4_000} value={audience} onChange={event => setAudience(event.target.value)} placeholder="写给谁看" /></label>
            <button className="composer-create" disabled={!canCreate} title={mind && production ? undefined : "先选齐内容 Skill 与制作 Skill"}>
              <Plus size={14} strokeWidth={2} style={{ verticalAlign: "-2px", marginRight: 6 }} />{compose.isPending ? "创建中…" : "创建组合栏目"}
            </button>
          </form>
          {notice?.kind === "error" && <p className="space-error" role="alert">{notice.text}</p>}
          {notice?.kind === "ok" && <p className="space-ok" role="status">{notice.text}</p>}
        </div>

        <div className="unassigned">
          <div className="space-col-head"><h2>未分配栏目</h2><span>{unassigned.length}</span></div>
          {!unassigned.length && <div className="space-empty"><b>没有未分配栏目</b>创建的栏目会先出现在这里，分配账号后才能生产。</div>}
          {unassigned.map(series => <div className="series-row" key={series.id}>
            <div className="series-row-head"><strong>{series.name}</strong>
              <span className={`bind-tag ${series.skill_name ? "legacy" : "pair"}`}>{series.skill_name ? "单 Skill" : "组合"}</span></div>
            <small>{series.topic_count} 个选题 · revision {series.revision}</small>
            <div className="series-assign">
              <select aria-label={`把 ${series.name} 分配给账号`} value="" disabled={assign.isPending}
                onChange={event => { if (event.target.value) assign.mutate({ series, creatorId: event.target.value }); }}>
                <option value="" disabled>分配给账号…</option>
                {accounts.map(account => <option key={account.id} value={account.id}>{account.display_name}</option>)}
              </select>
              <Link className="bind-tag legacy" to={`/series/${series.id}`}>打开</Link>
            </div>
          </div>)}
        </div>
      </section>

      <section className="space-col accounts-col" aria-label="账号">
        <div className="space-col-head"><h2>账号</h2><span>{accounts.length} 个</span></div>
        {!accounts.length && <div className="space-empty"><b>还没有运营账号</b><Link to="/creators">先创建账号 →</Link></div>}
        {accounts.map(account => <div className="account-card" key={account.id}>
          <div className="account-card-head">
            <span className="account-avatar">{account.display_name.slice(0, 1)}</span>
            <div><h3>{account.display_name}</h3><small>{account.account_handle ?? account.platform} · {byCreator.get(account.id)?.length ?? 0} 个栏目</small></div>
          </div>
          {(byCreator.get(account.id) ?? []).map(series => <Link className="series-row" key={series.id} to={`/series/${series.id}`}>
            <div className="series-row-head"><strong>{series.name}</strong>
              <span className={`bind-tag ${series.skill_name ? "legacy" : "pair"}`}>{series.skill_name ? "单 Skill" : "组合"}</span></div>
            <small>{series.topic_count} 个选题{series.skill_name ? "" : " · 生产待 P4 接入"}</small>
          </Link>)}
          {!(byCreator.get(account.id) ?? []).length && <p className="account-empty">还没有栏目。</p>}
        </div>)}
      </section>
    </div>
  </div>;
}
