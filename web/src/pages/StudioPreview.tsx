import { useEffect, useRef, useState } from "react";
import type { DragEvent, FormEvent, PointerEvent } from "react";
import { Link } from "react-router-dom";
import "./studio-preview.css";

type Kind = "mind" | "production";
type Skill = { id: string; name: string; kind: Kind; detail: string; url: string };
type Series = { id: string; name: string; description: string; mind: string; production: string; account: string; topics: string[] };
type Workspace = { skills: Skill[]; series: Series[] };
const KEY = "creatoros.studio-preview.v1";
const accounts = [{ id: "demo-knowledge", name: "知识实验室", handle: "@knowledge_lab" }, { id: "demo-notes", name: "开发者笔记", handle: "@dev_notes" }];
const initial: Workspace = { skills: [
  { id: "deep", name: "知识讲解 · Deep", kind: "mind", detail: "因果学习路径 · 教学分镜", url: "https://github.com/SlamWeb/knowledge-to-storyboard" },
  { id: "brief", name: "知识轮播", kind: "mind", detail: "逐页文案 · 6–12 页", url: "https://github.com/SlamWeb/knowledge-to-storyboard" },
  { id: "xiaobai", name: "小白", kind: "production", detail: "角色图解 · 竖版图文", url: "https://github.com/SlamWeb/creatorOS-ip-skills" },
  { id: "notes-demo", name: "纸上笔记", kind: "production", detail: "排版演示 · 无生产能力", url: "" },
], series: [] };
function restore(): Workspace {
  try {
    const w = JSON.parse(localStorage.getItem(KEY) ?? "null") as Workspace | null;
    if (w && Array.isArray(w.skills) && Array.isArray(w.series)
      && w.skills.every(s => typeof s.id === "string" && typeof s.name === "string" && typeof s.detail === "string" && typeof s.url === "string" && ["mind", "production"].includes(s.kind))
      && w.series.every(s => typeof s.id === "string" && typeof s.name === "string" && typeof s.description === "string" && typeof s.account === "string" && Array.isArray(s.topics) && s.topics.every(t => typeof t === "string") && w.skills.some(k => k.id === s.mind && k.kind === "mind") && w.skills.some(k => k.id === s.production && k.kind === "production"))) return w;
  } catch { /* Start safely if prototype storage is unavailable. */ }
  return initial;
}

