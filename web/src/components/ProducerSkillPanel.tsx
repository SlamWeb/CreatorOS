import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { request } from "../api/client";
import "./producer-skills.css";

type Skill = { id: string; name: string; description: string; carousel_compatible: boolean;
  compatibility_note: string; commit: string | null; github_url: string | null };
type Job = { id: string; github_url: string; status: string; message: string; skill: Skill | null };

export function ProducerSkillPanel({ seriesId, current }: { seriesId: string; current: string }) {
  const [url, setUrl] = useState("");
  const [choice, setChoice] = useState<{ id: string; expected: string } | null>(null);
  const [params, setParams] = useSearchParams();
  const client = useQueryClient();
  const jobId = params.get("skillJob");
  const skills = useQuery({ queryKey: ["producer-skills"], queryFn: () => request<{ items: Skill[] }>("/api/producer-skills"), refetchInterval: jobId ? 3000 : false });
  const job = useQuery({ queryKey: ["skill-install", jobId], enabled: !!jobId,
    queryFn: () => request<Job>(`/api/producer-skills/jobs/${encodeURIComponent(jobId!)}`),
    refetchInterval: q => q.state.data?.status === "installing" ? 2000 : false });
  const install = useMutation({ retry: false,
    mutationFn: (retry: boolean) => request<Job>("/api/producer-skills/install", { method: "POST", body: JSON.stringify({ github_url: retry ? job.data!.github_url : url, retry }) }),
    onSuccess: data => { setParams(p => { p.set("skillJob", data.id); return p; }); void client.invalidateQueries({ queryKey: ["producer-skills"] }); } });
  const bind = useMutation({ retry: false,
    mutationFn: () => request(`/api/series/${encodeURIComponent(seriesId)}/skill`, {
      method: "POST", body: JSON.stringify({ skill_id: choice!.id, expected_skill_name: choice!.expected }) }),
    onSuccess: async () => { setChoice(null); await client.invalidateQueries({ queryKey: ["series", seriesId] }); await client.invalidateQueries({ queryKey: ["creators"] }); } });
  const selected = skills.data?.items.find(s => s.id === choice?.id);
  return <details open className="content-section producer-skill-panel">
    <summary>管理生产 Skill · 从 GitHub 安装 / 更换绑定</summary>
    <p className="page-subtitle">CreatorOS 下载并核验 Git 版本，绑定由你确认。不会生成内容，也不会改变已有任务。</p>
    <form className="topic-form" onSubmit={e => { e.preventDefault(); install.mutate(false); }}>
      <label>GitHub Skill 链接<input aria-label="GitHub Skill 链接" type="url" required value={url} onChange={e => setUrl(e.target.value)} placeholder="https://github.com/owner/repo/tree/main/skill" /></label>
      <button className="button button-secondary" disabled={!url || install.isPending || job.data?.status === "installing"}>安装 GitHub Skill</button>
    </form>
    {install.error && <p className="form-error">{install.error.message} 可重新提交同一链接查询已有任务，不会重复安装。</p>}
    {job.data && <p role="status">{job.data.status} · {job.data.message}</p>}
    {job.data && ["failed", "interrupted"].includes(job.data.status) && <button type="button" className="button button-secondary" disabled={install.isPending} onClick={() => install.mutate(true)}>重新安装</button>}
    {job.error && <p className="form-error">{job.error.message}</p>}
    {skills.error && <p className="form-error">{skills.error.message}</p>}
    <label>已安装技能<select aria-label="已安装技能" value={choice?.id ?? ""} onChange={e => { setChoice(e.target.value ? { id: e.target.value, expected: current } : null); bind.reset(); }}>
      <option value="">选择要绑定的技能版本</option>
      {skills.data?.items.map(s => <option key={s.id} value={s.id} disabled={!s.carousel_compatible}>{s.name} · {s.commit?.slice(0, 8) ?? "内置"}{s.carousel_compatible ? "" : " · 非图片轮播"}</option>)}
    </select></label>
    {selected && <div className="notice-strip" style={{ overflowWrap: "anywhere" }}>
      <p>{selected.description}</p><p>{selected.compatibility_note}</p>
      <p>当前：{choice?.expected} → 新绑定：{selected.id}</p>
      <p>只影响新建任务；安装预检不代表内容质量已验收。</p>
      <button className="button button-primary" type="button" disabled={bind.isPending || choice?.expected !== current || choice?.id === current} onClick={() => bind.mutate()}>确认绑定此 Skill</button>
      {choice?.expected !== current && <p>栏目绑定已变化，请重新选择并检查。</p>}
    </div>}
    {bind.error && <p className="form-error">{bind.error.message}</p>}
    {bind.isSuccess && <p role="status">已更新栏目绑定，已有任务保持不变。</p>}
  </details>;
}
