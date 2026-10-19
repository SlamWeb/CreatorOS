import { useEffect, useId, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BookOpen, File, FileCode, FileImage, FileText, Folder, FolderOpen, Plus, Trash2, X } from "lucide-react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { ApiError, apiUrl, studioApi } from "../api/client";
import type { ProducerSkillFile, ProducerSkillItem, ProducerSkillText } from "../api/types";
import { skillEditPrompt, type SkillEditContext } from "./skillEditHandoff";
import { useAgentChat } from "./AgentChatHost";
import "./skill-inspector.css";

type TreeNode = { name: string; path: string; file?: ProducerSkillFile; children: TreeNode[] };
type AssetDraft = { mode: "add" | "replace"; file: File; path: string; expectedDigest: string };
const MAX_ASSET_BYTES = 4 * 1024 * 1024;
const IMAGE_TYPES = new Set(["image/png", "image/jpeg", "image/webp", "image/gif"]);

async function fileBase64(file: File) {
  const dataUrl = await new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error(`无法读取图片「${file.name}」，请重新选择。`));
    reader.onload = () => resolve(String(reader.result));
    reader.readAsDataURL(file);
  });
  return dataUrl.slice(dataUrl.indexOf(",") + 1);
}

function assetName(file: File) {
  const extension = file.type === "image/jpeg" ? "jpg" : file.type === "image/webp" ? "webp" : file.type === "image/gif" ? "gif" : "png";
  const stem = file.name.replace(/\.[^.]+$/, "").normalize("NFKD")
    .replace(/[^a-zA-Z0-9_-]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 100) || "image";
  return `assets/${stem}.${extension}`;
}

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
    : <TreeFolder node={node} selected={selected} onSelect={onSelect} disabled={disabled} />}
  </li>)}</ul>;
}

function TreeFolder({ node, selected, onSelect, disabled }: {
  node: TreeNode; selected: string; onSelect: (path: string) => void; disabled?: boolean;
}) {
  const [open, setOpen] = useState(true);
  const contentId = useId();
  return <div className="skill-file-folder">
    <button type="button" className="skill-folder-trigger" aria-expanded={open} aria-controls={contentId}
      disabled={disabled} onClick={() => setOpen(value => !value)}>
      {open ? <FolderOpen size={16} /> : <Folder size={16} />}<span>{node.name}</span>
    </button>
    <div id={contentId} hidden={!open}><TreeItems nodes={node.children} selected={selected} onSelect={onSelect} disabled={disabled} /></div>
  </div>;
}