export function StudioPreview() {
  const [w, setW] = useState<Workspace>(restore);
  const [mind, setMind] = useState("");
  const [production, setProduction] = useState("");
  const [search, setSearch] = useState("");
  const [active, setActive] = useState<string | null>(null);
  const [modal, setModal] = useState<"skill" | "series" | null>(null);
  const [message, setMessage] = useState("");
  const [storageError, setStorageError] = useState(false);
  const [drag, setDrag] = useState<{ type: "skill" | "series"; id: string } | null>(null);
  const dialog = useRef<HTMLDialogElement>(null);
  const opener = useRef<HTMLElement | null>(null);
  const pointer = useRef<{ x: number; y: number; source: HTMLElement; moved: boolean } | null>(null);
  const suppressClick = useRef(false);
  function pointerStart(e: PointerEvent<HTMLDivElement>) {
    if (e.button !== 0 || e.pointerType !== "mouse") return;
    const target = e.target as HTMLElement;
    if (target.closest("select,input,textarea")) return;
    const source = target.closest<HTMLElement>("[draggable]");
    if (!source) return;
    pointer.current = { x: e.clientX, y: e.clientY, source, moved: false };
    suppressClick.current = false;
  }
  function pointerMove(e: PointerEvent<HTMLDivElement>) {
    const p = pointer.current;
    if (!p || Math.hypot(e.clientX - p.x, e.clientY - p.y) < 6) return;
    p.moved = true;
    const isSkill = p.source.classList.contains("sp-skill");
    const index = Array.from(document.querySelectorAll(".sp-skill")).indexOf(p.source);
    const entry = isSkill ? w.skills.filter(s => `${s.name} ${s.detail}`.toLowerCase().includes(search.toLowerCase())).sort((a,b) => a.kind === b.kind ? 0 : a.kind === "mind" ? -1 : 1)[index] : w.series.find(s => s.id === p.source.dataset.seriesId);
    if (entry) setDrag({ type: isSkill ? "skill" : "series", id: entry.id });
    e.currentTarget.setPointerCapture(e.pointerId);
  }
  function pointerEnd(e: PointerEvent<HTMLDivElement>) {
    const p = pointer.current; pointer.current = null;
    if (!p?.moved) return;
    suppressClick.current = true;
    const target = document.elementFromPoint(e.clientX, e.clientY)?.closest<HTMLElement>("[data-testid]")?.dataset.testid;
    if (target === "slot-mind" || target === "slot-production") {
      const kind = target === "slot-mind" ? "mind" : "production";
      const s = drag?.type === "skill" ? skill(drag.id) : undefined;
      if (s?.kind === kind) choose(s); else setMessage(kind === "mind" ? "这里需要内容 Skill" : "这里需要制作 Skill");
    } else if (target === "unassigned" || accounts.some(a => a.id === target)) {
      if (drag?.type === "series") assign(drag.id, target === "unassigned" ? "" : target!);
      else setMessage("请拖入栏目，不是 Skill");
    }
    setDrag(null);
  }
  useEffect(() => { try { localStorage.setItem(KEY, JSON.stringify(w)); setStorageError(false); } catch { setStorageError(true); } }, [w]);
  useEffect(() => { if (modal) { dialog.current?.showModal(); dialog.current?.querySelector<HTMLInputElement>('input[name="name"]')?.focus(); } }, [modal]);
  const skill = (id: string) => w.skills.find(s => s.id === id);
  const current = w.series.find(s => s.id === active);
  function open(value: "skill" | "series") { opener.current = document.activeElement as HTMLElement; setMessage(""); setModal(value); }
  function close() { dialog.current?.close(); setModal(null); setMessage(v => v.startsWith("请填写") ? "" : v); opener.current?.focus(); }
  function choose(s: Skill) { (s.kind === "mind" ? setMind : setProduction)(s.id); setMessage(`已选择${s.name}`); }
  function startDrag(e: DragEvent, type: "skill" | "series", id: string) { setDrag({ type, id }); e.dataTransfer.setData("text/plain", `${type}:${id}`); e.dataTransfer.effectAllowed = "copyMove"; }
  function dropSkill(e: DragEvent, kind: Kind) {
    e.preventDefault(); const s = drag?.type === "skill" ? skill(drag.id) : undefined;
    if (s?.kind === kind) choose(s); else setMessage(kind === "mind" ? "这里需要内容 Skill" : "这里需要制作 Skill");
    setDrag(null);
  }
  function assign(id: string, account: string) { setW(v => ({ ...v, series: v.series.map(s => s.id === id ? { ...s, account } : s) })); setMessage(account ? `已分配给${accounts.find(a => a.id === account)?.name}` : "已移回未分配"); }
  function dropSeries(e: DragEvent, account: string) { e.preventDefault(); if (drag?.type === "series") assign(drag.id, account); else setMessage("请拖入栏目，不是 Skill"); setDrag(null); }
  function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault(); const data = new FormData(e.currentTarget); const name = String(data.get("name") ?? "").trim();
    if (!name) { setMessage("请填写名称"); return; }
    if (modal === "skill") {
      const url = String(data.get("url") ?? "").trim();
      try { const u = new URL(url); if (u.protocol !== "https:" || u.hostname !== "github.com") throw new Error(); }
      catch { setMessage("请填写 https://github.com/ 开头的链接"); return; }
      setW(v => ({ ...v, skills: [...v.skills, { id: crypto.randomUUID(), name, kind: data.get("kind") as Kind, detail: "自定义条目 · 未安装", url }] })); setMessage(`已添加示例条目：${name}，未安装`);
    } else {
      if (!mind || !production) return;
      setW(v => ({ ...v, series: [...v.series, { id: crypto.randomUUID(), name, description: String(data.get("description") ?? "").trim(), mind, production, account: "", topics: [] }] }));
      setMind(""); setProduction(""); setMessage(`已创建栏目：${name}`);
    }
    close();
  }
  const options = <><option value="">未分配</option>{accounts.map(a => <option key={a.id} value={a.id}>{a.name}</option>)}</>;
  const card = (s: Series) => <article className="sp-series" key={s.id} data-series-id={s.id} draggable onDragStart={e => startDrag(e, "series", s.id)}>
    <button className="sp-series-open" onClick={() => setActive(s.id)}><strong>{s.name}</strong><span>{skill(s.mind)?.name} ＋ {skill(s.production)?.name}</span><small>{s.topics.length} 个选题 <span>↗</span></small></button>
    <label className="sp-assign"><span className="sp-sr">分配 {s.name}</span><select value={s.account} onChange={e => assign(s.id, e.target.value)}>{options}</select></label>
  </article>;
  return <div className="sp-app" onPointerDown={pointerStart} onPointerMove={pointerMove} onPointerUp={pointerEnd} onPointerCancel={() => { pointer.current = null; setDrag(null); }} onDragStartCapture={e => e.preventDefault()} onClickCapture={e => { if (suppressClick.current) { e.preventDefault(); e.stopPropagation(); suppressClick.current = false; } }} onDragEnd={() => setDrag(null)}>
    <header className="sp-top"><Link to="/" className="sp-brand"><span>C</span> CreatorOS</Link><span className="sp-prototype">交互原型 · 示例数据</span><Link to="/">返回现有系统 ↗</Link></header>
    <main className="sp-main"><div className="sp-title"><h1>{current ? current.name : "创作空间"}</h1>{current ? <button onClick={() => setActive(null)}>← 返回创作空间</button> : <button onClick={() => open("skill")}>＋ 添加 Skill</button>}</div>
      {storageError && <p role="alert">浏览器无法保存，刷新会丢失本次更改。</p>}<p className="sp-notice" role="status">{message}</p>
      {current ? <section className="sp-detail"><div className="sp-detail-info"><span>{skill(current.mind)?.name}</span><b>＋</b><span>{skill(current.production)?.name}</span><small>组合未验证</small></div>{current.description && <p>{current.description}</p>}
        <label className="sp-owner">账号<select value={current.account} onChange={e => assign(current.id, e.target.value)}>{options}</select></label><h2>选题</h2>
        <form className="sp-topic-form" onSubmit={e => { e.preventDefault(); const f = e.currentTarget; const title = String(new FormData(f).get("topic") ?? "").trim(); if (!title) return; setW(v => ({ ...v, series: v.series.map(s => s.id === current.id ? { ...s, topics: [...s.topics, title] } : s) })); f.reset(); setMessage("选题已添加到原型"); }}><input name="topic" aria-label="选题标题" placeholder="输入选题" required maxLength={200} /><button>添加选题</button></form>
        {current.topics.length ? <ol className="sp-topics">{current.topics.map((t, i) => <li key={i}>{t}<span>待生产</span></li>)}</ol> : <p className="sp-empty">暂无选题</p>}<div className="sp-production"><button disabled>开始生产</button><span>原型未接生产服务</span></div>
      </section> : <div className="sp-columns">
        <section className="sp-library" aria-label="Skill 库"><div className="sp-section-heading"><h2>Skill 库</h2><span>{w.skills.length}</span></div><input className="sp-search" aria-label="搜索 Skill" placeholder="搜索 Skill" value={search} onChange={e => setSearch(e.target.value)} />
          {(["mind", "production"] as Kind[]).map(kind => <div className={`sp-group ${kind}`} key={kind}><h3>{kind === "mind" ? "内容方法" : "制作方式"}</h3>{w.skills.filter(s => s.kind === kind && `${s.name} ${s.detail}`.toLowerCase().includes(search.toLowerCase())).map(s => <button key={s.id} className={`sp-skill ${mind === s.id || production === s.id ? "selected" : ""}`} aria-pressed={mind === s.id || production === s.id} draggable onDragStart={e => startDrag(e, "skill", s.id)} onClick={() => choose(s)}><span className="sp-skill-icon" aria-hidden="true">{kind === "mind" ? "✳" : "◈"}</span><span><strong>{s.name}</strong><small>{s.detail}</small></span><span aria-hidden="true">{mind === s.id || production === s.id ? "✓" : "+"}</span></button>)}</div>)}
          {w.skills.every(s => !`${s.name} ${s.detail}`.toLowerCase().includes(search.toLowerCase())) && <p className="sp-empty">没有匹配的 Skill</p>}
        </section>
        <section className="sp-workbench" aria-label="组合栏目"><h2>组合栏目</h2><div className="sp-composer">
          {(["mind", "production"] as Kind[]).map((kind, i) => <div key={kind}><div className={`sp-slot ${kind} ${drag?.type === "skill" && skill(drag.id)?.kind === kind ? "accepts" : ""}`} data-testid={`slot-${kind}`} onDragOver={e => e.preventDefault()} onDrop={e => dropSkill(e, kind)}><small>{kind === "mind" ? "内容方法" : "制作方式"}</small>{(kind === "mind" ? mind : production) ? <><strong>{skill(kind === "mind" ? mind : production)?.name}</strong><button aria-label={`移除${kind === "mind" ? "内容方法" : "制作方式"}`} onClick={() => (kind === "mind" ? setMind : setProduction)("")}>×</button></> : <span>从左侧选择或拖入</span>}</div>{i === 0 && <div className="sp-plus">＋</div>}</div>)}
          <button className="sp-primary" disabled={!mind || !production} onClick={() => open("series")}>创建栏目</button>
        </div><div className={`sp-unassigned ${drag?.type === "series" ? "accepts" : ""}`} data-testid="unassigned" onDragOver={e => e.preventDefault()} onDrop={e => dropSeries(e, "")}><h3>未分配栏目</h3>{w.series.filter(s => !s.account).map(card)}{!w.series.some(s => !s.account) && <p className="sp-empty">还没有未分配栏目</p>}</div></section>
        <section className="sp-accounts" aria-label="账号"><h2>账号</h2>{accounts.map((a, i) => <div key={a.id} data-testid={a.id} className={`sp-account tone-${i} ${drag?.type === "series" ? "accepts" : ""}`} onDragOver={e => e.preventDefault()} onDrop={e => dropSeries(e, a.id)}><div className="sp-account-head"><span className="sp-avatar">{a.name.slice(0, 1)}</span><div><h3>{a.name}</h3><small>{a.handle}</small></div></div>{w.series.filter(s => s.account === a.id).map(card)}{!w.series.some(s => s.account === a.id) && <p className="sp-drop-hint">拖入栏目，或在栏目卡片中分配</p>}</div>)}</section>
      </div>}
    </main>
    {modal && <dialog ref={dialog} className="sp-dialog" onCancel={e => { e.preventDefault(); close(); }} aria-label={modal === "skill" ? "添加示例 Skill" : "创建栏目"}><form onSubmit={submit}><div className="sp-dialog-heading"><h2>{modal === "skill" ? "添加 Skill" : "创建栏目"}</h2><button type="button" aria-label="关闭" onClick={close}>×</button></div>
      {modal === "skill" ? <><p>仅添加原型条目，不下载或安装。</p><label>类型<select name="kind"><option value="mind">内容方法</option><option value="production">制作方式</option></select></label><label>GitHub 链接<input name="url" type="url" required placeholder="https://github.com/…" /></label></> : <p>{skill(mind)?.name} ＋ {skill(production)?.name} · 未验证</p>}
      <label>{modal === "skill" ? "Skill 名称" : "栏目名称"}<input name="name" required maxLength={100} autoFocus /></label>{modal === "series" && <label>栏目定位<textarea name="description" rows={2} maxLength={2000} placeholder="面向谁，持续讲什么" /></label>}
      <p role="status" className="sp-dialog-status">{message.startsWith("请填写") ? message : ""}</p><div className="sp-dialog-actions"><button type="button" onClick={close}>取消</button><button className="sp-primary" type="submit">{modal === "skill" ? "添加示例条目" : "创建"}</button></div></form></dialog>}
  </div>;
}
