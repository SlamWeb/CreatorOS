import { Link, NavLink, Outlet, useLocation, useSearchParams } from "react-router-dom";
import { Sunrise, Layers, Users, TrendingUp, Sparkles, SquarePen } from "lucide-react";
import { OperationDrawer } from "../features/operations/OperationDrawer";

const navItems = [
  { to: "/", label: "今日", icon: Sunrise },
  { to: "/studio", label: "创作", icon: Layers },
  { to: "/creators", label: "账号", icon: Users },
  { to: "/runs", label: "运行", icon: TrendingUp },
  { to: "/agent", label: "Agent", icon: Sparkles },
];

export function Layout() {
  const location = useLocation();
  const [, setParams] = useSearchParams();
  const section = location.pathname.startsWith("/agent") ? "Agent" : location.pathname.startsWith("/studio") ? "创作空间" : location.pathname.startsWith("/runs") ? "内容运行" : location.pathname === "/" ? "今天" : "账号与栏目";
  return (
    <div className={`studio-shell ${location.pathname.startsWith("/series/") ? "series-studio" : ""} ${location.pathname === "/agent" ? "agent-studio" : ""}`}>
      <aside className="sidebar">
        <Link to="/" className="creative-brand" aria-label="CreatorOS 首页"><svg viewBox="0 0 36 36" aria-hidden="true"><path d="M30 9a14 14 0 1 0 0 18l-7-6a5 5 0 1 1 0-6z" fill="#2277f5" /><path d="m30 9-7 6-7-7 6-4z" fill="#74b7ff" /></svg><span>CreatorOS</span></Link>
        <nav className="primary-nav" aria-label="主导航">
          {navItems.map((item) => (
            <NavLink key={item.to} to={item.to} aria-label={item.label} end={item.to === "/"} className={({ isActive }) => `nav-item ${isActive || (item.to === "/creators" && location.pathname.startsWith("/series/")) ? "active" : ""}`}>
              <span className="nav-icon" aria-hidden="true"><item.icon size={17} strokeWidth={1.8} /></span><span>{item.label}</span>
            </NavLink>
          ))}
        </nav>
      </aside>
      <main className="main-content">
        <header className="topbar">
          <div>
            <p className="topbar-title">{section}</p>
          </div>
          <div className="topbar-actions"><button className="command-trigger" type="button" aria-label="运营指令" title="运营指令 (Ctrl K)" onClick={() => setParams(p => { p.delete("operation"); p.set("command", "new"); return p; })}><SquarePen size={15} strokeWidth={1.8} /></button></div>
        </header>
        <div className="page-container"><Outlet /></div>
        <OperationDrawer />
      </main>
    </div>
  );
}
