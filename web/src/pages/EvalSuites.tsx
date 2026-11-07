import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { ChevronRight, RefreshCw } from "lucide-react";
import { loadCatalog, loadSuiteRuns, type CatalogCase, type CatalogSuite } from "../features/eval/catalog";
import "./eval-workbench.css";

const readiness = (item: CatalogCase) => item.execution_readiness === "enabled" ? "适配器已开放" : "待核查";
const resource = (value: string) => ({ routine: "常规资源", heavy: "重型资源", unknown: "资源待核查" } as Record<string, string>)[value] ?? "资源未记录";
const detailList = (items: string[]) => Array.isArray(items) && items.length ? items.join("、") : "未记录";
const runStatus = (value: string) => ({ registered: "已登记", freezing: "冻结中", running: "运行中", completed: "已完成", failed: "失败", interrupted: "已中断", unknown: "未知" } as Record<string, string>)[value] ?? "未知";
const runScope = (value: string) => ({ partial: "范围不完整", complete: "完整范围" } as Record<string, string>)[value] ?? "范围未记录";

function SuiteRuns({ suite, onOpen }: { suite: CatalogSuite; onOpen: (roundId: string, runId: string) => void }) {
  const query = useQuery({ queryKey: ["eval-catalog-runs", suite.id], queryFn: ({ signal }) => loadSuiteRuns(suite.id, signal), retry: false, refetchOnWindowFocus: false });
  return <section aria-label="已登记运行" className="ec-list-section">
    <div className="ew-section-head"><h3>已登记运行</h3><span className="ew-muted">不合并不同运行的结果</span></div>
    {query.isPending && <p role="status" className="ew-muted">正在读取运行记录…</p>}
    {query.isError && <div role="alert" className="ew-message"><p>{query.error instanceof Error ? query.error.message : "运行记录读取失败。"}</p><button type="button" onClick={() => void query.refetch()}>重试</button></div>}
    {query.data?.length === 0 && <p className="ew-muted">暂无已登记运行。</p>}
    {!!query.data?.length && <div className="ec-case-list">{query.data.map(run => <button type="button" key={run.run_id} className="ec-run" onClick={() => onOpen(run.round_id, run.run_id)}>
      <span><strong>{run.round_title || run.round_id}</strong><small>版本 {run.suite_version} · {runScope(run.selection_kind)} · {run.selected_count} / {run.planned_count} 题 · {run.created_at}</small></span>
      <span className="ew-status">{runStatus(run.display_status || run.status)}</span><ChevronRight size={16} aria-hidden="true" />
    </button>)}</div>}
  </section>;
}

function CaseList({ title, items }: { title: string; items: CatalogCase[] }) {
  if (!items.length) return null;
  return <section aria-label={title} className="ec-list-section">
    <div className="ew-section-head"><h3>{title}</h3><span className="ew-muted">{items.length} 题</span></div>
    <div className="ec-case-list">{items.map(item => <details className="ec-case" key={item.instance_id}>
      <summary><span className="ec-case-main"><strong>{item.title || item.case_id}</strong><small>{item.instance_id}</small></span><span className={`ec-readiness ${item.execution_readiness === "enabled" ? "is-enabled" : "is-review"}`}>{readiness(item)}</span><ChevronRight size={16} aria-hidden="true" /></summary>
      <div className="ec-case-detail"><p>{item.reason || "未记录执行条件。"}</p><dl>
        <div><dt>资源</dt><dd>{resource(item.resource_class)}</dd></div>
        <div><dt>依赖</dt><dd>{detailList(item.dependencies)}</dd></div>
        <div><dt>外部副作用</dt><dd>{detailList(item.side_effects)}</dd></div>
        <div><dt>来源</dt><dd>{item.dataset_id} / {item.case_id}{item.parent_instance_id ? ` · 原题 ${item.parent_instance_id}` : ""}</dd></div>
      </dl></div>
    </details>)}</div>
  </section>;
}

