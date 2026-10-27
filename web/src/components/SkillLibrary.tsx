import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { request, studioApi } from "../api/client";
import type { ProducerSkillItem, SkillLibraryView, TaskKind } from "../api/types";
import { SkillReferenceButton } from "./SkillReferenceButton";
import "./skill-library.css";

export const skillLabel = (skill: ProducerSkillItem) => `${skill.display_name || skill.name}${skill.version_number ? ` · v${skill.version_number}` : ""}${skill.version_status === "candidate" ? " · 候选" : ""}`;
export const supportsOutput = (skill: ProducerSkillItem, kind: TaskKind) => (skill.outputs ?? skill.capabilities?.outputs ?? (skill.carousel_compatible ? ["image"] : [])).includes(kind);
const creatorPath = (id: string) => `/api/creators/${encodeURIComponent(id)}/skills`;

export function CreatorSkillLibrary({ creatorId, onInspect }: { creatorId: string; onInspect: (skill: ProducerSkillItem) => void }) {
  const client = useQueryClient();
  const key = ["creator-skills", creatorId];
  const library = useQuery({ queryKey: key, queryFn: () => request<SkillLibraryView>(creatorPath(creatorId)), retry: false });
  const catalog = useQuery({ queryKey: ["producer-skills"], queryFn: studioApi.producerSkills, retry: false });
  const [selected, setSelected] = useState("");
  const [requestId, setRequestId] = useState(() => crypto.randomUUID());
  const write = useMutation({ retry: false,
    mutationFn: (input: { id: string; remove?: boolean; version: number }) => request<SkillLibraryView>(`${creatorPath(creatorId)}${input.remove ? `/${encodeURIComponent(input.id)}?expected_version=${input.version}` : ""}`, {
      method: input.remove ? "DELETE" : "POST", ...(!input.remove ? { body: JSON.stringify({ skill_id: input.id, request_id: requestId }) } : {}),
    }),
    onSuccess: result => { client.setQueryData(key, result); setSelected(""); setRequestId(crypto.randomUUID()); },
    onSettled: () => client.invalidateQueries({ queryKey: key }),
  });
  const choices = (catalog.data?.items ?? []).filter(skill => skill.version_status !== "candidate" && !library.data?.items.some(item => (item.family_id ?? item.id) === (skill.family_id ?? skill.id)));
  return <section className="skill-library" aria-label="账号 Skill 库"><header><h2>账号 Skill 库</h2><p>添加已安装的 Skill 供这个账号使用。栏目可从这里挑选常用项。</p></header>
    {library.isPending ? <p role="status">正在读取账号 Skill…</p> : library.isError ? <p className="form-error" role="alert">{library.error.message} <button type="button" className="text-link" onClick={() => void library.refetch()}>重新读取</button></p> : <>
      <form className="skill-library-add" onSubmit={event => { event.preventDefault(); if (selected) write.mutate({ id: selected, version: library.data.version }); }}>
        <label>已安装的 Skill<select aria-label="添加到账号的 Skill" value={selected} disabled={write.isPending || catalog.isPending || catalog.isError} onChange={event => { setSelected(event.target.value); setRequestId(crypto.randomUUID()); write.reset(); }}><option value="">选择 Skill</option>{choices.map(skill => <option key={skill.id} value={skill.id}>{skillLabel(skill)}</option>)}</select></label>
        <button className="button button-primary" disabled={!selected || write.isPending}>{write.isPending ? "正在保存…" : "加入账号库"}</button><Link className="text-link" to="/skills">管理已安装 Skill</Link>
      </form>
      {catalog.isError && <p className="form-error" role="alert">可添加列表读取失败。<button type="button" className="text-link" onClick={() => void catalog.refetch()}>重新读取</button></p>}
      {library.data.items.length ? <ul className="skill-library-list">{library.data.items.map(skill => <li key={skill.id}><div><SkillReferenceButton id={skill.id} known={skill} fallback={skillLabel(skill)} className="text-link skill-library-name" onInspect={onInspect} showVersion /><p>{skill.description}</p><small>{(skill.outputs ?? skill.capabilities?.outputs ?? []).map(kind => kind === "video" ? "视频" : "图片").join(" / ") || "辅助 Skill"}</small></div><button type="button" className="button button-quiet" disabled={write.isPending} onClick={() => write.mutate({ id: skill.id, remove: true, version: library.data.version })}>移出账号库</button></li>)}</ul> : <p className="muted">账号库还没有 Skill，先从已安装列表添加。</p>}
    </>}{write.isError && <p className="form-error" role="alert">{write.error.message} 已重新读取，请检查当前库后再操作。</p>}{write.isSuccess && <p role="status">账号 Skill 库已保存。</p>}
  </section>;
}

