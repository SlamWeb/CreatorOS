import { Link } from "react-router-dom";
import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useCreators } from "../api/hooks";
import { studioApi } from "../api/client";
import { CreatorCard } from "../components/CreatorCard";
import { EmptyState, ErrorState, LoadingState } from "../components/PageState";

export function CreatorsPage() {
  const [formOpen, setFormOpen] = useState(false);
  const [displayName, setDisplayName] = useState("");
  const [handle, setHandle] = useState("");
  const queryClient = useQueryClient();
  const createMutation = useMutation({
    mutationFn: () => studioApi.createCreator({ display_name: displayName, ...(handle.trim() ? { account_handle: handle } : {}) }),
    onSuccess: async (creator) => { await queryClient.invalidateQueries({ queryKey: ["creators"] }); await queryClient.invalidateQueries({ queryKey: ["overview"] }); window.location.assign(`/creators/${creator.id}`); },
  });
  const query = useCreators();
  if (query.isPending) return <LoadingState />;
  if (query.isError) return <ErrorState message={query.error.message} onRetry={() => void query.refetch()} />;
  const { items, page } = query.data;
  return <>
    <div className="page-heading"><div><p className="section-kicker">CREATORS</p><h1>账号目录</h1><p className="page-subtitle">每个账号都有自己的栏目、选题队列和内容运行。</p></div><button className="button button-primary" onClick={() => setFormOpen((value) => !value)}>{formOpen ? "收起" : "创建账号"} <span>{formOpen ? "−" : "+"}</span></button></div>
    {formOpen ? <form className="inline-form" onSubmit={(event) => { event.preventDefault(); createMutation.mutate(); }}><div className="form-heading"><div><span className="card-label">NEW ACCOUNT</span><h2>建立一个运营边界</h2></div><span className="form-note">平台固定为小红书</span></div><div className="form-grid"><label>账号名称<input value={displayName} onChange={(event) => setDisplayName(event.target.value)} placeholder="例如：面试知识实验室" required maxLength={120} /></label><label>账号标识 <small>可选</small><input value={handle} onChange={(event) => setHandle(event.target.value)} placeholder="例如：agent_lab" maxLength={160} /></label></div>{createMutation.isError ? <p className="form-error">{createMutation.error.message}</p> : null}<div className="form-actions"><button className="button button-primary" type="submit" disabled={createMutation.isPending}>{createMutation.isPending ? "保存中…" : "保存账号"}</button><button className="button button-quiet" type="button" onClick={() => setFormOpen(false)}>取消</button></div></form> : null}
    {items.length ? <><div className="directory-toolbar"><span>已配置 {page.total} 个账号</span><span className="toolbar-note">数据来自本地 Studio API</span></div><div className="creator-grid creator-grid-wide">{items.map((creator) => <CreatorCard key={creator.id} creator={creator} />)}</div></> : <EmptyState title="还没有账号"><span>账号目录为空。创建功能将在下一阶段接入，届时从这里开始设置你的第一个内容矩阵。</span><br /><Link className="text-link" to="/">回到今日 →</Link></EmptyState>}
  </>;
}
