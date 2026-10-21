import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { ArrowLeft, ChevronDown, ExternalLink, Pause, Play, RefreshCw, Repeat2 } from "lucide-react";
import { ApiError, apiUrl } from "../api/client";
import { skillIterationsApi, type SkillIteration, type SkillIterationAction, type SkillIterationActionInput } from "../api/skillIterations";
import "./skill-iteration.css";

const statuses: Record<SkillIteration["status"], string> = { queued: "已排队", running: "正在迭代", waiting_production: "等待试产", paused: "已暂停", completed: "达到标准", exhausted: "本轮已结束", failed: "未完成", interrupted: "需要核对" };
const stages: Record<SkillIteration["stage"], string> = { rubric: "建立评判准则", prepare: "准备候选", waiting: "Codex 试产", judge: "对照参考作品评审", revise: "修改候选", finished: "已结束" };
const actionLabels = { pause: "暂停迭代", resume: "恢复迭代", continue: "再试 3 轮" };
const pendingKey = (id: string) => `creatoros.skill-iteration.action.v1:${id}`;
function readPending(id: string): SkillIterationActionInput | null {
  try {
    const item = JSON.parse(sessionStorage.getItem(pendingKey(id)) ?? "null");
    return item && ["pause", "resume", "continue"].includes(item.action) && typeof item.request_id === "string"
      && Number.isInteger(item.expected_revision) ? item : null;
  } catch { return null; }
}
function useVisible() {
  const [visible, setVisible] = useState(!document.hidden);
  useEffect(() => { const update = () => setVisible(!document.hidden); document.addEventListener("visibilitychange", update);
    return () => document.removeEventListener("visibilitychange", update); }, []);
  return visible;
}
const running = (item: SkillIteration) => ["queued", "running", "waiting_production"].includes(item.status);
const stamp = (value: string) => new Date(value).toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });

function RoundJudgeHistory({ evaluations, criteria }: {
  evaluations: NonNullable<SkillIteration["rounds"][number]["evaluations"]>;
  criteria: SkillIteration["judge"]["criteria"];
}) {
  const [open, setOpen] = useState(false);
  if (evaluations.length < 2) return null;
  return <div className="skill-iteration-evaluation-history">
    <button type="button" className="skill-iteration-disclosure" aria-expanded={open} onClick={() => setOpen(value => !value)}>
      历次评分<ChevronDown size={14} aria-hidden="true" className={open ? "is-open" : ""} /></button>
    {open && evaluations.map((evaluation, index) => <section className="skill-iteration-assessment" key={`${evaluation.judge_version}-${index}`}>
      <h3>Judge v{evaluation.judge_version} · {evaluation.score.toFixed(1)} / 100{evaluation.passed ? " · 达标" : ""}</h3>
      <time>{stamp(evaluation.time)}</time>
      <p className="skill-iteration-feedback">{evaluation.feedback}</p>
      {evaluation.scores.map(score => <div key={score.criterion_id}>
        <span>{criteria.find(criterion => criterion.id === score.criterion_id)?.description ?? score.criterion_id}</span><strong>{score.score}</strong><p>{score.evidence}</p>
      </div>)}
    </section>)}
  </div>;
}

export function SkillIterationHistory() {
  const [open, setOpen] = useState(false);
  const history = useQuery({ queryKey: ["skill-iterations"], queryFn: () => skillIterationsApi.list(), enabled: open, retry: false });
  return <section className="skill-iteration-history">
    <button type="button" className="skill-iteration-history-toggle" aria-expanded={open} onClick={() => setOpen(value => !value)}>
      <Repeat2 size={15} aria-hidden="true" />RSI 迭代记录<ChevronDown size={14} aria-hidden="true" className={open ? "is-open" : ""} /></button>
    {open && <div className="skill-iteration-history-list">
      {history.isPending ? <p role="status">正在读取…</p> : history.error ? <p role="alert">{history.error.message} <button type="button" onClick={() => void history.refetch()}>重试</button></p>
        : !history.data?.items.length ? <p>还没有迭代任务。在 Agent 对话中开启 RSI，并指定 Skill、选题和改进要求。</p>
          : history.data.items.map(item => <Link key={item.id} to={`/skills?iteration=${encodeURIComponent(item.id)}`}>
            <span><strong>{item.skill_name}</strong><small>{item.topic_title}</small></span><span>{statuses[item.status] ?? item.status}<small>{stamp(item.updated_at)}</small></span></Link>)}
    </div>}
  </section>;
}

