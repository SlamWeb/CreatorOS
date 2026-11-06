import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { RefreshCw, ChevronRight } from "lucide-react";
import { registryApi, type EvalComparison, type EvalResult, type EvalRun } from "../features/eval/registry";
import { LegacyEvalPage } from "./LegacyEvalPage";
import "./eval-workbench.css";

const options = {retry: false, refetchOnWindowFocus: false};
const labels: Record<string, string> = {
  passed: "通过", failed: "失败", needs_review: "待复核", not_run: "未运行", unknown: "未知",
  registered: "已登记", freezing: "冻结中", running: "运行中", completed: "已完成", interrupted: "已中断",
  merged: "已合并", unmerged: "未合并", developing: "开发中", development: "开发中", draft: "开发中",
};
const label = (value: string | null | undefined) => value ? (labels[value] ?? value) : "未记录";
const errorText = (value: unknown) => value instanceof Error ? value.message : "读取失败";
const dateText = (value?: string | null) => value ? value.replace("T", " ").replace(/\.\d+Z$/, " UTC").replace(/Z$/, " UTC") : "未记录";
const latestFirst = (items: EvalRun[]) => [...items].sort((a, b) => b.created_at.localeCompare(a.created_at) || b.id.localeCompare(a.id));
const pretty = (value: unknown) => typeof value === "string" ? value : JSON.stringify(value, null, 2);
const metricValue = (value: number | null | undefined) => typeof value === "number" ? Number(value.toFixed(2)).toLocaleString("zh-CN") : "未记录";
const amountValue = (value: number) => Number(value.toPrecision(4)).toString();
const validAmount = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value) && value >= 0;

function CostComparison({metrics}: {metrics: EvalComparison["metrics"]}) {
  const min = metrics.estimated_cost_min_usd;
  const max = metrics.estimated_cost_max_usd;
  const paired = min?.paired_cases ?? 0;
  const available = paired > 0 && paired === max?.paired_cases && validAmount(min?.baseline_mean) && validAmount(max?.baseline_mean)
    && validAmount(min?.candidate_mean) && validAmount(max?.candidate_mean)
    && min.baseline_mean <= max.baseline_mean && min.candidate_mean <= max.candidate_mean;
  return <div><span>Agent 费用估算（USD）· 每题均值</span><strong>{available ? `$${amountValue(min.baseline_mean!)}–$${amountValue(max.baseline_mean!)} → $${amountValue(min.candidate_mean!)}–$${amountValue(max.candidate_mean!)}` : "未记录"}</strong><small>双方同口径 {available ? paired : 0} 题 · 估算区间</small></div>;
}

function Status({value}: {value: string | null | undefined}) {
  const kind = value && ["passed", "failed", "needs_review", "not_run", "unknown", "running"].includes(value) ? value : "neutral";
  return <span className={`ew-status ${kind}`}>{label(value)}</span>;
}

function ChangeRequirements({run}: {run: EvalRun | undefined}) {
  const value = run?.metadata?.change_requirements;
  const requirements = value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : null;
  const rows = (name: string) => Array.isArray(requirements?.[name]) ? (requirements[name] as unknown[]).filter((entry): entry is Record<string, unknown> => !!entry && typeof entry === "object" && !Array.isArray(entry)) : [];
  const required = rows("required"), recommended = rows("recommended");
  const observedToolRun = run?.metadata?.suite_role === "specialist" && run.metadata.change_scope === "tool"
    && run.results.some(result => ["passed", "failed", "needs_review"].includes(result.status));
  const unknownScopes = Array.isArray(requirements?.unknown_scopes) ? requirements.unknown_scopes.filter((scope): scope is string => typeof scope === "string") : [];
  const renderRows = (items: Record<string, unknown>[]) => items.map((item, index) => {
    const id = typeof item.id === "string" ? item.id : "";
    const title = typeof item.title === "string" ? item.title : id || "未记录名称";
    const level = typeof item.level === "string" ? item.level : "未记录类型";
    return <li key={`${id}-${index}`}><span><strong>{title}</strong><small>{level}</small></span><span className="ew-requirement-status">{id === "tool_calling" && observedToolRun ? "已有专项运行" : "未验证"}</span></li>;
  });
  return <section className="ew-requirements" aria-label="该运行登记的检查要求">
    <div className="ew-section-head"><h2>该运行登记的检查要求</h2><span className="ew-muted">{run ? `运行 ${run.id}` : "未验证"}</span></div>
    {!requirements ? <p className="ew-muted">此运行未记录检查要求，改动范围和必要检查未验证。</p> : <>
      <p className="ew-muted">规则版本 {typeof requirements.rule_version === "string" ? requirements.rule_version : "未记录"} · 登记的检查要求不代表已经通过</p>
      {requirements.coverage_known !== true && <p className="ew-requirement-warning">改动范围未完整识别，检查要求未验证{unknownScopes.length ? `：${unknownScopes.join("、")}` : "。"}</p>}
      <div className="ew-requirement-columns"><div><h3>必要检查</h3>{required.length ? <ul>{renderRows(required)}</ul> : <p className="ew-muted">未记录必要检查，尚未验证。</p>}</div><div><h3>建议检查</h3>{recommended.length ? <ul>{renderRows(recommended)}</ul> : <p className="ew-muted">未记录建议检查。</p>}</div></div>
    </>}
  </section>;
}

