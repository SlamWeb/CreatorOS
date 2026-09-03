import { Link } from "react-router-dom";
import type { CreatorView } from "../api/types";
import { platformLabel, StatusPill } from "./StatusPill";

export function CreatorCard({ creator }: { creator: CreatorView }) {
  const available = creator.series.reduce((sum, series) => sum + series.available_topic_count, 0);
  return <article className="creator-card">
    <div className="creator-card-head"><div className="avatar">{creator.display_name.slice(0, 1)}</div><div className="creator-title"><h3>{creator.display_name}</h3><p>{platformLabel(creator.platform)}{creator.account_handle ? ` · ${creator.account_handle}` : ""}</p></div><StatusPill status={creator.is_active ? "active" : "cancelled"} /></div>
    <div className="creator-card-meta"><span><b>{creator.series.length}</b> 个栏目</span><span><b>{available}</b> 个可选题</span>{creator.daily_content_limit ? <span>每日上限 <b>{creator.daily_content_limit}</b></span> : null}</div>
    <div className="creator-series-list">{creator.series.slice(0, 3).map((series) => <Link className="series-chip" key={series.id} to={`/series/${series.id}`}><span>{series.name}</span><small>{series.available_topic_count} 可用</small></Link>)}{creator.series.length > 3 ? <span className="series-more">+{creator.series.length - 3}</span> : null}</div>
    <Link className="text-link creator-open" to={`/creators/${creator.id}`}>打开账号 <span>→</span></Link>
  </article>;
}
