import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { RefreshCw } from "lucide-react";
import { evalApi, type BenchmarkCase, type Score, type Scores } from "../features/eval/api";
import "./eval.css";

const options = {retry: false, refetchOnWindowFocus: false};
const statusLabels: Record<string, string> = {passed: "通过", failed: "失败", needs_review: "待复核", missing: "未运行", completed: "已完成"};
const categoryLabels: Record<string, string> = {tools: "工具调用", security: "账号边界", state: "状态管理", persistence: "保存与交付"};
const scopeLabels: Record<string, string> = {outcome: "最终结果", trajectory: "结果与关键链路", multi_turn: "多轮结果与链路"};
const errorText = (error: unknown) => error instanceof Error ? error.message : "读取失败。";
const text = (value: unknown) => typeof value === "string" ? value : JSON.stringify(value, null, 2);

function ScoreCard({label, value}: {label: string; value?: Score}) {
  const score = value?.score;
  return <div className="eval-score-card"><span>{label}</span><strong>{typeof score === "number" ? `${score.toFixed(1)} / 100` : "未提供完整分数"}</strong>
    {value && <small>覆盖率 {typeof value.coverage === "number" ? `${(value.coverage * 100).toFixed(0)}%` : "未记录"}{typeof value.observed_score === "number" && score == null ? ` · 已测均分 ${value.observed_score.toFixed(1)}` : ""}</small>}
  </div>;
}
function ScoreItems({label, value}: {label: string; value?: Score}) {
  if (!value?.items?.length) return null;
  return <details className="eval-score-items"><summary>{label}逐项结果（{value.items.length} 项）</summary><ul>{value.items.map(item => <li key={item.id}>
    <span className={`eval-status ${item.status}`}>{statusLabels[item.status] ?? item.status}</span> {item.label ?? item.question ?? item.id}{item.critical && <small>关键项</small>}
    {item.detail && <p className="eval-muted">{item.detail}</p>}
  </li>)}</ul></details>;
}
function ScorePair({scores}: {scores?: Scores | null}) {
  return <><div className="eval-scores"><ScoreCard label="程序分" value={scores?.program} /><ScoreCard label="语义分" value={scores?.semantic} /></div>
    <p className="eval-muted">{scores?.protocol ? `评分协议：${scores.protocol}。` : "本批未提供分数，保留历史通过／失败结果。"} 两项分开展示，尚未设置综合权重。</p>
    {scores?.critical_status && <p>关键项：{statusLabels[scores.critical_status] ?? scores.critical_status}</p>}
    <ScoreItems label="程序" value={scores?.program} /><ScoreItems label="语义" value={scores?.semantic} /></>;
}
function Definition({item}: {item: BenchmarkCase}) {
  return <div className="eval-definition">
    <section><h3>用户要什么</h3><p>{item.intent ?? "该版本尚未提供独立意图说明。"}</p>
      {item.steps.map((step, index) => <blockquote key={index}><span>{step.kind === "user" ? `用户请求 ${index + 1}` : `事件 ${index + 1}`}</span><p>{step.text ?? step.details ?? step.name}</p></blockquote>)}
    </section>
    <section><h3>理应得到什么</h3>{Array.isArray(item.expected_outcome) ? <ul>{item.expected_outcome.map((line, index) => <li key={index}>{line}</li>)}</ul> : <p>{item.expected_outcome ?? "该版本未单列预期结果。"}</p>}
      <p className="eval-muted">检查范围：{scopeLabels[item.evaluation_scope ?? ""] ?? item.evaluation_scope ?? "旧协议未声明"}{item.live_codex ? " · 包含真实 Codex" : ""}</p>
    </section>
    <div className="eval-rule-columns"><section><h3>程序怎么判</h3>{item.scoring?.program_checks?.length ? <ul>{item.scoring.program_checks.map(rule => <li key={rule.id}>{rule.label}{rule.critical && <small>关键项</small>}</li>)}</ul> : <p className="eval-muted">此版本尚未单列计分检查。</p>}</section>
      <section><h3>语义怎么判</h3><ul>{item.rubric.map(rule => <li key={rule.id}>{rule.question}{rule.critical && <small>关键项</small>}</li>)}</ul></section></div>
  </div>;
}
function EvidenceReader({batch, caseId, record, name}: {batch: string; caseId: string; record: string; name: string}) {
  const [open, setOpen] = useState(false);
  const result = useQuery({queryKey: ["benchmark", "evidence", batch, caseId, record, name],
    queryFn: ({signal}) => evalApi.evidence(batch, caseId, record, name, signal), enabled: open, ...options});
  return <div className="eval-evidence"><button type="button" aria-expanded={open} onClick={() => setOpen(!open)}>{open ? "收起" : "读取"} {name}</button>
    {open && <div>{result.isPending && <p role="status">正在读取…</p>}{result.isError && <p role="alert">{errorText(result.error)} <button type="button" onClick={() => void result.refetch()}>重试</button></p>}
      {result.data && <pre>{text(result.data.content)}</pre>}</div>}</div>;
}