function ResultDetails({run, item}: {run: EvalRun; item: EvalResult | undefined}) {
  if (!item) return <div className="ew-result-detail"><p>此题尚无结果。</p></div>;
  const metrics = item.metrics ?? {};
  const evidence = item.evidence ?? {};
  const actualRequests = Array.isArray(evidence.actual_user_requests) ? evidence.actual_user_requests.filter((value): value is string => typeof value === "string" && value.length > 0) : [];
  const templateRequests = Array.isArray(evidence.user_requests) ? evidence.user_requests.filter((value): value is string => typeof value === "string") : [];
  const requests = actualRequests.length ? actualRequests : templateRequests;
  const judgments = Array.isArray(evidence.judgments) ? evidence.judgments.filter((value): value is Record<string, unknown> => !!value && typeof value === "object" && !Array.isArray(value)) : [];
  const textValue = (value: unknown) => typeof value === "string" ? value : null;
  return <div className="ew-result-detail">
    <div className="ew-detail-header"><div><span className="ew-eyebrow">题目</span><h3>{item.case_id}</h3></div><Status value={item.status} /></div>
    <p>{item.summary || "本次未保存判定说明。"}</p>
    <p className="ew-muted">运行 {run.id} · 题目版本 {item.case_definition_hash ?? "未记录"}</p>
    <section className="ew-case-summary"><h4>用户请求</h4>{requests.length ? <>{requests.map((request, index) => <p key={index}>{request}</p>)}<p className="ew-muted">{actualRequests.length ? "实际发送的用户请求。" : "展示冻结题面模板；尚无变量展开后的实际请求。"}</p></> : <p className="ew-muted">本次未记录用户请求。</p>}</section>
    <section className="ew-case-summary"><h4>判定</h4><div className="ew-verdicts"><span>程序 <Status value={textValue(metrics.program_status) ?? textValue(evidence.program_status)} /></span><span>语义 <Status value={textValue(metrics.semantic_status) ?? textValue(evidence.semantic_status)} /></span><span>关键门禁 <Status value={textValue(metrics.critical_status) ?? textValue(evidence.critical_status)} /></span></div>
      {judgments.length > 0 && <ul>{judgments.map((entry, index) => <li key={index}>{textValue(entry.label) ?? textValue(entry.question) ?? textValue(entry.id) ?? "评分项"} · {label(textValue(entry.status))}{textValue(entry.detail) && <p className="ew-muted">{textValue(entry.detail)}</p>}</li>)}</ul>}</section>
    <details><summary>指标与用量</summary>{Object.keys(metrics).length ? <pre>{pretty(metrics)}</pre> : <p className="ew-muted">本次未记录指标。</p>}</details>
    <details><summary>评分依据与证据</summary>{Object.keys(evidence).length ? <pre>{pretty(evidence)}</pre> : <p className="ew-muted">本次未提供证据。</p>}</details>
  </div>;
}

