import { Link, useParams, useSearchParams } from "react-router-dom";
import { useState, type FormEvent } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useCreator, useSeries, useTopics } from "../api/hooks";
import { ApiError, studioApi } from "../api/client";
import type { OperationPlanInput, TopicView } from "../api/types";
import { ErrorState, LoadingState } from "../components/PageState";
import { StatusPill } from "../components/StatusPill";
import { ProducerSkillPanel } from "../components/ProducerSkillPanel";
import { TopicResearchPanel } from "../components/TopicResearchPanel";
import { CreativeMark } from "../components/CreativeMark";

export function SeriesPage() {
  const { seriesId } = useParams();
  return <SeriesContent key={seriesId} seriesId={seriesId} />;
}

function SeriesContent({ seriesId }: { seriesId?: string }) {
  const seriesQuery = useSeries(seriesId);
  const creatorQuery = useCreator(seriesQuery.data?.creator_id);
  const topicsQuery = useTopics(seriesId);
  const [topicDraft, setTopicDraft] = useState("");
  const [skillOpen, setSkillOpen] = useState(false);
  const [queueOpen, setQueueOpen] = useState(false);
  const [, setParams] = useSearchParams();
  const queryClient = useQueryClient();
  const previewMutation = useMutation({
    retry: false,
    mutationFn: (input: { plan: OperationPlanInput; count: number }) => studioApi.previewOperation({ request_text: `为「${seriesQuery.data?.name ?? "当前栏目"}」添加 ${input.count} 个选题`, plan: input.plan, series_id: seriesId }),
    onSuccess: (operation) => { setTopicDraft(""); setParams(p => { p.set("operation", operation.id); return p; }); },
  });
  const runMutation = useMutation({
    retry: false,
    mutationFn: async (topic: TopicView) => {
      const run = topic.existing_run_id ? { id: topic.existing_run_id, version: topic.existing_run_version! } : await studioApi.startRun({ topic_id: topic.id });
      return studioApi.executeRun(run.id, run.version);
    },
    onSettled: async () => {
      await Promise.all(["topics", "series", "overview", "runs"].map(key => queryClient.invalidateQueries({ queryKey: key === "topics" || key === "series" ? [key, seriesId] : [key] })));
    },
  });
  if (seriesQuery.isPending || topicsQuery.isPending) return <LoadingState label="正在读取栏目与选题…" />;
  if (seriesQuery.isError) return <ErrorState message={seriesQuery.error.message} onRetry={() => void seriesQuery.refetch()} />;
  if (topicsQuery.isError) return <ErrorState message={topicsQuery.error.message} onRetry={() => void topicsQuery.refetch()} />;
  const series = seriesQuery.data;
  const topics = topicsQuery.data.items;
  const pending = topics.filter(t => t.available_actions.includes("start") || t.available_actions.includes("resume"));
  const submitTopics = (event: FormEvent) => {
    event.preventDefault();
    const titles = topicDraft.split("\n").map(title => title.trim()).filter(Boolean);
    if (!titles.length || !seriesId) return;
    const plan: OperationPlanInput = { schema_version: 1, operations: [{ action: "add_topics", series_id: seriesId, topics: titles.map(title => ({ topic_id: `topic-${crypto.randomUUID().replaceAll("-", "").slice(0, 20)}`, title, source: "manual" })) }] };
    previewMutation.mutate({ plan, count: titles.length });
  };
  const startButton = (topic: TopicView) => <button type="button" className="button button-secondary queue-start" disabled={!series.is_active || runMutation.isPending} title="会调用 Codex 开始后台生产并消耗额度" onClick={() => runMutation.mutate(topic)}>{runMutation.isPending && runMutation.variables?.id === topic.id ? "提交中…" : topic.available_actions.includes("resume") ? "恢复生产" : "开始生产"}</button>;
  return <div className="series-index">
    <nav className="series-breadcrumb" aria-label="栏目路径"><Link to={`/creators/${series.creator_id}`}>{creatorQuery.data?.display_name ?? "返回账号"}</Link><span>/</span><b>{series.name}</b></nav>
    <div className="series-workspace">
      <div className="series-main">
        <section className="series-hero"><CreativeMark /><div className="series-identity"><h1>{series.name}</h1><p>{series.description || "还没有填写栏目定位"}</p>
          <div className="series-tags"><span>◉ {series.audience || "未设置受众"}</span><span title={series.skill_name}>▤ {series.skill_name === "knowledge-to-carousel" ? "知识点 → 图片轮播" : series.skill_name}</span>{!series.is_active && <StatusPill status="cancelled" />}</div>
        </div><button className="button button-secondary" aria-expanded={skillOpen} onClick={() => setSkillOpen(!skillOpen)}>生产 Skill</button></section>
        {skillOpen && <ProducerSkillPanel key={series.id} seriesId={series.id} current={series.skill_name} />}
        <a className="mobile-queue-jump" href="#pending-queue">待生产队列 · {series.available_topic_count} ↓</a>
        <TopicResearchPanel key={series.id} seriesId={series.id} />
        <details className="manual-topics"><summary>手动添加选题 / 用一句话调整</summary><form className="topic-form" onSubmit={submitTopics}>
          <label>添加选题 <small>每行一个标题，先 Preview 再确认写入</small><textarea value={topicDraft} onChange={event => setTopicDraft(event.target.value)} placeholder="每行输入一个选题" rows={3} /></label>
          {previewMutation.isError && <p className="form-error">{previewMutation.error.message}</p>}
          <button className="button button-primary" disabled={!series.is_active || previewMutation.isPending || !topicDraft.trim()}>生成 Preview</button>
          <button className="button button-secondary" type="button" onClick={() => setParams(p => { p.delete("operation"); p.set("command", "new"); p.set("series", series.id); return p; })}>用一句话调整</button>
        </form></details>
        <details className="full-topic-queue" id="full-topic-queue" open={queueOpen} onToggle={e => setQueueOpen(e.currentTarget.open)}><summary>全部选题 · {topicsQuery.data.page.total} 项</summary>
          {topics.length ? topics.map(topic => <article className="full-queue-item" key={topic.id}><h3>{topic.position}. {topic.title}</h3><StatusPill status={topic.existing_run_status ?? topic.status} /><details><summary>切入点与来源</summary><p>{topic.brief || "无补充说明"}</p></details>
            {topic.existing_run_id && <Link to={`/runs/${topic.existing_run_id}`}>查看运行 →</Link>}
            {(topic.available_actions.includes("start") || topic.available_actions.includes("resume")) && startButton(topic)}
          </article>) : <p>确认选题后，它们会出现在这里。</p>}
          {topicsQuery.data.page.total > topics.length && <p>当前显示前 {topics.length} 项，共 {topicsQuery.data.page.total} 项。</p>}
        </details>
      </div>
      <aside className="pending-queue" id="pending-queue" aria-label="待生产队列"><h2>待生产队列 <span>{pending.length}</span></h2><p className="queue-caption">已决定制作，还没有完成。</p>
        {pending.length ? pending.slice(0, 5).map((topic, i) => <article className="pending-item" key={topic.id}><span className={`queue-file file-${i % 3}`} aria-hidden="true">▤</span><div><h3>{topic.title}</h3><StatusPill status="queued" />{startButton(topic)}</div></article>) : <div className="queue-empty"><span aria-hidden="true">▱</span><p>下一篇作品，从选择开始</p><small>左侧候选确认入队后，会出现在这里。</small></div>}
        {runMutation.isError && <p className="form-error" role="alert">{runMutation.error.message}{runMutation.error instanceof ApiError && runMutation.error.runId && <Link to={`/runs/${runMutation.error.runId}`}>查看当前运行 →</Link>}</p>}
        {runMutation.isSuccess && <p className="queue-submitted" role="status">已提交后台，尚未完成。<Link to={`/runs/${runMutation.data.id}`}>查看本次运行 →</Link></p>}
        <a className="queue-more" href="#full-topic-queue" onClick={() => setQueueOpen(true)}>查看全部选题 →</a><small className="queue-disclaimer">开始生产会调用 Codex；审批和发布不会自动执行。</small>
      </aside>
    </div>
  </div>;
}