export function SkillInspector({ skill, onClose, onAdd, onDeleted, editContext, onAgentEdit }: {
  skill: ProducerSkillItem; onClose: () => void; onAdd?: () => void;
  onDeleted?: () => void;
  editContext?: SkillEditContext; onAgentEdit?: (prompt: string) => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const assetInput = useRef<HTMLInputElement>(null);
  const assetChoice = useRef<"add" | "replace">("add");
  const chat = useAgentChat();
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
  const [filesOpen, setFilesOpen] = useState(true);
  const [latestOpen, setLatestOpen] = useState(false);
  const [renaming, setRenaming] = useState(false);
  const [renameDirty, setRenameDirty] = useState(false);
  const [renameBusy, setRenameBusy] = useState(false);
  const [assetDraft, setAssetDraft] = useState<AssetDraft | null>(null);
  const [assetError, setAssetError] = useState("");
  const [deleteError, setDeleteError] = useState("");
  const fileListId = useId();
  const latestId = useId();
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
  const dirty = (editing && draft !== text.data?.content) || renameDirty;
  const applySaved = (result: ProducerSkillText) => {
    queryClient.setQueryData(["producer-skill-file", skill.id, result.path], result);
    queryClient.setQueryData(["producer-skill-files", skill.id], (current: typeof files.data) => current
      ? { ...current, digest: result.digest, name: result.name ?? current.name,
        display_name: result.display_name, description: result.description ?? current.description } : current);
    void queryClient.invalidateQueries({ queryKey: ["producer-skills"] });
    if (result.path !== selected) void queryClient.invalidateQueries({ queryKey: ["producer-skill-file", skill.id, selected] });
  };
  const save = useMutation({
    mutationFn: () => studioApi.saveProducerSkillText(skill.id, {
      path: selected, content: draft, expected_digest: editDigest,
    }),
    onSuccess: result => {
      applySaved(result);
      setDraft(result.content); setEditing(false); setEditError(""); setLatestContent(null);
    },
    onError: error => setEditError(error.message),
  });
  const assetSave = useMutation({
    retry: false,
    mutationFn: async (asset: AssetDraft) => studioApi.saveProducerSkillAsset(skill.id, {
      path: asset.path.trim(), data_base64: await fileBase64(asset.file),
      expected_digest: asset.expectedDigest, overwrite: asset.mode === "replace",
    }),
    onSuccess: async result => {
      queryClient.setQueryData(["producer-skill-files", skill.id], (current: typeof files.data) =>
        current ? { ...current, digest: result.digest } : current);
      setAssetDraft(null); setAssetError(""); setSelected(result.path);
      await files.refetch();
      await queryClient.invalidateQueries({ queryKey: ["producer-skill-file", skill.id] });
      void queryClient.invalidateQueries({ queryKey: ["producer-skills"] });
    },
    onError: error => setAssetError(error.message),
  });
  const assetDelete = useMutation({
    retry: false,
    mutationFn: (input: { path: string; digest: string }) => studioApi.deleteProducerSkillAsset(skill.id, {
      path: input.path, expected_digest: input.digest,
    }),
    onSuccess: async result => {
      queryClient.setQueryData(["producer-skill-files", skill.id], (current: typeof files.data) =>
        current ? { ...current, digest: result.digest } : current);
      setAssetError(""); setSelected("SKILL.md");
      await files.refetch();
      await queryClient.invalidateQueries({ queryKey: ["producer-skill-file", skill.id] });
      void queryClient.invalidateQueries({ queryKey: ["producer-skills"] });
    },
    onError: error => setAssetError(error.message),
  });
  const skillDelete = useMutation({
    retry: false,
    mutationFn: (digest: string) => studioApi.deleteProducerSkill(skill.id, { expected_digest: digest }),
    onSuccess: () => {
      if (onDeleted) onDeleted(); else onClose();
      void queryClient.invalidateQueries({ queryKey: ["producer-skills"] });
    },
    onError: error => setDeleteError(error.message),
  });
  const busy = renameBusy || assetSave.isPending || assetDelete.isPending || skillDelete.isPending;
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
  const confirmDiscard = () => !(dirty || assetDraft) || window.confirm("有未保存的修改或待上传图片。离开会丢弃草稿，仍要离开吗？");
  const chooseAsset = (mode: "add" | "replace") => {
    if (!files.data?.editable || !files.data.digest || busy || save.isPending) return;
    if (editing || renaming) { setAssetError("请先保存或取消当前文字、名称编辑，再修改图片。"); return; }
    if (!confirmDiscard()) return;
    assetChoice.current = mode;
    setAssetDraft(null); setAssetError("");
    if (assetInput.current) { assetInput.current.value = ""; assetInput.current.click(); }
  };
  const selectedAsset = (candidate: File | undefined) => {
    if (!candidate || !files.data) return;
    if (!IMAGE_TYPES.has(candidate.type)) { setAssetError("仅支持 PNG、JPEG、WebP 或 GIF 图片。"); return; }
    if (candidate.size > MAX_ASSET_BYTES) { setAssetError("图片不能超过 4 MiB，请压缩后重选。"); return; }
    const mode = assetChoice.current;
    setAssetDraft({ mode, file: candidate, path: mode === "replace" ? selected : assetName(candidate), expectedDigest: files.data.digest });
    setAssetError("");
  };
  const saveAsset = () => {
    if (!assetDraft || busy || save.isPending) return;
    const path = assetDraft.path.trim();
    if (assetDraft.mode === "add" && (!/^assets\/[a-zA-Z0-9][a-zA-Z0-9_./-]*\.(?:png|jpe?g|webp|gif)$/i.test(path)
      || path.length > 512 || path.split("/").some(part => !part || part === "." || part === ".."))) {
      setAssetError("图片路径须位于 assets/，使用字母、数字、横线或下划线，并以 png、jpg、webp 或 gif 结尾。"); return;
    }
    const suffix = path.split(".").at(-1)?.toLowerCase();
    const expectedType = suffix === "jpg" || suffix === "jpeg" ? "image/jpeg" : `image/${suffix}`;
    if (assetDraft.file.type !== expectedType) { setAssetError("路径扩展名必须与所选图片格式一致。"); return; }
    if (assetDraft.mode === "add" && files.data?.files.some(item => item.path === path)) {
      setAssetError("文件路径已存在。请改名后新增，或选中原图使用「替换图片」。"); return;
    }
    assetSave.mutate({ ...assetDraft, path });
  };
  const deleteAsset = () => {
    if (file?.kind !== "image" || !files.data?.digest || busy) return;
    if (editing || renaming) { setAssetError("请先保存或取消当前文字、名称编辑，再删除图片。"); return; }
    if (!confirmDiscard()) return;
    if (!window.confirm(`确定删除图片「${file.path}」吗？此操作会影响后续使用该 Skill 的生产。`)) return;
    setAssetDraft(null); setAssetError("");
    assetDelete.mutate({ path: file.path, digest: files.data.digest });
  };
  const deleteSkill = () => {
    if (!files.data?.editable || !files.data.digest || busy || renaming || save.isPending || !confirmDiscard()) return;
    if (!window.confirm(`确定移除 Skill「${name}」吗？已使用的栏目会阻止删除。`)) return;
    setDeleteError(""); skillDelete.mutate(files.data.digest);
  };
  const selectFile = (path: string) => {
    if (save.isPending || busy || renaming || path === selected || !confirmDiscard()) return;
    setEditing(false); setDraft(""); setEditError(""); setLatestContent(null); setAssetDraft(null); setAssetError(""); setSelected(path); setSource(false);
  };
  const close = () => { if (!save.isPending && !busy && confirmDiscard()) onClose(); };
  const readLatest = async () => {
    const [latestFiles, latestText] = await Promise.all([files.refetch(), text.refetch()]);
    if (latestFiles.data && latestText.data) {
      setEditDigest(latestText.data.digest);
      setLatestContent(latestText.data.content);
      setEditError("已读取服务器当前版本；你的草稿仍保留。检查差异后可明确覆盖保存。");
    }
  };
  const handToAgent = () => {
    if (!agentRequest.trim() || save.isPending || busy || renaming || !confirmDiscard()) return;
    const payload = { ...editContext, skillId: skill.id, skillName: name, path: selected, request: agentRequest.trim() };
    if (onAgentEdit) { onAgentEdit(skillEditPrompt(payload)); return; }
    chat.open({ creatorId: editContext?.creatorId ?? null, draftSeed: skillEditPrompt(payload) });
    onClose();
  };
  const resolveReference = (value: string | undefined) => {
    if (!value || /^(?:[a-z][a-z\d+.-]*:|\/\/)/i.test(value)) return undefined;
    try {
      const path = decodeURIComponent(new URL(value, `https://skill.local/${selected}`).pathname).slice(1);
      return files.data?.files.find(item => item.path === path);
    } catch { return undefined; }
  };
  const role = files.data ? files.data.role : skill.role;
  const name = files.data?.display_name ?? files.data?.name ?? skill.display_name ?? skill.name;
  const imagePath = (path: string) => `${apiUrl(studioApi.producerSkillFilePath(skill.id, path))}&digest=${encodeURIComponent(files.data?.digest ?? "")}`;
  const imageUrl = file?.kind === "image" ? imagePath(file.path) : "";
  const markdown = text.data?.content.replace(/^---\r?\n[\s\S]*?\r?\n---(?:\r?\n|$)/, "") ?? "";
  const assetConflict = assetSave.error instanceof ApiError && assetSave.error.status === 409
    || assetDelete.error instanceof ApiError && assetDelete.error.status === 409
      && assetDelete.error.code !== "skill_asset_referenced";
  const assetProblem = assetSave.error instanceof ApiError ? assetSave.error : assetDelete.error instanceof ApiError ? assetDelete.error : null;
  const blockers = skillDelete.error instanceof ApiError && Array.isArray(skillDelete.error.details)
    ? skillDelete.error.details.filter((item): item is { id: string; name: string } =>
      !!item && typeof item === "object" && "id" in item && "name" in item) : [];

  return <dialog ref={dialog} className="skill-inspector" aria-labelledby={titleId}
    onCancel={event => { event.preventDefault(); close(); }}
    onKeyDown={event => {
      if (event.key !== "Tab") return;
      const controls = Array.from(event.currentTarget.querySelectorAll<HTMLElement>(
        'button:not(:disabled), input:not(:disabled), textarea:not(:disabled), select:not(:disabled), a[href], summary, [tabindex="0"]',
      )).filter(node => node.offsetParent !== null);
      const first = controls[0], last = controls.at(-1);
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    }}>
    <input ref={assetInput} className="skill-asset-input" type="file" accept="image/png,image/jpeg,image/webp,image/gif"
      aria-label="选择 Skill 图片" onChange={event => selectedAsset(event.target.files?.[0])} />
    <header className="skill-inspector-header">
      <div className="skill-inspector-heading"><span className="skill-inspector-mark"><BookOpen size={23} /></span>
        <div><div className="skill-inspector-title"><h2 id={titleId}>{name}</h2>
          {files.data?.editable && <button type="button" className="skill-rename-trigger" disabled={editing || renaming || save.isPending || busy || !!assetDraft}
            onClick={() => setRenaming(true)}>改名</button>}
          <span className="skill-inspector-role">{role === "mind" ? "Mind" : role === "production" ? "Visualize" : role ? "完整 Skill" : "未分类"}</span></div>
          <p>{files.data?.description ?? skill.description}</p>
          {renaming && <SkillRenameForm skillId={skill.id} initialName={name} onDirty={setRenameDirty} onBusy={setRenameBusy}
            onCancel={() => { setRenaming(false); setRenameDirty(false); }}
            onSaved={result => { applySaved(result); setRenaming(false); setRenameDirty(false); }} />}
        </div>
      </div>
      <div className="skill-inspector-actions">
        {onAdd && <button type="button" className="skill-inspector-add" disabled={save.isPending || busy}
          onClick={() => { if (confirmDiscard()) onAdd(); }}>加入组合</button>}
        <button type="button" className="skill-inspector-close" aria-label="关闭 Skill 详情" autoFocus disabled={save.isPending || busy} onClick={close}><X size={20} /></button>
      </div>
    </header>
    {files.isPending ? <p className="skill-inspector-state" role="status">正在读取 Skill 文件…</p>
      : files.isError ? <div className="skill-inspector-state" role="alert"><p>{files.error.message}</p>
        <button type="button" onClick={() => void files.refetch()}>重新读取</button></div>
      : <div className="skill-inspector-body">
        <aside className="skill-inspector-sidebar">
          <button type="button" className="skill-files-trigger" aria-expanded={filesOpen} aria-controls={fileListId}
            onClick={() => setFilesOpen(value => !value)}>文件<span>{filesOpen ? "收起" : "展开"}</span></button>
          {files.data?.editable && <button type="button" className="skill-asset-add" disabled={busy || save.isPending || renaming}
            onClick={() => chooseAsset("add")}><Plus size={14} aria-hidden="true" />添加图片</button>}
          <nav id={fileListId} hidden={!filesOpen} aria-label="Skill 文件结构"><TreeItems nodes={tree} selected={selected} onSelect={selectFile} disabled={save.isPending || busy || renaming} /></nav>
        </aside>
        <section className="skill-inspector-preview" aria-label="文件预览">
          <div className="skill-file-toolbar"><span className="skill-file-path">{selected}</span>
            {file?.kind === "image" && files.data?.editable && <div className="skill-asset-toolbar">
              <button type="button" disabled={busy || save.isPending || renaming} onClick={() => chooseAsset("replace")}>替换图片</button>
              <button type="button" className="skill-danger-link" disabled={busy || save.isPending || renaming} onClick={deleteAsset}>删除图片</button>
            </div>}
            {files.data && <button type="button" className="skill-agent-trigger" aria-expanded={agentOpen}
              aria-label="让 Agent 帮我修改当前 Skill" disabled={renaming} onClick={() => setAgentOpen(!agentOpen)}>Agent 改稿</button>}
            {(file?.kind === "markdown" || file?.kind === "text") && text.data && <div className="skill-file-modes">
              {editing ? <><button type="button" disabled={save.isPending} onClick={() => { if (confirmDiscard()) { setEditing(false); setDraft(text.data?.content ?? ""); setEditError(""); setLatestContent(null); } }}>取消编辑</button>
                <button type="button" disabled={!dirty || save.isPending || !editDigest}
                  onClick={() => { setEditError(""); save.mutate(); }}>{save.isPending ? "正在保存…" : latestContent !== null ? "覆盖保存" : "保存文件"}</button></>
                : <><button type="button" aria-pressed={!source} onClick={() => setSource(false)}>阅读</button>
                  {file.kind === "markdown" && <button type="button" aria-pressed={source} onClick={() => setSource(true)}>源码</button>}
                  {files.data?.editable && <button type="button" disabled={renaming || busy || !!assetDraft} onClick={() => { setEditing(true); setDraft(text.data?.content ?? ""); setEditDigest(text.data?.digest ?? ""); setEditError(""); }}>编辑</button>}</>}
            </div>}
          </div>
          <div className="skill-file-content" key={selected}>
            {assetDraft && <div className="skill-asset-draft" role="group" aria-label="待保存图片">
              <strong>{assetDraft.mode === "add" ? "添加图片" : "替换当前图片"}</strong>
              <p>已选择 {assetDraft.file.name}（{(assetDraft.file.size / 1024 / 1024).toFixed(2)} MiB）；保存前不会修改 Skill。</p>
              {assetDraft.mode === "add" ? <label>保存路径<input value={assetDraft.path} maxLength={512} disabled={assetSave.isPending}
                onChange={event => { setAssetDraft({ ...assetDraft, path: event.target.value }); setAssetError(""); }} /></label>
                : <p>保留路径：<code>{assetDraft.path}</code></p>}
              <div className="skill-asset-draft-actions"><button type="button" className="skill-inspector-add" disabled={busy || save.isPending} onClick={saveAsset}>
                {assetSave.isPending ? "正在保存…" : "保存图片"}</button>
                <button type="button" disabled={assetSave.isPending} onClick={() => { setAssetDraft(null); setAssetError(""); }}>取消</button></div>
            </div>}
            {(assetError || assetDelete.isPending) && <div className="skill-asset-feedback" role={assetError ? "alert" : "status"}>
              <p>{assetDelete.isPending ? "正在删除图片…" : assetError}</p>
              {assetProblem?.referencingFiles?.length ? <p>引用它的文件：{assetProblem.referencingFiles.join("、")}。请先修改引用，再删除图片。</p> : null}
              {assetConflict && <button type="button" disabled={busy} onClick={async () => {
                const latest = await files.refetch();
                if (latest.data) {
                  setAssetDraft(current => current ? { ...current, expectedDigest: latest.data.digest } : current);
                  setAssetError("已读取最新文件清单。请核对图片与路径，再手动保存或删除。");
                }
              }}>读取最新文件清单</button>}
            </div>}
            {agentOpen && files.data && <div className="skill-agent-edit"><form onSubmit={event => { event.preventDefault(); handToAgent(); }}><label>让 Agent 修改 {name}
              <textarea value={agentRequest} maxLength={6000} rows={3} onChange={event => setAgentRequest(event.target.value)} placeholder="说明希望调整什么，以及需要保留什么。" /></label>
              <button type="submit" className="skill-inspector-add" disabled={!agentRequest.trim() || save.isPending || busy || renaming}>在 Agent 中继续</button></form></div>}
            {!file && <p role="alert">此文件不存在，请关闭后重新读取 Skill。</p>}
            {file?.kind === "unsupported" && <p>不支持预览，请在本地查看。</p>}
            {file?.kind === "image" && <SkillImage key={imageUrl} url={imageUrl} path={file.path} />}
            {(file?.kind === "markdown" || file?.kind === "text") && <>
              {files.data && !files.data.editable && <p className="skill-inspector-state">只读 Skill</p>}
              {text.isPending && <p role="status">正在读取文件…</p>}
              {text.isError && <div role="alert"><p>{text.error.message}</p><button type="button" onClick={() => void text.refetch()}>重新读取文件</button></div>}
              {text.data && editing ? <div className="skill-file-editor"><textarea aria-label={`编辑 ${selected}`} disabled={save.isPending} value={draft} onChange={event => setDraft(event.target.value)} spellCheck={false} />
                <p>修改只影响后续生产。</p></div>
                : text.data && (file.kind === "text" || source
                ? <pre className="skill-file-source">{text.data.content}</pre>
                : <div className="skill-file-markdown"><Markdown remarkPlugins={[remarkGfm]} skipHtml components={{
                  img: ({ src, alt }) => {
                    const asset = resolveReference(src);
                    return asset?.kind === "image" ? <SkillImage key={imagePath(asset.path)} url={imagePath(asset.path)} path={asset.path} />
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
                {latestContent !== null && <div className="skill-current-version">
                  <button type="button" aria-expanded={latestOpen} aria-controls={latestId} onClick={() => setLatestOpen(value => !value)}>
                    {latestOpen ? "收起服务器当前版本" : "查看服务器当前版本"}</button>
                  <pre id={latestId} hidden={!latestOpen}>{latestContent}</pre>
                </div>}
              </div>}
            </>}
          </div>
        </section>
      </div>}
    {files.data?.editable && <footer className="skill-inspector-footer">
      <button type="button" className="skill-delete-trigger" disabled={busy || save.isPending || renaming} onClick={deleteSkill}>
        <Trash2 size={15} />{skillDelete.isPending ? "正在移除…" : "删除这个 Skill"}</button>
      {deleteError && <div role="alert"><p>{deleteError}</p>
        {blockers.length > 0 && <p>被这些栏目使用：{blockers.map(item => `${item.name}（${item.id}）`).join("、")}。请先解除绑定。</p>}
        {skillDelete.error instanceof ApiError && skillDelete.error.status === 409 && skillDelete.error.code !== "skill_in_use" &&
          <button type="button" disabled={busy} onClick={() => void files.refetch()}>读取最新版本</button>}
      </div>}
    </footer>}
  </dialog>;
}

function SkillRenameForm({ skillId, initialName, onDirty, onBusy, onCancel, onSaved }: {
  skillId: string; initialName: string; onDirty: (dirty: boolean) => void; onBusy: (busy: boolean) => void;
  onCancel: () => void; onSaved: (result: ProducerSkillText) => void;
}) {
  const [value, setValue] = useState(initialName);
  const submitting = useRef(false);
  const snapshot = useQuery({ queryKey: ["producer-skill-rename", skillId],
    queryFn: ({ signal }) => studioApi.producerSkillText(skillId, "SKILL.md", signal),
    retry: false, gcTime: 0, staleTime: Infinity, refetchOnWindowFocus: false, refetchOnReconnect: false });
  const rename = useMutation({ retry: false, mutationFn: () => studioApi.saveProducerSkillText(skillId, {
    path: "SKILL.md", content: snapshot.data!.content, expected_digest: snapshot.data!.digest, display_name: value.trim(),
  }), onSuccess: onSaved, onSettled: () => { submitting.current = false; } });
  useEffect(() => { onDirty(value !== initialName); }, [value, initialName, onDirty]);
  useEffect(() => { onBusy(rename.isPending); }, [rename.isPending, onBusy]);
  useEffect(() => () => onBusy(false), [onBusy]);
  return <form className="skill-rename-form" aria-label="修改 Skill 显示名称" onSubmit={event => {
    event.preventDefault();
    if (snapshot.data && !submitting.current && value.trim()) { submitting.current = true; rename.mutate(); }
  }}>
    <label>显示名称<input aria-label="Skill 显示名称" autoFocus maxLength={80} required value={value} disabled={rename.isPending}
      onChange={event => setValue(event.target.value)} /></label>
    <div><button type="submit" className="skill-inspector-add" disabled={!snapshot.data || rename.isPending || !value.trim() || value.trim() === initialName}>
      {rename.isPending ? "正在保存…" : "保存名称"}</button>
      <button type="button" className="skill-rename-cancel" disabled={rename.isPending} onClick={onCancel}>取消</button></div>
    {snapshot.isPending && <span role="status">正在读取…</span>}
    {snapshot.error && <span role="alert">{snapshot.error.message}<button type="button" onClick={() => void snapshot.refetch()}>重试读取</button></span>}
    {rename.error && <span role="alert">{rename.error.message}{rename.error instanceof ApiError && rename.error.status === 409 ? " 请取消改名后重新打开，核对最新名称。" : ""}</span>}
  </form>;
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