export function EvalPage() {
  const [params, setParams] = useSearchParams();
  const [showCases, setShowCases] = useState(false);
  const overview = useQuery({queryKey: ["benchmark", "overview"], queryFn: ({signal}) => evalApi.overview(signal), ...options});
  const caseId = params.get("case") ?? "B11";
  const selected = overview.data?.cases.find(item => item.id === caseId);
  const batchId = params.get("batch") ?? "";
  const batch = useQuery({queryKey: ["benchmark", "batch", batchId], queryFn: ({signal}) => evalApi.batch(batchId, signal), enabled: !!batchId, ...options});
  const planned = batch.data?.summary.cases.some(item => item.case_id === caseId);
  const detail = useQuery({queryKey: ["benchmark", "detail", batchId, caseId], queryFn: ({signal}) => evalApi.detail(batchId, caseId, signal), enabled: !!batchId && planned === true, ...options});
  const navigate = (name: string, value: string) => setParams(current => {const next = new URLSearchParams(current); if (value) next.set(name, value); else next.delete(name); return next;});
  const refresh = () => {void overview.refetch(); if (batchId) void batch.refetch(); if (planned) void detail.refetch();};
  return <main className="eval-page">
    <header className="eval-page-header"><div><h1>Agent Benchmark</h1><p className="eval-muted">当前 50 题 · 程序与语义分别计分</p></div><button className="eval-refresh" type="button" onClick={refresh} disabled={overview.isFetching || batch.isFetching || detail.isFetching}><RefreshCw size={15} />刷新记录</button></header>
    {overview.isPending && <p role="status">正在读取题集…</p>}
    {overview.isError && <div role="alert"><p>{errorText(overview.error)}</p><button type="button" onClick={() => void overview.refetch()}>重新读取</button></div>}
    {overview.data && <>
      <p className="eval-muted eval-dataset">{overview.data.dataset_id} · {overview.data.cases.length} 题 · 只读查看，不会执行模型或修改旧分数</p>
      {overview.data.errors.map(error => <p role="alert" key={error.batch_id}>{error.batch_id}：{error.message}</p>)}
      <label className="eval-batch-select">运行批次<select value={batchId} onChange={event => navigate("batch", event.target.value)}><option value="">只看现行标准</option>{overview.data.batches.map(item => <option key={item.id} value={item.id}>{item.id} · {statusLabels[item.overall_status ?? ""] ?? item.overall_status ?? "状态未记录"}</option>)}</select></label>
      {batchId && <section aria-label="批次结果" className="eval-batch-result">{batch.isPending && <p role="status">正在读取批次…</p>}{batch.isError && <p role="alert">{errorText(batch.error)} <button type="button" onClick={() => void batch.refetch()}>重试</button></p>}
        {batch.data && <><h2>本批结果</h2><p className="eval-muted">本批题集：{batch.data.summary.dataset_id} · 计划 {batch.data.summary.planned} 题 · 已记录 {batch.data.summary.with_results} 题</p><ScorePair scores={batch.data.summary.scores} /><p>历史结果：通过 {batch.data.summary.passed} · 失败 {batch.data.summary.failed} · 待复核 {batch.data.summary.needs_review}</p><p className="eval-muted">{batch.data.score_note}</p></>}
      </section>}
      <div className="eval-workbench"><aside className={`eval-case-panel ${showCases ? "expanded" : ""}`} aria-label="评测题目"><button className="eval-mobile-toggle" type="button" aria-expanded={showCases} onClick={() => setShowCases(!showCases)}>{showCases ? "收起题目" : "选择题目"}</button>
        <nav aria-label="50 道评测题目">{overview.data.cases.map(item => <button key={item.id} className={`eval-case ${item.id === caseId ? "selected" : ""}`} type="button" aria-current={item.id === caseId ? "true" : undefined} onClick={() => {navigate("case", item.id); setShowCases(false);}}><span>{item.id}</span>{item.title}</button>)}</nav>
      </aside><section className="eval-detail" aria-label="题目详情">{!selected ? <p role="alert">题目不存在，请从列表选择。</p> : <>
        <header><p className="eval-muted">{selected.id} · {categoryLabels[selected.category] ?? selected.category} · {selected.split === "dev" ? "开发题" : "公开验收题"}</p><h2>{selected.title}</h2></header>
        <section aria-label="现行评分标准"><Definition item={selected} /></section>
        {batchId && batch.data && !planned && <p className="eval-muted">本批未计划这道题。</p>}
        {planned && <section className="eval-case-result" aria-label="本题运行结果"><h2>本题运行结果</h2>{detail.isPending && <p role="status">正在读取结果…</p>}{detail.isError && <p role="alert">{errorText(detail.error)} <button type="button" onClick={() => void detail.refetch()}>重试</button></p>}
          {detail.data && <><p>本批状态：{statusLabels[detail.data.row.status] ?? detail.data.row.status}</p><ScorePair scores={detail.data.row.scores} />
            <details><summary>本批实际使用的冻结标准</summary>{detail.data.frozen_definition ? <Definition item={detail.data.frozen_definition} /> : <p className="eval-muted">冻结题目未保存，不能用现行定义冒充本批标准。</p>}</details>
            {detail.data.errors.map((line, index) => <p role="alert" key={index}>{line}</p>)}
            {detail.data.records.map(record => <article key={record.record_id} className="eval-record"><h3>原始检查与诊断（不全计入业务分）</h3><ul className="eval-record-checks">{record.report.checks?.map((check, index) => <li key={`${check.id}-${index}`}><span className={`eval-status ${check.status}`}>{statusLabels[check.status] ?? check.status}</span> {check.label}<p className="eval-muted">{check.detail}</p></li>)}</ul>
              <div className="eval-evidence-list">{record.evidence_files.map(name => <EvidenceReader key={`${batchId}-${caseId}-${record.record_id}-${name}`} batch={batchId} caseId={caseId} record={record.record_id} name={name} />)}</div>
            </article>)}
          </>}
        </section>}
      </>}</section></div>
    </>}
  </main>;
}
