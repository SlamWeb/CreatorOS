import { Component, type ReactNode } from "react";

/** A failed lazy page must not unmount the surrounding workspace or chat. */
export class RouteErrorBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  render() {
    if (!this.state.failed) return this.props.children;
    return <div className="state-panel error-state" role="alert">
      <div><strong>页面暂时加载失败</strong><p>可以重新加载页面，也可以先切换到其他页面。</p></div>
      <button type="button" className="button button-secondary" onClick={() => window.location.reload()}>重新加载页面</button>
    </div>;
  }
}