function RunView({run, runs, caseId, onRun, onCase}: {run: EvalRun; runs: EvalRun[]; caseId: string; onRun: (id: string) => void; onCase: (id: string) => void}) {
  const item = run.results.find(result => result.case_id === caseId);
  const unplanned = run.results.filter(result => !run.planned_cases.includes(result.case_id));
  return <section className="ew-run-view" aria-label="运行详情">
    <div className="ew-section-head"><div><p className="ew-eyebrow">测试集</p><h2>{run.suite_id}</h2></div><button type="button" className="ew-link" onClick={() => onRun("")}>返回测试结果</button></div>
    <div className="ew-run-meta"><label>运行记录 <select aria-label="运行记录" value={run.id} onChange={e => onRun(e.target.value)}>{runs.map((entry, index) => <option key={entry.id} value={entry.id}>{index === 0 ? "最新 · " : ""}{dateText(entry.created_at)} · {label(entry.display_status ?? entry.status)} · {entry.id}</option>)}</select></label>
      <span>题集版本 {run.suite_version}</span><span>计划 {run.planned_cases.length} 题 · 已记录 {run.results.length} 题</span><Status value={run.display_status ?? run.status} /></div>
    {run.display_status === "unknown" && run.status !== "unknown" && <p className="ew-muted">运行状态未确认，最后登记状态：{label(run.status)}；不会自动重试。</p>}
    {runs.length > 1 && <p className="ew-muted">本测试集有 {runs.length} 次运行；先前结果保留，最近一次不代表稳定通过。</p>}
    <div className="ew-case-layout"><div className="ew-case-list" aria-label="题目列表">
      {run.planned_cases.map(id => {const result = run.results.find(entry => entry.case_id === id); return <button key={id} type="button" className={caseId === id ? "is-selected" : ""} aria-current={caseId === id ? "true" : undefined} onClick={() => onCase(id)}><span>{id}</span><Status value={result?.status ?? "not_run"} /><ChevronRight size={16} aria-hidden="true" /></button>;})}
      {unplanned.length > 0 && <p role="alert">存在 {unplanned.length} 条计划外结果，请核对运行记录。</p>}
      {!run.planned_cases.length && <p className="ew-muted">本次未记录计划题目。</p>}
    </div><div>{caseId ? <ResultDetails run={run} item={item} /> : <p className="ew-muted ew-choose-case">选择一道题，查看结果和证据。</p>}</div></div>
  </section>;
}

