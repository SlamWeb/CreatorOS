import { Link } from "react-router-dom";
import { formatDate, StatusPill } from "./StatusPill";

export interface RunRowData {
  id: string;
  topic_title: string;
  creator_name: string;
  series_name: string;
  status: string;
  updated_at: string;
  href?: string | null;
}

export function RunRow({ run }: { run: RunRowData }) {
  const content = <>
    <div className="run-row-main"><span className="run-topic">{run.topic_title}</span><span className="run-context">{run.creator_name} <i>·</i> {run.series_name}</span></div>
    <StatusPill status={run.status} /><span className="run-updated">{formatDate(run.updated_at)}</span><span className="row-arrow">→</span>
  </>;
  return run.href === null ? <div className="run-row">{content}</div> : <Link to={run.href ?? `/runs/${run.id}`} className="run-row">{content}</Link>;
}

export function SectionTitle({ title, count, link }: { title: string; count?: number; link?: string }) {
  return <div className="section-title"><div><h2>{title}{count !== undefined ? <span className="count-badge">{count}</span> : null}</h2></div>{link ? <Link className="text-link" to={link}>查看全部 →</Link> : null}</div>;
}
