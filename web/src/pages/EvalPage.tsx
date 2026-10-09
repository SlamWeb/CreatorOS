import { useEffect, useId, useState, type FormEvent, type ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { Copy, Minus, Plus, RefreshCw } from "lucide-react";
import { ApiError } from "../api/client";
import { evalApi, type EvalCase, type EvalReadError, type EvalReport, type EvalRunSummary } from "../features/eval/api";
import "./eval.css";

const categories = [
  { id: "security", label: "账号边界" }, { id: "persistence", label: "会话持久化" },
  { id: "state", label: "状态更新" }, { id: "tools", label: "工具正确性" },
];
const statusLabels: Record<string, string> = {
  not_run: "未运行", passed: "通过", failed: "失败", needs_review: "待复核", evidence_missing: "缺证据",
};
const dimensions: Record<string, string> = {
  task_success: "任务完成", boundary_enforced: "账号边界", state_consistent: "状态一致", protocol_valid: "工具协议",
};
const readOptions = { retry: false, refetchOnWindowFocus: false };
const message = (error: unknown) => error instanceof Error ? error.message : "读取失败，请重新读取。";
const dateLabel = (value?: string) => value ? new Date(value).toLocaleString("zh-CN", { hour12: false }) : "时间未记录";

function Status({ value }: { value: string }) {
  return <span className={`eval-status ${value}`}><i aria-hidden="true" />{statusLabels[value] ?? value}</span>;
}

function ReadError({ error, onRetry, label = "重新读取" }: { error: unknown; onRetry: () => void; label?: string }) {
  return <div className="eval-error" role="alert"><p>{message(error)}</p><button type="button" onClick={onRetry}>{label}</button></div>;
}

function Reveal({ label, className = "", children }: { label: string; className?: string; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  return <div className={className} data-open={open}>
    <button className="eval-reveal" type="button" aria-expanded={open} aria-controls={id} onClick={() => setOpen(!open)}>
      <span>{label}</span>{open ? <Minus size={14} aria-hidden="true" /> : <Plus size={14} aria-hidden="true" />}
    </button>
    <div id={id} hidden={!open}>{open && children}</div>
  </div>;
}

function SavedErrors({ errors }: { errors?: EvalReadError[] }) {
  if (!errors?.length) return null;
  return <Reveal className="eval-saved-errors" label={`${errors.length} 份历史报告无法读取`}>
    <ul>{errors.map(item => <li key={item.run_id}><code>{item.run_id}</code>：{item.message}</li>)}</ul>
  </Reveal>;
}

function LongText({ text, code = false }: { text: string; code?: boolean }) {
  const [expanded, setExpanded] = useState(false);
  const long = text.length > 650;
  return <div className="eval-long-text">
    <pre className={`${code ? "eval-code" : "eval-prose"} ${long && !expanded ? "eval-text-preview" : ""}`}>{long && !expanded ? `${text.slice(0, 650)}…` : text}</pre>
    {long && <button className="eval-text-action" type="button" aria-expanded={expanded} onClick={() => setExpanded(!expanded)}>{expanded ? "收起全文" : "展开全文"}<span>{text.length.toLocaleString()} 字符</span></button>}
  </div>;
}

function CaseDefinition({ definition }: { definition: EvalCase["definition"] }) {
  return <Reveal className="eval-case-definition" label="题目与验收标准">
    {!definition ? <p className="eval-muted">题目定义未记录。</p> : <div className="eval-definition-content">
      <section><h3>测试步骤</h3><ol>{definition.steps.map((step, index) => <li key={index}>
        <h4>{step.kind === "user" ? "用户输入" : step.kind === "event" ? "控制事件" : step.kind}{step.name ? ` · ${step.name}` : ` · ${index + 1}`}</h4>
        {step.text && <LongText text={step.text} />}{step.details && <LongText text={step.details} />}
      </li>)}</ol></section>
      <section><h3>程序检查标准</h3><ul>{definition.assertions.map(assertion => <li key={assertion.id}>
        <h4>{assertion.id}</h4><LongText text={assertion.pass} />
        <p className="eval-definition-evidence">所需证据：<code>{assertion.evidence.join(" · ")}</code></p>
      </li>)}</ul></section>
      <section><h3>人工检查标准</h3>{definition.manual_checks.length ? <ul>{definition.manual_checks.map((item, index) => <li key={index}><LongText text={item} /></li>)}</ul> : <p className="eval-muted">未记录人工检查标准。</p>}</section>
    </div>}
  </Reveal>;
}

function Evidence({ runId, digest, file, open, onToggle }: {
  runId: string; digest: string; file: { name: string; label: string }; open: boolean; onToggle: (open: boolean) => void;
}) {
  const id = useId();
  const query = useQuery({ queryKey: ["eval", "evidence", runId, digest, file.name], queryFn: ({ signal }) => evalApi.evidence(runId, file.name, signal), enabled: open, ...readOptions });
  const content = query.data ? typeof query.data.content === "string" ? query.data.content : JSON.stringify(query.data.content, null, 2) : "";
  return <div className="eval-evidence" data-open={open}>
    <button className="eval-reveal" type="button" aria-expanded={open} aria-controls={id} onClick={() => onToggle(!open)}>
      <span>{file.label || file.name}{file.label && file.label !== file.name && <small>{file.name}</small>}</span>
      {open ? <Minus size={14} aria-hidden="true" /> : <Plus size={14} aria-hidden="true" />}
    </button>
    <div id={id} className="eval-evidence-content" hidden={!open}>{open && <>
      {query.isPending && <p className="eval-muted" role="status">正在读取原始证据…</p>}
      {query.isError ? <ReadError error={query.error} onRetry={() => void query.refetch()} /> : query.data && <LongText text={content} code />}
    </>}</div>
  </div>;
}

function Review({ report, readFailed, onSaved, onReload }: { report: EvalReport; readFailed: boolean; onSaved: (report: EvalReport) => void; onReload: () => Promise<EvalReport | undefined> }) {
  const [decision, setDecision] = useState<"passed" | "failed" | "">(report.review?.decision ?? "");
  const [note, setNote] = useState(report.review?.note ?? "");
  const [digest, setDigest] = useState(report.report_digest);
  const [saving, setSaving] = useState(false);
  const [reading, setReading] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [conflict, setConflict] = useState(false);
  const stale = conflict || digest !== report.report_digest;
  const canPass = report.auto_status === "passed" && report.execution_status === "completed";

  async function reload() {
    setReading(true); setNotice("");
    const current = await onReload();
    if (current) { setDigest(current.report_digest); setConflict(false); setError(""); setNotice("已重新读取报告，复核草稿已保留。请核对最新证据后再保存。"); }
    setReading(false);
  }
  async function save(event: FormEvent) {
    event.preventDefault();
    if (!decision || !note.trim() || saving || stale || readFailed || (decision === "passed" && !canPass)) return;
    setSaving(true); setError(""); setNotice("");
    try {
      const saved = await evalApi.review(report.run_id, { expected_digest: digest, decision, note });
      setDigest(saved.report_digest); onSaved(saved); setNotice("人工复核已保存。");
    } catch (failure) {
      const changed = failure instanceof ApiError && failure.status === 409;
      setConflict(changed || !(failure instanceof ApiError) || failure.status === 0 || failure.status >= 500);
      setError(changed ? "报告已变化，尚未保存复核。草稿已保留，请重新读取并核对。" : `${message(failure)} 复核草稿已保留；请读取当前报告核对保存结果。`);
    } finally { setSaving(false); }
  }
  return <section className="eval-review" aria-label="人工复核">
    <div className="eval-section-heading"><h3>人工复核</h3><span>{report.review ? `最近保存于 ${dateLabel(report.review.reviewed_at)}` : "尚未签署"}</span></div>
    {report.manual_checks.length > 0 ? <ul className="eval-manual-checks">{report.manual_checks.map((item, index) => <li key={index}><LongText text={item} /></li>)}</ul> : <p className="eval-muted">报告未记录人工检查标准。</p>}
    {!canPass && <p className="eval-review-limit">自动判分未通过或执行失败，不能确认通过。</p>}
    <form onSubmit={event => void save(event)}>
      <label>复核结论<select value={decision} disabled={saving || reading} onChange={event => { setDecision(event.target.value as typeof decision); setNotice(""); }}>
        <option value="">请选择结论</option><option value="passed" disabled={!canPass}>确认通过</option><option value="failed">确认失败</option>
      </select></label>
      <label>复核备注<textarea rows={3} value={note} required disabled={saving || reading} onChange={event => { setNote(event.target.value); setNotice(""); }} placeholder="记录检查依据、问题或保留意见" /></label>
      {error && <p className="eval-review-error" role="alert">{error}</p>}
      {notice && <p className="eval-review-notice" role="status">{notice}</p>}
      {stale && !error && <p className="eval-review-error" role="alert">报告已更新，请重新读取并核对；复核草稿已保留。</p>}
      <div className="eval-review-actions"><button className="button button-primary" type="submit" disabled={!decision || !note.trim() || saving || reading || stale || readFailed || (decision === "passed" && !canPass)}>{saving ? "正在保存…" : "保存复核"}</button>
        <button className="eval-secondary" type="button" disabled={saving || reading} onClick={() => void reload()}>{reading ? "正在读取…" : "读取最新报告"}</button></div>
    </form>
  </section>;
}

function Report({ report, readFailed, onSaved, onReload }: { report: EvalReport; readFailed: boolean; onSaved: (report: EvalReport) => void; onReload: () => Promise<EvalReport | undefined> }) {
  const [opened, setOpened] = useState<Set<string>>(new Set());
  const setOpen = (name: string, open: boolean) => setOpened(current => { const next = new Set(current); if (open) next.add(name); else next.delete(name); return next; });
  return <div className="eval-report">
    <div className="eval-report-heading"><h3>运行结果</h3><Status value={report.status} /></div>
    <dl className="eval-run-meta">
      <div><dt>执行</dt><dd>{report.execution_mode === "live" ? "真实模型" : "受控执行"} · {report.execution_status === "completed" ? "完成" : "失败"}</dd></div>
      <div><dt>模型</dt><dd>{report.model?.name || "未记录"}{report.model?.provider ? ` · ${report.model.provider}` : ""}</dd></div>
      <div><dt>耗时</dt><dd>{report.elapsed_seconds == null ? "未记录" : `${report.elapsed_seconds.toFixed(2)} 秒`}</dd></div>
      <div><dt>用量</dt><dd>{report.usage == null ? "未记录" : Object.entries(report.usage).map(([key, value]) => `${key}: ${value}`).join(" · ") || "未记录"}</dd></div>
    </dl>
    {report.error && <div className="eval-error"><LongText text={`${report.error.kind}：${report.error.message}`} /></div>}
    {report.execution_mode === "controlled" && <p className="eval-mode-note">受控执行用于验证接线与交互，不代表真实模型任务成绩。</p>}
    <section className="eval-auto" aria-label="自动判分"><div className="eval-section-heading"><h3>自动判分</h3><Status value={report.auto_status} /></div>
      <div className="eval-dimensions">{Object.entries(report.dimensions).map(([key, value]) => <div key={key}><span>{dimensions[key] ?? key}</span><Status value={value} /></div>)}</div>
      <ul className="eval-checks">{report.checks.map(check => <li key={check.id}>
        <div className="eval-check-heading"><h4>{check.label}</h4><Status value={check.status} /></div>
        <LongText text={check.detail || "检查未记录说明。"} />
        {check.evidence?.length > 0 && <div className="eval-check-evidence">{check.evidence.map(name => <button type="button" key={name} onClick={() => setOpen(name, true)} disabled={!report.evidence_files.some(file => file.name === name)}>{name}</button>)}</div>}
      </li>)}</ul>
      {report.auto_status === "passed" && !report.review && <p className="eval-review-limit">自动检查通过，仍需人工核对后才能计为通过。</p>}
    </section>
    <Review report={report} readFailed={readFailed} onSaved={onSaved} onReload={onReload} />
    <section className="eval-evidence-section" aria-label="原始证据"><div className="eval-section-heading"><h3>原始证据</h3><span>{report.evidence_files.length} 份文件</span></div>
      {report.evidence_files.length === 0 && <p className="eval-muted">未记录可读取的证据文件，不能据此认定通过。</p>}
      {report.evidence_files.map(file => <Evidence key={`${report.run_id}:${file.name}`} runId={report.run_id} digest={report.report_digest} file={file} open={opened.has(file.name)} onToggle={open => setOpen(file.name, open)} />)}
      <Reveal className="eval-evidence" label="报告原文"><div className="eval-evidence-content"><LongText text={JSON.stringify(report, null, 2)} code /></div></Reveal>
    </section>
  </div>;
}

const runLabel = (run: EvalRunSummary) => `${dateLabel(run.started_at || run.finished_at)} · ${statusLabels[run.status] ?? run.status} · ${run.run_id}`;

export function EvalPage() {
  const [params, setParams] = useSearchParams();
  const client = useQueryClient();
  const [listExpanded, setListExpanded] = useState(true);
  const [copyNotice, setCopyNotice] = useState("");
  const overview = useQuery({ queryKey: ["eval", "overview"], queryFn: ({ signal }) => evalApi.overview(signal), ...readOptions });
  const caseId = params.get("case") ?? overview.data?.cases[0]?.id ?? "";
  const selected = overview.data?.cases.find(item => item.id === caseId);
  const runs = useQuery({ queryKey: ["eval", "runs", caseId], queryFn: ({ signal }) => evalApi.runs(caseId, signal), enabled: !!selected, ...readOptions });
  const runId = params.get("run") ?? runs.data?.items[0]?.run_id ?? "";
  const report = useQuery({ queryKey: ["eval", "run", runId], queryFn: ({ signal }) => evalApi.report(runId, signal), enabled: !!selected && !!runId, ...readOptions });

  useEffect(() => {
    if (!selected || (params.get("case") && (!runId || params.get("run")))) return;
    setParams(current => { const next = new URLSearchParams(current); next.set("case", selected.id); if (runId) next.set("run", runId); return next; }, { replace: true });
  }, [selected, runId, params, setParams]);
  const selectCase = (item: EvalCase) => { setCopyNotice(""); setParams(current => { const next = new URLSearchParams(current); next.set("case", item.id); next.delete("run"); return next; }); };
  const selectRun = (id: string) => setParams(current => { const next = new URLSearchParams(current); next.set("run", id); return next; });
  const refresh = () => { void overview.refetch(); if (selected) void runs.refetch(); if (runId) void report.refetch(); };
  const saved = (value: EvalReport) => { client.setQueryData(["eval", "run", value.run_id], value); void client.invalidateQueries({ queryKey: ["eval", "runs", value.case_id] }); void client.invalidateQueries({ queryKey: ["eval", "overview"] }); };
  const reload = async () => { const result = await report.refetch(); return result.isError ? undefined : result.data; };

  async function copyCommand() {
    try { await navigator.clipboard.writeText("python -m creatoros.evaluation.run --case E01"); setCopyNotice("命令已复制。"); }
    catch { setCopyNotice("复制失败，可直接选择并复制命令文本。"); }
  }
  return <div className="eval-page">
    <header className="eval-page-header"><h1>Eval</h1><button className="eval-secondary eval-refresh" type="button" onClick={refresh} disabled={overview.isFetching || runs.isFetching || report.isFetching}><RefreshCw size={14} />刷新记录</button></header>
    {overview.isPending && <p className="eval-muted" role="status">正在读取题集…</p>}
    {overview.isError ? <ReadError error={overview.error} onRetry={() => void overview.refetch()} /> : overview.data && <>
      <div className="eval-dataset-line"><span>{overview.data.cases.length} 题 · {categories.length} 类</span><span>{overview.data.cases.filter(item => item.status === "not_run").length} 未运行 · {overview.data.cases.filter(item => item.status === "needs_review").length} 待复核</span></div>
      <SavedErrors errors={overview.data.errors} />
      <div className="eval-workbench"><aside className={`eval-case-panel ${listExpanded ? "" : "mobile-collapsed"}`} aria-label="评测题目">
        <div className="eval-case-heading"><h2>题目</h2><button className="eval-mobile-toggle" type="button" aria-expanded={listExpanded} onClick={() => setListExpanded(!listExpanded)}>{listExpanded ? "收起题目" : "展开题目"}</button></div>
        <nav className="eval-case-list" aria-label="题目分类">{categories.map(category => <section key={category.id}><h3>{category.label}</h3><ul>{overview.data.cases.filter(item => item.category === category.id).map(item => <li key={item.id}>
          <button className={`eval-case ${item.id === caseId ? "selected" : ""}`} type="button" aria-current={item.id === caseId ? "true" : undefined} onClick={() => selectCase(item)}>
            <span className="eval-case-title"><span>{item.id}</span>{item.title}</span><span className="eval-case-meta"><Status value={item.status} /><span>{item.split === "dev" ? "开发" : "阶段验收"}</span></span>
          </button></li>)}</ul></section>)}</nav>
      </aside><section className="eval-detail" aria-label="评测详情" aria-busy={runs.isFetching || report.isFetching}>
        {!selected ? <p className="eval-muted">题目不存在，请从列表中选择。</p> : <>
          <header className="eval-case-detail-heading"><h2>{selected.title}</h2><span className="eval-case-id">{selected.id} · {selected.split === "dev" ? "开发题" : "阶段验收题"}</span></header>
          <CaseDefinition key={selected.id} definition={selected.definition} />
          <div className="eval-executor">{selected.id === "E01" ? <><code>python -m creatoros.evaluation.run --case E01</code><button className="eval-copy" type="button" onClick={() => void copyCommand()} aria-label="复制执行命令"><Copy size={14} /></button></> : <p>本题尚未开放执行。</p>}</div>
          {copyNotice && <p className="eval-muted" role="status">{copyNotice}</p>}
          <section className="eval-history" aria-label="历史运行"><div className="eval-section-heading"><h3>历史运行</h3><span>{runs.data?.items.length ?? selected.run_count} 次</span></div>
            {runs.isPending && <p className="eval-muted" role="status">正在读取历史运行…</p>}
            {runs.isError ? <ReadError error={runs.error} onRetry={() => void runs.refetch()} /> : runs.data && <>
              {runs.data.items.length > 0 ? <label className="eval-run-select">选择运行<select value={runId} onChange={event => selectRun(event.target.value)}>{!runs.data.items.some(item => item.run_id === runId) && <option value={runId}>链接中的运行</option>}{runs.data.items.map(item => <option key={item.run_id} value={item.run_id}>{runLabel(item)}</option>)}</select></label> : <div className="eval-no-runs"><h3>尚未运行</h3><p>{selected.id === "E01" ? "在终端执行命令后，刷新这里查看报告。" : "此题还没有执行器与运行报告。"}</p></div>}
              <SavedErrors errors={runs.data.errors} /></>}
          </section>
          {runId && <>{report.isPending && <p className="eval-muted" role="status">正在读取运行报告…</p>}{report.isError && <ReadError error={report.error} onRetry={() => void report.refetch()} />}{report.data && (report.data.case_id !== caseId ? <p className="eval-review-error" role="alert">链接中的运行属于其他题目，请重新选择本题运行。</p> : <Report key={report.data.run_id} report={report.data} readFailed={report.isError} onSaved={saved} onReload={reload} />)}</>}
        </>}
      </section></div>
      <Reveal className="eval-dataset-details" label="题集说明"><p>题集：<code>{overview.data.dataset_id}</code></p><p>阶段验收题暂缓执行；12 题结果不代表通用 Agent 成功率。页面刷新和选择题目只读取报告。</p></Reveal>
    </>}
  </div>;
}