export function SkillIterationPanel({ id }: { id: string }) {
  const cache = useQueryClient();
  const visible = useVisible();
  const [pending, setPending] = useState(() => readPending(id));
  const [actionError, setActionError] = useState("");
  const [technical, setTechnical] = useState(false);
  const [judgeHistory, setJudgeHistory] = useState(false);
  const [judgeOpen, setJudgeOpen] = useState(() => window.matchMedia("(min-width: 901px)").matches);
  const detail = useQuery({ queryKey: ["skill-iteration", id], queryFn: ({ signal }) => skillIterationsApi.get(id, signal),
    retry: false, refetchInterval: query => visible && query.state.data && running(query.state.data) ? 2000 : false });
  const item = detail.data;
  const act = useMutation({ retry: false, mutationFn: async (input: SkillIterationActionInput) => {
    if (pending) await skillIterationsApi.get(id);
    return skillIterationsApi.action(id, input);
  }, onSuccess: next => {
    cache.setQueryData(["skill-iteration", id], next); setPending(null); setActionError("");
    try { sessionStorage.removeItem(pendingKey(id)); } catch { /* The server keeps the action receipt. */ }
    void cache.invalidateQueries({ queryKey: ["skill-iterations"] });
  }, onError: error => {
    const rejected = error instanceof ApiError && error.status >= 400 && error.status < 500;
    if (rejected) { setPending(null); try { sessionStorage.removeItem(pendingKey(id)); } catch { /* Explicit rejection is final. */ } }
    setActionError(`${error.message}${rejected ? " 请重新读取并核对当前状态。" : " 结果待核对；请恢复同一请求，不要重复操作。"}`);
    void detail.refetch();
  } });
  const submit = (action: SkillIterationAction) => {
    if (!item || act.isPending || pending) return;
    if (action === "continue" && !window.confirm("再授权最多 3 轮试产？仍只交付候选，不自动切换栏目。")) return;
    const input = { action, expected_revision: item.revision, request_id: crypto.randomUUID().replaceAll("-", "") };
    try { sessionStorage.setItem(pendingKey(id), JSON.stringify(input)); }
    catch { setActionError("无法保存请求身份，请检查浏览器存储后重试。"); return; }
    setPending(input); setActionError(""); act.mutate(input);
  };
  const refresh = () => { setActionError(""); void detail.refetch(); };
  return <section className="skill-iteration-panel" aria-label="RSI 迭代任务">
    <Link className="skill-iteration-back" to="/skills"><ArrowLeft size={15} aria-hidden="true" />Skill 库</Link>
    {detail.isPending && <div className="skill-iteration-loading" role="status">正在读取迭代任务…</div>}
    {detail.error && <p className="form-error" role="alert">{detail.error.message} <button type="button" onClick={refresh}>重新读取</button></p>}
    {item && <>
      <header className="skill-iteration-heading"><div><h1>{item.skill_name}</h1><p>{item.series_name} · {item.topic_title}</p></div>
        <span className={`skill-iteration-status${running(item) ? " task-running-text" : ""}`} data-status={item.status}>{statuses[item.status] ?? item.status}</span></header>
      <div className="skill-iteration-toolbar"><span role="status">{stages[item.stage] ?? item.stage} · {item.round_number} / {item.max_rounds} 轮</span>
        <div>{item.can_pause && <button type="button" disabled={act.isPending || !!pending} onClick={() => submit("pause")}><Pause size={14} aria-hidden="true" />暂停</button>}
          {item.can_resume && <button type="button" disabled={act.isPending || !!pending} onClick={() => submit("resume")}><Play size={14} aria-hidden="true" />恢复</button>}
          {item.can_continue && <button type="button" className="button-primary" disabled={act.isPending || !!pending} onClick={() => submit("continue")}><Repeat2 size={14} aria-hidden="true" />再试 3 轮</button>}
          <button type="button" aria-label="刷新迭代任务" disabled={detail.isFetching || act.isPending} onClick={refresh}><RefreshCw size={14} aria-hidden="true" /></button></div></div>
      {pending && !act.isPending && <div className="skill-iteration-recovery" role="status"><span>{actionLabels[pending.action]}：上次结果待核对</span>
        <button type="button" onClick={() => act.mutate(pending)}>核对并恢复上次操作</button></div>}
      {actionError && <p className="form-error" role="alert">{actionError}</p>}
      {item.error && <p className="form-error" role="alert">{item.error}</p>}
      <div className="skill-iteration-result"><span>只交付候选，不自动切换栏目</span>
        {item.best_skill_id && <Link to={`/skills?skill=${encodeURIComponent(item.best_skill_id)}`}>查看最佳候选{item.best_round != null ? ` · 第 ${item.best_round} 轮` : ""}<ExternalLink size={13} aria-hidden="true" /></Link>}</div>
      <div className="skill-iteration-body"><div className="skill-iteration-rounds">
        {!item.rounds.length && <p className="skill-iteration-empty">{item.status === "queued" ? "任务已排队，开始后在这里保留每轮产物和评分。" : "尚无试产结果。"}</p>}
        {item.rounds.map(round => <article className="skill-iteration-round" key={round.number}>
          <header><h2>第 {round.number} 轮{round.number === 1 ? " · 基线" : ""}</h2><Link to={`/skills?skill=${encodeURIComponent(round.skill_id)}`}>v{round.version_number} <ExternalLink size={12} aria-hidden="true" /></Link>
            <span className="skill-iteration-score" data-passed={round.passed}>{round.score == null ? "待评审" : `${round.score.toFixed(1)} / 100${round.passed ? " · 达标" : ""}`}</span></header>
          {round.images.length > 0 && <div className="skill-iteration-images">{round.images.map(image => <a key={image.order} href={apiUrl(image.url)} target="_blank" rel="noopener noreferrer"><img src={apiUrl(image.url)} alt={image.alt || `第 ${round.number} 轮，第 ${image.order} 张`} loading="lazy" /></a>)}</div>}
          {round.feedback && <p className="skill-iteration-feedback">{round.feedback}</p>}
          {round.changes && <div className="skill-iteration-changes"><h3>本轮修改</h3><p>{round.changes}</p></div>}
          {round.scores.length > 0 && <div className="skill-iteration-assessment"><h3>Judge v{round.judge_version ?? item.judge.version}</h3>
            {round.scores.map(score => <div key={score.criterion_id}><span>{item.judge.criteria.find(criterion => criterion.id === score.criterion_id)?.description ?? score.criterion_id}</span><strong>{score.score}</strong><p>{score.evidence}</p></div>)}</div>}
          {round.evaluations && <RoundJudgeHistory evaluations={round.evaluations} criteria={item.judge.criteria} />}
          {round.trial_url && <Link className="skill-iteration-trial-link" to={round.trial_url}>完整试产记录<ExternalLink size={12} aria-hidden="true" /></Link>}
        </article>)}
      </div><aside className="skill-iteration-judge" aria-label="Judge 准则">
        <header><h2><button type="button" aria-expanded={judgeOpen} onClick={() => setJudgeOpen(value => !value)}>评判准则<ChevronDown size={14} aria-hidden="true" className={judgeOpen ? "is-open" : ""} /></button></h2><span>v{item.judge.version}</span></header>
        <p className="skill-iteration-judge-threshold">总分 ≥ {item.judge.threshold}；关键项 ≥ {item.judge.critical_floor}</p>
        {judgeOpen && <>
        {!item.judge.criteria.length && <p>正在根据参考作品建立基础准则。</p>}
        <ol>{item.judge.criteria.map(criterion => <li key={criterion.id}><strong>{criterion.description}{criterion.critical && <small>关键项</small>}</strong><p>{criterion.evidence}</p></li>)}</ol>
        <button type="button" className="skill-iteration-disclosure" aria-expanded={judgeHistory} onClick={() => setJudgeHistory(value => !value)}>准则变更记录<ChevronDown size={14} aria-hidden="true" className={judgeHistory ? "is-open" : ""} /></button>
        {judgeHistory && <div className="skill-iteration-judge-history">{item.judge.history.length ? item.judge.history.map(change => <div key={change.version}><strong>v{change.version} · {stamp(change.at)}</strong><p>{change.reason}</p>
          {change.added_ids.map(criterionId => <p key={criterionId}>新增：{item.judge.criteria.find(criterion => criterion.id === criterionId)?.description ?? criterionId}</p>)}</div>) : <p>尚无追加准则。</p>}</div>}
        </>}
      </aside></div>
      <section className="skill-iteration-technical"><button type="button" className="skill-iteration-disclosure" aria-expanded={technical} onClick={() => setTechnical(value => !value)}>技术记录<ChevronDown size={14} aria-hidden="true" className={technical ? "is-open" : ""} /></button>
        {technical && <><p>DS 用量 · 模型请求 {item.usage.calls.toLocaleString()} 次 · 输入 {item.usage.input_tokens.toLocaleString()} · 输出 {item.usage.output_tokens.toLocaleString()} tokens</p>
          {item.usage.has_unknown_usage && <p>部分调用用量未记录</p>}
          <p>Codex 用量请查看各轮完整试产记录。</p>
          <p>任务 {item.id} · 状态版本 {item.revision} · 更新 {stamp(item.updated_at)}</p>
          <ol>{item.events.map((event, index) => <li key={`${event.time}-${index}`}><time>{stamp(event.time)}</time><strong>{event.message}</strong>{event.details != null && <pre>{typeof event.details === "string" ? event.details : JSON.stringify(event.details, null, 2)}</pre>}</li>)}</ol></>}
      </section>
    </>}
  </section>;
}