type PoolDraft = { version: number; ids: string[]; defaults: SkillLibraryView["defaults"] };
export function SeriesSkillPool({ seriesId, creatorId }: { seriesId: string; creatorId: string }) {
  const client = useQueryClient();
  const key = ["series-skills", seriesId];
  const path = `/api/series/${encodeURIComponent(seriesId)}/skills`;
  const pool = useQuery({ queryKey: key, queryFn: () => request<SkillLibraryView>(path), retry: false });
  const library = useQuery({ queryKey: ["creator-skills", creatorId], queryFn: () => request<SkillLibraryView>(creatorPath(creatorId)), retry: false });
  const [draft, setDraft] = useState<PoolDraft | null>(null);
  const save = useMutation({ retry: false, mutationFn: (input: PoolDraft) => request<SkillLibraryView>(path, { method: "PUT", body: JSON.stringify({ expected_version: input.version, skill_ids: input.ids, defaults: input.defaults }) }),
    onSuccess: result => { client.setQueryData(key, result); setDraft(null); }, onSettled: () => client.invalidateQueries({ queryKey: key }),
  });
  if (pool.isPending || library.isPending) return <p role="status">正在读取栏目常用 Skill…</p>;
  if (pool.isError || library.isError) return <p className="form-error" role="alert">{pool.error?.message ?? library.error?.message}<button className="text-link" type="button" onClick={() => { void pool.refetch(); void library.refetch(); }}>重新读取 Skill</button></p>;
  const current: PoolDraft = draft ?? { version: pool.data!.version, ids: pool.data!.items.map(skill => skill.id), defaults: pool.data!.defaults };
  const items = Array.from(new Map([...library.data!.items, ...pool.data!.items].map(skill => [skill.id, skill])).values());
  const stale = !!draft && draft.version !== pool.data!.version;
  const patch = (change: Partial<PoolDraft>) => { save.reset(); setDraft({ ...current, ...change }); };
  function choose(kind: TaskKind, ids: string[]) { patch({ defaults: { ...current.defaults, [kind]: (ids[0] ? ids : []).filter(Boolean).map((skill_id, index) => ({ skill_id, role: index ? "supporting" as const : "primary" as const })) } }); }
  function togglePool(id: string, included: boolean) {
    const ids = included ? [...current.ids, id] : current.ids.filter(value => value !== id);
    const defaults = Object.fromEntries((["image", "video"] as const).map(kind => {
      const kept = current.defaults[kind].filter(item => ids.includes(item.skill_id));
      return [kind, kept.some(item => item.role === "primary") ? kept : []];
    })) as SkillLibraryView["defaults"];
    patch({ ids, defaults });
  }
  return <section className="skill-library series-skill-pool" aria-label="栏目常用 Skill"><h3>栏目常用 Skill</h3><p>常用池只用于快速选择；每次制作仍可从账号库选其他 Skill。</p>
    <fieldset disabled={save.isPending || stale}><legend>常用池</legend><div className="skill-check-list">{items.map(skill => <label key={skill.id}><input type="checkbox" checked={current.ids.includes(skill.id)} onChange={event => togglePool(skill.id, event.target.checked)} /><span>{skillLabel(skill)}</span></label>)}</div>
      {(["image", "video"] as const).map(kind => { const defaults = current.defaults[kind] ?? []; const primary = defaults.find(item => item.role === "primary")?.skill_id ?? "";
        return <div className="stage-skill-default" key={kind}><h4>{kind === "image" ? "生图默认" : "视频默认"}</h4><label>主 Skill<select aria-label={`${kind === "image" ? "生图" : "视频"}默认主 Skill`} value={primary} onChange={event => choose(kind, [event.target.value, ...defaults.filter(item => item.role === "supporting" && item.skill_id !== event.target.value).map(item => item.skill_id)])}><option value="">不设置默认</option>{items.filter(skill => current.ids.includes(skill.id) && supportsOutput(skill, kind)).map(skill => <option key={skill.id} value={skill.id}>{skillLabel(skill)}</option>)}</select></label>
          <div className="skill-check-list">{items.filter(skill => current.ids.includes(skill.id) && skill.id !== primary).map(skill => <label key={skill.id}><input type="checkbox" disabled={!primary} checked={defaults.some(item => item.role === "supporting" && item.skill_id === skill.id)} onChange={event => choose(kind, [primary, ...defaults.filter(item => item.role === "supporting" && item.skill_id !== skill.id).map(item => item.skill_id), ...(event.target.checked ? [skill.id] : [])])} /><span>{skillLabel(skill)}（辅助）</span></label>)}</div>
        </div>; })}
    </fieldset>
    {stale && <p className="form-error" role="alert">栏目 Skill 已被修改，请重新读取后再保存。</p>}
    <div className="media-actions"><button className="button button-secondary" type="button" disabled={!draft || stale || save.isPending} onClick={() => save.mutate(current)}>{save.isPending ? "保存中…" : "保存常用池与默认"}</button>{draft && <button className="button button-quiet" type="button" disabled={save.isPending} onClick={() => { setDraft(null); save.reset(); }}>重新读取并放弃草稿</button>}</div>
    {save.isError && <p className="form-error" role="alert">{save.error.message} 请检查当前配置后重新保存。</p>}{save.isSuccess && <p role="status">常用池与阶段默认已保存。</p>}
  </section>;
}
