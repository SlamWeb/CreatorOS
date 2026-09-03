import type { ReactNode } from "react";

export function LoadingState({ label = "正在读取 Studio 数据…" }: { label?: string }) {
  return <div className="state-panel loading-state" role="status"><span className="spinner" />{label}</div>;
}

export function ErrorState({ message, onRetry }: { message: string; onRetry: () => void }) {
  return <div className="state-panel error-state" role="alert">
    <div><span className="state-symbol">!</span><strong>暂时读不到本地服务</strong><p>{message}</p></div>
    <button className="button button-secondary" onClick={onRetry}>重新连接</button>
  </div>;
}

export function EmptyState({ title, children, action }: { title: string; children: ReactNode; action?: ReactNode }) {
  return <div className="empty-state"><div className="empty-orbit">✦</div><h3>{title}</h3><p>{children}</p>{action}</div>;
}