function SuiteDetail({ suite, back, openRun }: { suite: CatalogSuite; back: () => void; openRun: (roundId: string, runId: string) => void }) {
  const [query, setQuery] = useState("");
  const enabled = suite.cases.filter(item => item.execution_readiness === "enabled").length;
  const needsReview = suite.cases.length - enabled;
  const matches = (item: CatalogCase) => `${item.instance_id} ${item.case_id} ${item.title}`.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase());
  const visibleCases = suite.cases.filter(matches);
  const visibleCandidates = (suite.candidates ?? []).filter(matches);
  return <div className="ec-detail">
    <div className="ec-back-row"><button className="ew-link" type="button" onClick={back}>← 返回回归集</button></div>
    <div className="ew-title"><div><p className="ew-eyebrow">回归集 / {suite.id}</p><h2>{suite.title}</h2><p>{suite.description}</p><p className="ew-muted">版本 {suite.version}</p></div></div>
    {suite.scope_status === "partial" && <p className="ec-scope-warning">执行范围尚不完整，不能视为全量运行能力。</p>}
    <section className="ec-counts" aria-label="套件范围"><div><span>已定义</span><strong>{suite.cases.length}</strong><small>正式成员</small></div><div><span>执行准入</span><strong>{enabled}</strong><small>适配器已开放</small></div><div><span>待核查</span><strong>{needsReview}</strong><small>尚未开放执行</small></div></section>
    <p className="ew-muted ec-footnote">这是题集定义与准入状态；运行结果请在评测工作台查看。题集成员可能与其他集合重叠。</p>
    <SuiteRuns suite={suite} onOpen={openRun} />
    <label className="ec-search">查找题目<input type="search" value={query} onChange={event => setQuery(event.target.value)} placeholder="按题号或标题查找" /></label>
    <CaseList title="正式成员" items={visibleCases} />
    {query.trim() && visibleCases.length === 0 && <p className="ew-muted">没有匹配的正式成员。</p>}
    {(suite.candidates?.length ?? 0) > 0 && <details className="ec-candidates"><summary>候选题目（不计入正式成员） · {suite.candidates!.length}</summary><CaseList title="候选题目（不计入正式成员）" items={visibleCandidates} />{visibleCandidates.length === 0 && <p className="ew-muted">没有匹配的候选题目。</p>}</details>}
  </div>;
}

export function EvalSuites() {
  const [params, setParams] = useSearchParams();
  const query = useQuery({ queryKey: ["eval-catalog"], queryFn: ({ signal }) => loadCatalog(signal), retry: false, refetchOnWindowFocus: false });
  const suiteId = params.get("suite") ?? "";
  const selected = query.data?.suites.find(item => item.id === suiteId);
  const update = (view: string | null, suite: string | null) => setParams(current => {
    const next = new URLSearchParams(current);
    if (view === null) next.delete("view"); else next.set("view", view);
    if (suite === null) next.delete("suite"); else next.set("suite", suite);
    return next;
  });
  const openRun = (roundId: string, runId: string) => setParams(current => {
    const next = new URLSearchParams(current);
    ["view", "suite", "case", "base", "base_run", "compare_run"].forEach(key => next.delete(key));
    next.set("round", roundId); next.set("run", runId);
    return next;
  });
  return <main className="eval-hub"><header className="ew-top"><div><p className="ew-eyebrow">CREATOROS / EVAL</p><h1>回归集</h1></div><div className="ew-top-actions"><button type="button" className="ew-link" onClick={() => update(null, null)}>返回评测工作台</button><button type="button" className="ew-refresh" onClick={() => void query.refetch()} disabled={query.isFetching}><RefreshCw size={15} />刷新</button></div></header>
    {query.isPending && <p role="status" className="ew-message">正在读取回归集…</p>}
    {query.isError && <div role="alert" className="ew-message"><p>{query.error instanceof Error ? query.error.message : "读取回归集失败。"}</p><button type="button" onClick={() => void query.refetch()}>重试</button></div>}
    {query.data && suiteId && !selected && <div role="alert" className="ew-message"><p>未找到该版本的回归集。</p><button type="button" className="ew-link" onClick={() => update("suites", null)}>返回回归集</button></div>}
    {query.data && selected && <SuiteDetail key={selected.id} suite={selected} back={() => update("suites", null)} openRun={openRun} />}
    {query.data && !suiteId && <><p className="ec-intro">查看每套题的范围和执行准入；每次运行的实际结果在评测工作台中查看。</p>
      {query.data.suites.length ? <div className="ec-suite-list">{query.data.suites.map(suite => {
        const enabled = suite.cases.filter(item => item.execution_readiness === "enabled").length;
        return <button type="button" className="ec-suite" key={suite.id} onClick={() => update("suites", suite.id)}><span className="ec-suite-title"><strong>{suite.title}</strong><small>{suite.description}</small><small>版本 {suite.version}{suite.scope_status === "partial" ? " · 执行范围尚不完整" : ""}</small></span><span className="ec-suite-count"><strong>{suite.cases.length}</strong><small>正式成员</small></span><span className="ec-suite-count"><strong>{enabled}</strong><small>适配器已开放</small></span><ChevronRight size={17} aria-hidden="true" /></button>;
      })}</div> : <div className="ew-empty"><h2>暂无回归集</h2><p>目录中还没有可展示的题集。</p></div>}
    </>}
  </main>;
}