function Workbench() {
  const [params, setParams] = useSearchParams();
  const rounds = useQuery({queryKey: ["eval-registry", "rounds"], queryFn: ({signal}) => registryApi.rounds(signal), ...options});
  const roundId = params.get("round") ?? rounds.data?.items[0]?.id ?? "";
  const detail = useQuery({queryKey: ["eval-registry", "round", roundId], queryFn: ({signal}) => registryApi.round(roundId, signal), enabled: !!roundId, ...options});
  const selected = detail.data?.round;
  const baseId = params.get("base") ?? selected?.parent_round_id ?? "";
  const baseline = useQuery({queryKey: ["eval-registry", "round", baseId], queryFn: ({signal}) => registryApi.round(baseId, signal), enabled: !!baseId && baseId !== roundId, ...options});
  const update = (values: Record<string, string | null>) => setParams(current => {const next = new URLSearchParams(current); Object.entries(values).forEach(([key, value]) => value == null ? next.delete(key) : next.set(key, value)); if (!next.has("round") && next.get("view") !== "history" && roundId) next.set("round", roundId); return next;});
  const changeRound = (id: string) => update({round: id, base: null, run: null, case: null, compare_run: null, base_run: null});
  const ordered = latestFirst(detail.data?.runs ?? []);
  const grouped = new Map<string, EvalRun[]>();
  ordered.forEach(run => grouped.set(run.suite_id, [...(grouped.get(run.suite_id) ?? []), run]));
  const runId = params.get("run") ?? "";
  const run = ordered.find(entry => entry.id === runId);
  const caseId = params.get("case") ?? "";
  const candidateId = params.get("compare_run") ?? "";
  const candidateRun = ordered.find(entry => entry.id === candidateId);
  const baseRuns = latestFirst(baseline.data?.runs ?? []);
  const baselineRunId = params.get("base_run") ?? "";
  const baselineRun = baseRuns.find(entry => entry.id === baselineRunId);
  const comparison = useQuery({queryKey: ["eval-registry", "compare", baselineRunId, candidateId], queryFn: ({signal}) => registryApi.compare(baselineRunId, candidateId, signal), enabled: !!baselineRun && !!candidateRun && baselineRunId !== candidateId, ...options});
  const refresh = () => {void rounds.refetch(); if (roundId) void detail.refetch(); if (baseId && baseId !== roundId) void baseline.refetch(); if (baselineRun && candidateRun) void comparison.refetch();};
  return <main className="eval-hub">
    <header className="ew-top"><div><p className="ew-eyebrow">CREATOROS / EVAL</p><h1>评测工作台</h1></div><div className="ew-top-actions"><button type="button" className="ew-link" onClick={() => update({view: "history", round: null, base: null, run: null, case: null})}>历史 Benchmark</button><button type="button" className="ew-refresh" onClick={refresh} disabled={rounds.isFetching || detail.isFetching}><RefreshCw size={15} />刷新</button></div></header>
    {rounds.isPending && <p role="status" className="ew-message">正在读取评测记录…</p>}
    {rounds.isError && <div role="alert" className="ew-message"><p>{errorText(rounds.error)}</p><button type="button" onClick={() => void rounds.refetch()}>重试</button></div>}
    {rounds.data && rounds.data.items.length === 0 && <div className="ew-empty"><h2>还没有正式评测记录</h2><p>登记并运行正式评测后，结果会显示在这里。</p><button type="button" className="ew-link" onClick={() => update({view: "history"})}>查看历史 Benchmark <ChevronRight size={15} /></button></div>}
    {rounds.data && rounds.data.items.length > 0 && <div className="ew-layout">
      <aside className="ew-rounds" aria-label="更新轮次"><div className="ew-section-head"><h2>更新轮次</h2><span className="ew-muted">{rounds.data.items.length} 轮</span></div>
        <label className="ew-mobile-select">选择轮次<select value={roundId} onChange={e => changeRound(e.target.value)}>{rounds.data.items.map(round => <option key={round.id} value={round.id}>{round.title}</option>)}</select></label>
        <nav aria-label="轮次列表">{rounds.data.items.map(round => <button className={`ew-round ${round.id === roundId ? "is-selected" : ""}`} type="button" key={round.id} aria-current={round.id === roundId ? "true" : undefined} onClick={() => changeRound(round.id)}><span className="ew-round-row"><strong>{round.title}</strong><span>{label(round.merge_status)}</span></span><small>{dateText(round.created_at)}</small><small>{round.branch || "未记录分支"}</small></button>)}</nav>
      </aside>
      <div className="ew-content">
        {detail.isPending && <p role="status" className="ew-message">正在读取轮次…</p>}
        {detail.isError && <div role="alert" className="ew-message"><p>{errorText(detail.error)}</p><button type="button" onClick={() => void detail.refetch()}>重试</button></div>}
        {selected && <><div className="ew-title"><div><p className="ew-eyebrow">更新轮次 / {selected.id}</p><h2>{selected.title}</h2><p>{selected.change_summary || "未记录改动说明。"}</p><p className="ew-muted">版本 {selected.code_version || "未记录"} · {selected.branch || "未记录分支"} · {dateText(selected.created_at)}</p></div><Status value={selected.merge_status} /></div>
          <div className="ew-notice"><strong>整体结论未提供</strong><p>请查看各测试集的实际结果及未运行项。</p></div>
          <ChangeRequirements run={ordered[0]} />
          <section className="ew-compare" aria-label="版本比较"><div className="ew-section-head"><h2>与基准比较</h2><label>对比 <select aria-label="选择对比版本" value={baseId} onChange={e => update({base: e.target.value, base_run: null})}><option value="">选择基准</option>{baseId && !rounds.data.items.some(item => item.id === baseId) && <option value={baseId}>基准不可用 · {baseId}</option>}{rounds.data.items.filter(item => item.id !== roundId).map(item => <option key={item.id} value={item.id}>{item.title} · {item.branch || "未记录分支"}</option>)}</select></label></div>
            {!baseId ? <p className="ew-muted">没有记录上一轮，请选择对比版本。</p> : baseId === roundId ? <p role="alert">请选择不同的对比版本。</p> : baseline.isPending ? <p role="status" className="ew-muted">正在读取基准…</p> : baseline.isError ? <p role="alert">基准读取失败：{errorText(baseline.error)} <button type="button" onClick={() => void baseline.refetch()}>重试</button></p> : baseline.data && <>
              <div className="ew-compare-selects"><label>当前运行<select aria-label="选择当前运行" value={candidateRun?.id ?? ""} onChange={e => update({compare_run: e.target.value, base_run: null})}><option value="">选择运行</option>{ordered.map(entry => <option key={entry.id} value={entry.id}>{entry.suite_id} · {dateText(entry.created_at)} · {entry.id}</option>)}</select></label><label>基准运行<select aria-label="选择基准运行" value={baselineRun?.id ?? ""} onChange={e => update({base_run: e.target.value})}><option value="">选择运行</option>{baseRuns.map(entry => <option key={entry.id} value={entry.id}>{entry.suite_id} · {dateText(entry.created_at)} · {entry.id}</option>)}</select></label></div>
              {!candidateRun || !baselineRun ? <p className="ew-muted">选择两次运行比较；当前尚未核实哪次是本轮首试。</p> : comparison.isPending ? <p role="status" className="ew-muted">正在核对比较条件…</p> : comparison.isError ? <p role="alert">无法比较：{errorText(comparison.error)} <button type="button" onClick={() => void comparison.refetch()}>重试</button></p> : comparison.data && <>
                {!comparison.data.contract_compatible && <p className="ew-comparison-note">比较条件不完整或不同，不能计算涨跌。{comparison.data.contract_differences.map(item => `${item.field}（${item.reason === "missing" ? "缺失" : "不同"}）`).join("、")}</p>}
                <div className="ew-compare-grid"><div><span>可比共同题</span><strong>{comparison.data.comparable_cases} / {comparison.data.common_planned}</strong><small>计划：基准 {comparison.data.baseline_planned} · 当前 {comparison.data.candidate_planned}</small></div><div><span>明确判定通过</span><strong>{comparison.data.outcomes.paired_decidable ? `${comparison.data.outcomes.baseline_passed} → ${comparison.data.outcomes.candidate_passed}` : "未记录"}</strong><small>双方均明确判定 {comparison.data.outcomes.paired_decidable} 题</small></div>{[["input_tokens", "输入 Token"], ["output_tokens", "输出 Token"], ["elapsed_seconds", "耗时（秒）"], ["tool_calls", "工具调用次数"]].map(([key, title]) => {const metric = comparison.data!.metrics[key]; return <div key={key}><span>{title} · 每题均值</span><strong>{metric?.paired_cases ? `${metricValue(metric.baseline_mean)} → ${metricValue(metric.candidate_mean)}` : "未记录"}</strong><small>双方同口径 {metric?.paired_cases ?? 0} 题</small></div>;})}<CostComparison metrics={comparison.data.metrics} /></div>
                <details className="ew-compare-details"><summary>查看逐题与比较依据</summary><ul>{comparison.data.cases.map(item => <li key={item.case_id}>{item.case_id} · {label(item.baseline_status)} → {label(item.candidate_status)} · {item.comparable ? "条件可比" : `不可比（${item.reason ?? "依据缺失"}）`}</li>)}</ul></details>
              </>}
            </>}
          </section>
          {runId ? run ? <RunView run={run} runs={ordered.filter(entry => entry.suite_id === run.suite_id)} caseId={caseId} onRun={id => update({run: id || null, case: null})} onCase={id => update({case: id})} /> : <div role="alert" className="ew-message">此轮没有该运行记录。<button type="button" onClick={() => update({run: null, case: null})}>返回测试结果</button></div> : <section aria-label="测试结果"><div className="ew-section-head"><h2>测试结果</h2><span className="ew-muted">{ordered.length} 次运行</span></div>
            {grouped.size === 0 ? <div className="ew-empty ew-small"><p>这一轮尚无已登记的运行。</p></div> : <div className="ew-suites">{Array.from(grouped.entries()).map(([suite, entries]) => {const latest = entries[0]; const counts = latest.planned_cases.reduce<Record<string, number>>((acc, id) => {const key = latest.results.find(result => result.case_id === id)?.status ?? "not_run"; acc[key] = (acc[key] ?? 0) + 1; return acc;}, {}); return <button type="button" key={suite} className="ew-suite" onClick={() => update({run: latest.id, case: null})}><span className="ew-suite-name"><strong>{suite}</strong><small>最新运行 · {entries.length} 次记录 · 版本 {latest.suite_version}</small></span><span className="ew-suite-count">{Object.entries(counts).map(([key, count]) => `${count} ${label(key)}`).join(" · ") || "未记录题目"}</span><Status value={latest.display_status ?? latest.status} /><ChevronRight size={16} aria-hidden="true" /></button>;})}</div>}
          </section>}
        </>}
      </div>
    </div>}
  </main>;
}

export function EvalPage() {
  const [params] = useSearchParams();
  return params.get("view") === "history" || params.has("batch") || (params.has("case") && !params.has("round") && !params.has("run")) ? <LegacyEvalPage /> : <Workbench />;
}
