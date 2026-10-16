import { useEffect, useId, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { analyticsApi, type AnalyticsNote, type MetricKey, type MetricSample } from "../api/analytics";
import { ErrorState, LoadingState } from "./PageState";
import "./account-analytics.css";

const metrics: { key: MetricKey; label: string }[] = [
  { key: "likes", label: "点赞" }, { key: "favorites", label: "收藏" },
  { key: "comments", label: "评论" }, { key: "views", label: "阅读" }, { key: "shares", label: "分享" },
];
const number = (value: number | null | undefined) => value == null ? "暂无" : value.toLocaleString("zh-CN");
const time = (value: string | null | undefined) => value ? new Date(value).toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "尚未采集";

export function AccountAnalytics({ creatorId }: { creatorId: string }) {
  const client = useQueryClient();
  const [visible, setVisible] = useState(document.visibilityState === "visible");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [metric, setMetric] = useState<MetricKey>("likes");
  const queryKey = ["account-analytics", creatorId];
  useEffect(() => {
    const update = () => setVisible(document.visibilityState === "visible");
    document.addEventListener("visibilitychange", update);
    return () => document.removeEventListener("visibilitychange", update);
  }, []);
  const query = useQuery({ queryKey, queryFn: ({ signal }) => analyticsApi.get(creatorId, signal), retry: false,
    enabled: visible, refetchInterval: q => visible ? q.state.data?.checking ? 3000 : 30000 : false,
    refetchIntervalInBackground: false });
  const settings = useMutation({ mutationFn: (enabled: boolean) => analyticsApi.settings(creatorId, enabled), retry: false,
    onSuccess: data => client.setQueryData(queryKey, data) });
  const refresh = useMutation({ mutationFn: () => analyticsApi.refresh(creatorId), retry: false,
    onSuccess: data => client.setQueryData(queryKey, data) });
  if (!query.data) {
    return query.isError ? <ErrorState message={query.error.message} onRetry={() => void query.refetch()} /> : <LoadingState label="读取账号数据…" />;
  }
  const data = query.data;
  const selected = data.notes.find(note => note.request_id === selectedId) ?? data.notes[0];
  const error = settings.error?.message ?? refresh.error?.message ?? query.error?.message ?? data.error;
  return <section className="account-analytics" aria-label="账号数据">
    <div className="analytics-toolbar">
      <div className="analytics-automation">
        <label className="analytics-switch"><input type="checkbox" role="switch" checked={data.enabled} disabled={settings.isPending}
          onChange={event => settings.mutate(event.target.checked)} /><span className="analytics-switch-track" aria-hidden="true" /><span>自动更新</span></label>
        <p>每 5 分钟检查 · 每篇间隔 30 分钟 · Studio 运行时生效</p>
      </div>
      <button type="button" className="button button-secondary" disabled={refresh.isPending || data.checking || !data.notes.length}
        onClick={() => refresh.mutate()}>{refresh.isPending || data.checking ? "更新中…" : "刷新数据"}</button>
    </div>
    <div className="analytics-updated" role="status">
      <span>最后更新 <time dateTime={data.last_updated_at ?? undefined}>{time(data.last_updated_at)}</time></span>
      {data.enabled && data.next_check_at && <span>下次检查 {time(data.next_check_at)}</span>}
    </div>
    {error && <p className="form-error" role="alert">{error} <button type="button" className="text-link" onClick={() => void query.refetch()}>重新读取</button></p>}
    <header className="analytics-section-head"><h2>最近 {data.window_days} 天发布</h2><span>{data.summary.notes} 篇 · 已采集 {data.summary.sampled_notes} 篇</span></header>
    <dl className="analytics-totals" aria-label="近期笔记累计数据">{metrics.map(item => <div key={item.key}><dt>{item.label}</dt><dd>{number(data.summary[item.key])}</dd></div>)}</dl>
    <p className="analytics-caption">各笔记最新累计值之和，不是近 7 天新增量。平台未提供的指标显示「暂无」。</p>
    {!data.notes.length ? <div className="analytics-empty"><h3>还没有近期发布的笔记</h3><p>笔记发布并核对成功后，数据会出现在这里。</p></div> : <>
      <section className="analytics-trend" aria-label="笔记数据趋势">
        <div className="analytics-trend-heading"><h2>笔记趋势</h2><label>笔记<select aria-label="选择趋势笔记" value={selected.request_id} onChange={event => setSelectedId(event.target.value)}>
          {data.notes.map(note => <option key={note.request_id} value={note.request_id}>{note.title}</option>)}
        </select></label></div>
        <div className="analytics-metric-picker" role="group" aria-label="趋势指标">{metrics.map(item => <button key={item.key} type="button" aria-pressed={metric === item.key}
          onClick={() => setMetric(item.key)}>{item.label}</button>)}</div>
        <MetricChart samples={selected.samples} metric={metric} />
        <MetricReport note={selected} metric={metric} />
      </section>
      <section className="analytics-notes" aria-label="已发布笔记">
        <h2>已发布笔记</h2>
        <div className="analytics-table-scroll"><table><thead><tr><th scope="col">笔记</th><th scope="col">点赞</th><th scope="col">收藏</th><th scope="col">评论</th><th scope="col">采集时间</th></tr></thead>
          <tbody>{data.notes.map(note => { const latest = note.samples.at(-1); return <tr key={note.request_id}>
            <th scope="row"><Link to={`/runs/${encodeURIComponent(note.run_id)}?publish=1`}>{note.title}</Link><span>{note.series_name} · 发布于 {time(note.published_at)}</span>
              {note.error && <p className="analytics-note-error">{note.error}</p>}</th>
            <td>{number(latest?.likes)}</td><td>{number(latest?.favorites)}</td><td>{number(latest?.comments)}</td><td><time dateTime={note.last_updated_at ?? undefined}>{time(note.last_updated_at)}</time></td>
          </tr>; })}</tbody></table></div>
      </section>
    </>}
  </section>;
}

function MetricReport({ note, metric }: { note: AnalyticsNote; metric: MetricKey }) {
  const label = metrics.find(item => item.key === metric)!.label;
  const samples = note.samples.filter(sample => sample[metric] != null);
  const first = samples[0];
  const last = samples.at(-1);
  let report = `平台尚未提供这篇笔记的${label}数据。`;
  if (first && last) {
    if (samples.length === 1) report = `${time(last.measured_at)}，累计${label} ${number(last[metric])}。目前只有一次采样，尚不能比较变化。`;
    else {
      const delta = last[metric]! - first[metric]!;
      report = `${time(first.measured_at)} 至 ${time(last.measured_at)}，累计${label}从 ${number(first[metric])} 到 ${number(last[metric])}，${delta > 0 ? `增加 ${number(delta)}` : delta < 0 ? `减少 ${number(-delta)}` : "没有变化"}。`;
    }
  }
  return <div className="analytics-report"><h3>采样报告</h3><p>{report}</p><a href={note.post_url} target="_blank" rel="noreferrer">打开笔记</a></div>;
}

function MetricChart({ samples, metric }: { samples: MetricSample[]; metric: MetricKey }) {
  const titleId = useId();
  const svgRef = useRef<SVGSVGElement>(null);
  const [width, setWidth] = useState(720);
  const label = metrics.find(item => item.key === metric)!.label;
  const ordered = [...samples].filter(sample => Number.isFinite(Date.parse(sample.measured_at))).sort((a, b) => Date.parse(a.measured_at) - Date.parse(b.measured_at));
  const valid = ordered.filter(sample => sample[metric] != null);
  const hasData = valid.length > 0;
  useEffect(() => {
    const svg = svgRef.current;
    if (!svg) return;
    const observer = new ResizeObserver(entries => setWidth(Math.max(280, entries[0].contentRect.width)));
    observer.observe(svg);
    return () => observer.disconnect();
  }, [hasData]);
  if (!valid.length) return <div className="analytics-chart-empty">暂无{label}数据</div>;
  const height = 240, left = 48, right = 24, top = 20, bottom = 38;
  const minTime = Date.parse(ordered[0].measured_at), maxTime = Date.parse(ordered.at(-1)!.measured_at);
  const maxValue = Math.max(1, ...valid.map(sample => sample[metric]!));
  const x = (sample: MetricSample) => minTime === maxTime ? width / 2 : left + (Date.parse(sample.measured_at) - minTime) / (maxTime - minTime) * (width - left - right);
  const y = (value: number) => height - bottom - value / maxValue * (height - top - bottom);
  // A missing sample breaks the path. It is not zero and is not interpolated.
  const segments: string[] = [];
  let segment = "";
  for (const sample of ordered) {
    if (sample[metric] == null) { if (segment) segments.push(segment); segment = ""; }
    else segment += `${segment ? " L" : "M"} ${x(sample)} ${y(sample[metric]!)}`;
  }
  if (segment) segments.push(segment);
  const ticks = maxValue <= 1 ? [0, 1] : [0, Math.ceil(maxValue / 2), maxValue];
  return <svg ref={svgRef} className="analytics-chart" viewBox={`0 0 ${width} ${height}`} role="img" aria-labelledby={titleId}>
    <title id={titleId}>{label}累计趋势，{valid.length} 个有效采样点，{time(ordered[0].measured_at)} 至 {time(ordered.at(-1)!.measured_at)}</title>
    {ticks.map(value => <g key={value}><line className="analytics-chart-grid" x1={left} x2={width - right} y1={y(value)} y2={y(value)} /><text x={left - 10} y={y(value) + 4} textAnchor="end">{value.toLocaleString("zh-CN", { notation:"compact", maximumFractionDigits:1 })}</text></g>)}
    {segments.map((path, index) => <path key={index} d={path} className="analytics-chart-line" />)}
    {valid.map((sample, index) => <circle key={`${sample.measured_at}-${index}`} cx={x(sample)} cy={y(sample[metric]!)} r={4} className="analytics-chart-point"><title>{time(sample.measured_at)} · {label} {number(sample[metric])}</title></circle>)}
    {minTime === maxTime ? <text x={width / 2} y={height - 8} textAnchor="middle">{time(ordered[0].measured_at)}</text> : <>
      <text x={left} y={height - 8}>{time(ordered[0].measured_at)}</text><text x={width - right} y={height - 8} textAnchor="end">{time(ordered.at(-1)!.measured_at)}</text>
    </>}
  </svg>;
}
