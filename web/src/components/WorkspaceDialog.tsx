import { useEffect, useRef, type ReactNode } from "react";
import { X } from "lucide-react";
import "./workspace-dialog.css";

/** Native modal owns focus trapping, Escape and the top layer, outside rail scrolling. */
export function WorkspaceDialog({ title, children, onClose, busy = false, className = "" }: {
  title: string; children: ReactNode; onClose: () => void; busy?: boolean; className?: string;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const element = dialog.current!;
    element.showModal();
    element.querySelector<HTMLInputElement>("[data-initial-focus]")?.focus();
    return () => { element.close(); if (previous?.isConnected) previous.focus(); };
  }, []);
  return <dialog ref={dialog} aria-label={title} className={`workspace-dialog ${className}`}
    onCancel={event => { event.preventDefault(); if (!busy) onClose(); }}>
    <header className="workspace-dialog-head"><h2>{title}</h2>
      <button type="button" aria-label={`关闭${title}`} className="workspace-icon-button" disabled={busy} onClick={onClose}><X size={18} /></button>
    </header>
    {children}
  </dialog>;
}
