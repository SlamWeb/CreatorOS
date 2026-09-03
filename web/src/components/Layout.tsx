import { NavLink, Outlet, useLocation } from "react-router-dom";

const navItems = [
  { to: "/", label: "今日", icon: "◒" },
  { to: "/creators", label: "账号", icon: "◌" },
  { to: "/runs", label: "运行", icon: "↗" },
];

export function Layout() {
  const location = useLocation();
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
          <p>只读预览 · S2</p>
        </div>
      </aside>
      <main className="main-content">
        <header className="topbar">
          <div>
            <p className="eyebrow">{isDetail ? "STUDIO / DETAIL" : "STUDIO / WORKSPACE"}</p>
            <p className="topbar-caption">把栏目选题变成可检查的内容生产</p>
          </div>
          <div className="topbar-status"><span className="status-dot" /> 本地数据</div>
        </header>
        <div className="page-container"><Outlet /></div>
      </main>
    </div>
  );
}
