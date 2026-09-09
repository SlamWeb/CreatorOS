import { Link, NavLink, Outlet, useLocation, useSearchParams } from "react-router-dom";
import { useHealth } from "../api/hooks";
import { OperationDrawer } from "../features/operations/OperationDrawer";
import "./creative-studio.css";

const navItems = [
  { to: "/", label: "今日", icon: "◒" },
  { to: "/creators", label: "账号", icon: "◌" },
  { to: "/runs", label: "运行", icon: "↗" },
  { to: "/agent", label: "Agent", icon: "✦" },
];

export function Layout() {
  const location = useLocation();
  const health = useHealth();
  const [, setParams] = useSearchParams();
  const isDetail = location.pathname !== "/" && !navItems.some((item) => item.to !== "/" && location.pathname === item.to);
  return (
    <div className={`studio-shell ${location.pathname.startsWith("/series/") ? "series-studio" : ""}`}>
      <aside className="sidebar">
        <Link to="/" className="creative-brand" aria-label="CreatorOS 首页"><svg viewBox="0 0 36 36" aria-hidden="true"><path d="M30 9a14 14 0 1 0 0 18l-7-6a5 5 0 1 1 0-6z" fill="#2277f5" /><path d="m30 9-7 6-7-7 6-4z" fill="#74b7ff" /></svg><span>CreatorOS</span></Link>
        <nav className="primary-nav" aria-label="主导航">
          {navItems.map((item) => (
            <NavLink key={item.to} to={item.to} aria-label={item.label} end={item.to === "/"} className={({ isActive }) => `nav-item ${isActive || (item.to === "/creators" && location.pathname.startsWith("/series/")) ? "active" : ""}`}>
              <span className="nav-icon" aria-hidden="true">{item.icon}</span><span>{item.label}</span>
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-footer">
          <div className="connection-dot"><span /> 本地 Studio</div>
          <p>本地执行 · 人工验收</p>
        </div>
      </aside>
      <main className="main-content">
        <header className="topbar">
          <div>
            <p className="eyebrow">{isDetail ? "STUDIO / DETAIL" : "STUDIO / WORKSPACE"}</p>
            <p className="topbar-caption">把栏目选题变成可检查的内容生产</p>
          </div>
          <div className="topbar-actions"><button className="command-trigger" type="button" onClick={() => setParams(p => { p.delete("operation"); p.set("command", "new"); return p; })}>运营指令 <kbd>Ctrl K</kbd></button><HealthStatus health={health} /></div>
        </header>
        <div className="page-container"><Outlet /></div>
        <OperationDrawer />
      </main>
    </div>
  );
}

function HealthStatus({ health }: { health: ReturnType<typeof useHealth> }) {
  if (health.isError) return <div className="topbar-status status-problem" title={health.error.message}><span className="status-dot" /> 服务连接失败</div>;
  if (health.isPending) return <div className="topbar-status"><span className="status-dot" /> 正在检查</div>;
  if (!health.data.codex_available) return <div className="topbar-status status-problem" title="请先在本机安装并登录 Codex CLI"><span className="status-dot" /> Codex 未就绪</div>;
  if (!health.data.operation_parser_configured) return <div className="topbar-status" title="可使用表单；自然语言指令需要 DEEPSEEK_API_KEY"><span className="status-dot" /> 表单模式</div>;
  return <div className="topbar-status"><span className="status-dot" /> 本地就绪</div>;
}
