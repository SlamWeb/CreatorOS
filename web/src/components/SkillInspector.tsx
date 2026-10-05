import { useEffect, useId, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { BookOpen, ChevronRight, File, FileCode, FileImage, FileText, Folder, X } from "lucide-react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { apiUrl, studioApi } from "../api/client";
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

function TreeItems({ nodes, selected, onSelect }: {
  nodes: TreeNode[]; selected: string; onSelect: (path: string) => void;
}) {
  return <ul>{nodes.map(node => <li key={node.path}>{node.file
    ? <button type="button" className="skill-file-item" title={node.path}
      aria-current={selected === node.path ? "page" : undefined} onClick={() => onSelect(node.path)}>
      <FileIcon file={node.file} /><span>{node.name}</span>
    </button>
    : <details open className="skill-file-folder"><summary><ChevronRight size={13} /><Folder size={16} /><span>{node.name}</span></summary>
      <TreeItems nodes={node.children} selected={selected} onSelect={onSelect} />
    </details>}
  </li>)}</ul>;
}

export function SkillInspector({ skill, onClose, onAdd }: {
  skill: ProducerSkillItem; onClose: () => void; onAdd?: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  const [selected, setSelected] = useState("SKILL.md");
  const [source, setSource] = useState(false);
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
  const selectFile = (path: string) => { setSelected(path); setSource(false); };
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
    onCancel={event => { event.preventDefault(); onClose(); }}
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
        {onAdd && <button type="button" className="skill-inspector-add" onClick={onAdd}>加入组合</button>}
        <button type="button" className="skill-inspector-close" aria-label="关闭 Skill 详情" autoFocus onClick={onClose}><X size={20} /></button>
      </div>
    </header>
    {files.isPending ? <p className="skill-inspector-state" role="status">正在读取 Skill 文件…</p>
      : files.isError ? <div className="skill-inspector-state" role="alert"><p>{files.error.message}</p>
        <button type="button" onClick={() => void files.refetch()}>重新读取</button></div>
      : <div className="skill-inspector-body">
        <details open className="skill-inspector-sidebar"><summary>文件</summary>
          <nav aria-label="Skill 文件结构"><TreeItems nodes={tree} selected={selected} onSelect={selectFile} /></nav>
        </details>
        <section className="skill-inspector-preview" aria-label="文件预览">
          <div className="skill-file-toolbar"><span className="skill-file-path">{selected}</span>
            {file?.kind === "markdown" && <div className="skill-file-modes" aria-label="Markdown 显示方式">
              <button type="button" aria-pressed={!source} onClick={() => setSource(false)}>阅读</button>
              <button type="button" aria-pressed={source} onClick={() => setSource(true)}>源码</button>
            </div>}
          </div>
          <div className="skill-file-content" key={selected}>
            {!file && <p role="alert">此文件不存在，请关闭后重新读取 Skill。</p>}
            {file?.kind === "unsupported" && <p>此文件不支持预览。可以在本地 Skill 目录中查看；网页不会执行它。</p>}
            {file?.kind === "image" && <SkillImage key={imageUrl} url={imageUrl} path={file.path} />}
            {(file?.kind === "markdown" || file?.kind === "text") && <>
              {text.isPending && <p role="status">正在读取文件…</p>}
              {text.isError && <div role="alert"><p>{text.error.message}</p><button type="button" onClick={() => void text.refetch()}>重新读取文件</button></div>}
              {text.data && (file.kind === "text" || source
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
