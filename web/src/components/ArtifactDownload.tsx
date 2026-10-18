import { useEffect, useRef, useState } from "react";
import { Download } from "lucide-react";
import { downloadRunImages } from "../api/client";
import type { RevisionView } from "../api/types";

export function ArtifactDownload({ runId, title, revision }: { runId: string; title: string; revision: RevisionView }) {
  const request = useRef<AbortController | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => () => { request.current?.abort(); }, []);

  async function download() {
    if (request.current || !revision.review_digest) return;
    const controller = new AbortController();
    request.current = controller;
    setBusy(true);
    setError("");
    try {
      const { blob, extension } = await downloadRunImages(runId, revision.id, revision.review_digest, controller.signal);
      if (controller.signal.aborted) return;
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      const name = title.replace(/[<>:"/\\|?*\u0000-\u001F]/g, "_").trim().slice(0, 80) || "CreatorOS";
      link.href = url;
      link.download = `${name}-第${revision.revision_number}版.${extension}`;
      document.body.append(link);
      link.click();
      link.remove();
      // Leave the object URL alive long enough for the browser's download handoff.
      window.setTimeout(() => URL.revokeObjectURL(url), 30_000);
    } catch (reason) {
      if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "下载失败，请重试。");
    } finally {
      if (!controller.signal.aborted) { request.current = null; setBusy(false); }
    }
  }

  return <div className="artifact-download">
    <button type="button" className="button button-secondary" onClick={() => void download()} disabled={busy} aria-busy={busy}>
      <Download size={16} aria-hidden="true" />{busy ? "正在准备…" : revision.cards.length === 1 ? "下载图片" : `下载全部（${revision.cards.length}）`}
    </button>
    {error ? <p className="form-error" role="alert">{error}</p> : null}
  </div>;
}
