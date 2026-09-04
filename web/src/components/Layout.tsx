import { NavLink, Outlet, useLocation, useSearchParams } from "react-router-dom";
import { OperationDrawer } from "../features/operations/OperationDrawer";

const navItems = [
  { to: "/", label: "今日", icon: "◒" },
  { to: "/creators", label: "账号", icon: "◌" },
  { to: "/runs", label: "运行", icon: "↗" },
];

export function Layout() {
  const location = useLocation();
  const [, setParams] = useSearchParams();
  const isDetail = location.pathname !== "/" && !navItems.some((item) => item.to !== "/" && location.pathname === item.to);
  return (
    <div className="studio-shell">
      <aside className="sidebar">
        <div className="brand-mark" aria-label="CreatorOS">C<span>O</span></div>
        <div className="brand-name">CreatorOS <span>Studio</span></div>
        <nav className="primary-nav" aria-label="主导航">
          {navItems.map((item) => (
            <NavLink key={item.to} to={item.to} end={item.to === "/"} className={({ isActive }) => `nav-item ${isActive ? "active" : ""}`}>
              <span className="nav-icon">{item.icon}</span><span>{item.label}</span>
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
          <div className="topbar-actions"><button className="command-trigger" type="button" onClick={() => setParams(p => { p.delete("operation"); p.set("command", "new"); return p; })}>运营指令 <kbd>Ctrl K</kbd></button><div className="topbar-status"><span className="status-dot" /> 本地数据</div></div>
        </header>
        <div className="page-container"><Outlet /></div>
        <OperationDrawer />
      </main>
    </div>
  );
}
