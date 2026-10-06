import { useEffect, useId, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { BookOpen, ChevronRight, File, FileCode, FileImage, FileText, Folder, X } from "lucide-react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { ApiError, apiUrl, studioApi } from "../api/client";
import type { ProducerSkillFile, ProducerSkillItem } from "../api/types";
import "./skill-inspector.css";

type TreeNode = { name: string; path: string; file?: ProducerSkillFile; children: TreeNode[] };

function fileTree(files: ProducerSkillFile[]) {
  const root: TreeNode = { name: "", path: "", children: [] };
  for (const file of files) {
    let branch = root;
    const parts = file.path.split("/");
    parts.forEach((name, index) => {
      let node = branch.children.find(child => child.name === name);
      if (!node) {
        node = { name, path: parts.slice(0, index + 1).join("/"), children: [] };
        branch.children.push(node);
      }
      if (index === parts.length - 1) node.file = file;
      branch = node;
    });
  }
  const sort = (nodes: TreeNode[]) => {
    nodes.sort((a, b) => Number(b.name === "SKILL.md") - Number(a.name === "SKILL.md")
      || Number(Boolean(a.file)) - Number(Boolean(b.file)) || a.name.localeCompare(b.name));
    nodes.forEach(node => sort(node.children));
  };
  sort(root.children);
  return root.children;
}

function FileIcon({ file }: { file: ProducerSkillFile }) {
  if (file.kind === "image") return <FileImage size={16} />;
  if (file.kind === "markdown") return file.path === "SKILL.md" ? <BookOpen size={16} /> : <FileText size={16} />;
  return file.kind === "text" ? <FileCode size={16} /> : <File size={16} />;
}

function TreeItems({ nodes, selected, onSelect, disabled }: {
  nodes: TreeNode[]; selected: string; onSelect: (path: string) => void; disabled?: boolean;
}) {
  return <ul>{nodes.map(node => <li key={node.path}>{node.file
    ? <button type="button" className="skill-file-item" title={node.path}
      disabled={disabled} aria-current={selected === node.path ? "page" : undefined} onClick={() => onSelect(node.path)}>
      <FileIcon file={node.file} /><span>{node.name}</span>
    </button>
    : <details open className="skill-file-folder"><summary><ChevronRight size={13} /><Folder size={16} /><span>{node.name}</span></summary>
      <TreeItems nodes={node.children} selected={selected} onSelect={onSelect} disabled={disabled} />
    </details>}
  </li>)}</ul>;
}

export function SkillInspector({ skill, onClose, onAdd }: {
  skill: ProducerSkillItem; onClose: () => void; onAdd?: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const titleId = useId();
  const [selected, setSelected] = useState("SKILL.md");
  const [source, setSource] = useState(false);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [editDigest, setEditDigest] = useState("");
  const [editError, setEditError] = useState("");
  const [latestContent, setLatestContent] = useState<string | null>(null);
  const [agentRequest, setAgentRequest] = useState("");
  const [agentOpen, setAgentOpen] = useState(false);
  const files = useQuery({
    queryKey: ["producer-skill-files", skill.id],
    queryFn: ({ signal }) => studioApi.producerSkillFiles(skill.id, signal),
    retry: false, gcTime: 0,
  });
  const tree = useMemo(() => fileTree(files.data?.files ?? []), [files.data]);
  const file = files.data?.files.find(item => item.path === selected);
  const text = useQuery({
    queryKey: ["producer-skill-file", skill.id, selected],
    queryFn: ({ signal }) => studioApi.producerSkillText(skill.id, selected, signal),
    enabled: file?.kind === "markdown" || file?.kind === "text",
    retry: false, gcTime: 0,
  });
  const dirty = editing && draft !== text.data?.content;
  const save = useMutation({
    mutationFn: () => studioApi.saveProducerSkillText(skill.id, {
      path: selected, content: draft, expected_digest: editDigest,
    }),
    onSuccess: result => {
      queryClient.setQueryData(["producer-skill-file", skill.id, selected], result);
      queryClient.setQueryData(["producer-skill-files", skill.id], (current: typeof files.data) => current
        ? { ...current, digest: result.digest, name: result.name, description: result.description } : current);
      void queryClient.invalidateQueries({ queryKey: ["producer-skills"] });
      setDraft(result.content); setEditing(false); setEditError(""); setLatestContent(null);
    },
    onError: error => setEditError(error.message),
  });
  useEffect(() => {
    const node = dialog.current;
    const origin = document.activeElement as HTMLElement | null;
    if (node && !node.open) {
      node.showModal();
      node.querySelector<HTMLButtonElement>(".skill-inspector-close")?.focus();
    }
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      node?.close();
      document.body.style.overflow = previousOverflow;
      if (origin?.isConnected) origin.focus();
    };
  }, []);
  useEffect(() => {
    if (text.data && !editing) setDraft(text.data.content);
  }, [text.data, editing]);
  const confirmDiscard = () => !dirty || window.confirm("当前文件有未保存修改。离开会丢弃草稿，仍要离开吗？");
  const selectFile = (path: string) => {
    if (save.isPending || path === selected || !confirmDiscard()) return;
    setEditing(false); setDraft(""); setEditError(""); setLatestContent(null); setSelected(path); setSource(false);
  };
  const close = () => { if (!save.isPending && confirmDiscard()) onClose(); };
  const readLatest = async () => {
    const [latestFiles, latestText] = await Promise.all([files.refetch(), text.refetch()]);
    if (latestFiles.data && latestText.data) {
      setEditDigest(latestText.data.digest);
      setLatestContent(latestText.data.content);
      setEditError("已读取服务器当前版本；你的草稿仍保留。检查差异后可明确覆盖保存。");
    }
  };
  const handToAgent = () => {
    if (!agentRequest.trim() || save.isPending || !confirmDiscard()) return;
    const nonce = crypto.randomUUID();
    const payload = { skillId: skill.id, skillName: name, path: selected, request: agentRequest.trim() };
    try { window.sessionStorage.setItem(`creatoros.skill-edit-handoff:${nonce}`, JSON.stringify(payload)); }
    catch { setEditError("无法保存本次聊天草稿，请检查浏览器会话存储后重试。"); return; }
    const existing = window.sessionStorage.getItem("creatoros.agent.selected-chat.v1:overview");
    navigate(`/agent${existing ? `?chat=${encodeURIComponent(existing)}&` : "?"}skill_edit=${encodeURIComponent(nonce)}`);
  };
  const resolveReference = (value: string | undefined) => {
    if (!value || /^(?:[a-z][a-z\d+.-]*:|\/\/)/i.test(value)) return undefined;
    try {
      const path = decodeURIComponent(new URL(value, `https://skill.local/${selected}`).pathname).slice(1);
      return files.data?.files.find(item => item.path === path);
    } catch { return undefined; }
  };
  const role = files.data ? files.data.role : skill.role;
  const name = files.data?.name ?? skill.name;
  const imageUrl = file?.kind === "image" ? apiUrl(studioApi.producerSkillFilePath(skill.id, file.path)) : "";
  const markdown = text.data?.content.replace(/^---\r?\n[\s\S]*?\r?\n---(?:\r?\n|$)/, "") ?? "";

  return <dialog ref={dialog} className="skill-inspector" aria-labelledby={titleId}
    onCancel={event => { event.preventDefault(); close(); }}
    onKeyDown={event => {
      if (event.key !== "Tab") return;
      const controls = Array.from(event.currentTarget.querySelectorAll<HTMLElement>(
        'button:not(:disabled), a[href], summary, [tabindex="0"]',
      )).filter(node => node.offsetParent !== null);
      const first = controls[0], last = controls.at(-1);
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    }}>
    <header className="skill-inspector-header">
      <div className="skill-inspector-heading"><span className="skill-inspector-mark"><BookOpen size={23} /></span>
        <div><div className="skill-inspector-title"><h2 id={titleId}>{name}</h2>
          <span className="skill-inspector-role">{role === "mind" ? "Mind" : role === "production" ? "Visualize" : role ? "完整 Skill" : "未分类"}</span></div>
          <p>{files.data?.description ?? skill.description}</p>
        </div>
      </div>
      <div className="skill-inspector-actions">
        {onAdd && <button type="button" className="skill-inspector-add" disabled={save.isPending}
          onClick={() => { if (confirmDiscard()) onAdd(); }}>加入组合</button>}
        <button type="button" className="skill-inspector-close" aria-label="关闭 Skill 详情" autoFocus disabled={save.isPending} onClick={close}><X size={20} /></button>
      </div>
    </header>
    {files.isPending ? <p className="skill-inspector-state" role="status">正在读取 Skill 文件…</p>
      : files.isError ? <div className="skill-inspector-state" role="alert"><p>{files.error.message}</p>
        <button type="button" onClick={() => void files.refetch()}>重新读取</button></div>
      : <div className="skill-inspector-body">
        <details open className="skill-inspector-sidebar"><summary>文件</summary>
          <nav aria-label="Skill 文件结构"><TreeItems nodes={tree} selected={selected} onSelect={selectFile} disabled={save.isPending} /></nav>
        </details>
        <section className="skill-inspector-preview" aria-label="文件预览">
          <div className="skill-file-toolbar"><span className="skill-file-path">{selected}</span>
            {files.data && <button type="button" className="skill-agent-trigger" aria-expanded={agentOpen}
              aria-label="让 Agent 帮我修改当前 Skill" onClick={() => setAgentOpen(!agentOpen)}>Agent 改稿</button>}
            {(file?.kind === "markdown" || file?.kind === "text") && text.data && <div className="skill-file-modes">
              {editing ? <><button type="button" disabled={save.isPending} onClick={() => { if (confirmDiscard()) { setEditing(false); setDraft(text.data?.content ?? ""); setEditError(""); setLatestContent(null); } }}>取消编辑</button>
                <button type="button" disabled={!dirty || save.isPending || !editDigest}
                  onClick={() => { setEditError(""); save.mutate(); }}>{save.isPending ? "正在保存…" : latestContent !== null ? "覆盖保存" : "保存文件"}</button></>
                : <><button type="button" aria-pressed={!source} onClick={() => setSource(false)}>阅读</button>
                  {file.kind === "markdown" && <button type="button" aria-pressed={source} onClick={() => setSource(true)}>源码</button>}
                  {files.data?.editable && <button type="button" onClick={() => { setEditing(true); setDraft(text.data?.content ?? ""); setEditDigest(text.data?.digest ?? ""); setEditError(""); }}>编辑</button>}</>}
            </div>}
          </div>
          <div className="skill-file-content" key={selected}>
            {agentOpen && files.data && <div className="skill-agent-edit"><form onSubmit={event => { event.preventDefault(); handToAgent(); }}><label>让 Agent 修改 {name}
              <textarea value={agentRequest} maxLength={6000} rows={3} onChange={event => setAgentRequest(event.target.value)} placeholder="说明希望调整什么，以及需要保留什么。" /></label>
              <p>会把 Skill 名称、ID、当前文件路径和要求带入 Agent 草稿；检查后由你发送。</p>
              <button type="submit" className="skill-inspector-add" disabled={!agentRequest.trim() || save.isPending}>在 Agent 中继续</button></form></div>}
            {!file && <p role="alert">此文件不存在，请关闭后重新读取 Skill。</p>}
            {file?.kind === "unsupported" && <p>此文件不支持预览。可以在本地 Skill 目录中查看；网页不会执行它。</p>}
            {file?.kind === "image" && <SkillImage key={imageUrl} url={imageUrl} path={file.path} />}
            {(file?.kind === "markdown" || file?.kind === "text") && <>
              {files.data && !files.data.editable && <p className="skill-inspector-state">此 Skill 为只读版本；可在本地 Skill 目录中维护，网页不会写入。</p>}
              {text.isPending && <p role="status">正在读取文件…</p>}
              {text.isError && <div role="alert"><p>{text.error.message}</p><button type="button" onClick={() => void text.refetch()}>重新读取文件</button></div>}
              {text.data && editing ? <div className="skill-file-editor"><textarea aria-label={`编辑 ${selected}`} disabled={save.isPending} value={draft} onChange={event => setDraft(event.target.value)} spellCheck={false} />
                <p>保存会更新本地 Skill 文件，并影响后续新 Run；已创建 Run 使用的冻结版本不变。</p></div>
                : text.data && (file.kind === "text" || source
                ? <pre className="skill-file-source">{text.data.content}</pre>
                : <div className="skill-file-markdown"><Markdown remarkPlugins={[remarkGfm]} skipHtml components={{
                  img: ({ src, alt }) => {
                    const asset = resolveReference(src);
                    return asset?.kind === "image" ? <SkillImage url={apiUrl(studioApi.producerSkillFilePath(skill.id, asset.path))} path={asset.path} />
                      : <span className="skill-file-image-unavailable">{alt || "图片"}（参考文件不可用）</span>;
                  },
                  a: ({ href, children }) => {
                    const linked = resolveReference(href);
                    return linked ? <button type="button" className="skill-file-link" onClick={() => selectFile(linked.path)}>{children}</button>
                      : href && /^https?:\/\//i.test(href) ? <a href={href} target="_blank" rel="noopener noreferrer">{children}</a> : <span>{children}</span>;
                  },
                }}>{markdown}</Markdown></div>)}
              {editError && <div className="skill-edit-feedback" role={save.isError ? "alert" : "status"}><p>{editError}</p>
                {save.error instanceof ApiError && save.error.status === 409 && latestContent === null && <button type="button" onClick={() => void readLatest()}>读取当前版本并保留草稿</button>}
                {latestContent !== null && <details><summary>查看服务器当前版本</summary><pre>{latestContent}</pre></details>}
              </div>}
            </>}
          </div>
        </section>
      </div>}
  </dialog>;
}

function SkillImage({ url, path }: { url: string; path: string }) {
  const [failed, setFailed] = useState(false);
  const [loaded, setLoaded] = useState(false);
  return <div className="skill-image-preview">
    {!loaded && !failed && <p role="status">正在加载图片…</p>}
    {failed ? <div role="alert"><p>图片无法读取或格式不支持。</p>
      <button type="button" onClick={() => { setFailed(false); setLoaded(false); }}>重新加载图片</button></div>
      : <img src={url} alt={path} onLoad={() => setLoaded(true)} onError={() => setFailed(true)} />}
  </div>;
}
