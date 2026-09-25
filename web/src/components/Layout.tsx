import { Link, NavLink, Outlet, useLocation, useSearchParams } from "react-router-dom";
import { LayoutGrid, Layers, Sparkles, SquarePen } from "lucide-react";
import { OperationDrawer } from "../features/operations/OperationDrawer";

const navItems = [
  { to: "/", label: "栏目", icon: LayoutGrid },
  { to: "/skills", label: "Skill", icon: Layers },
  { to: "/agent", label: "Agent", icon: Sparkles },
];

export function Layout() {
  const location = useLocation();
  const [, setParams] = useSearchParams();
  const section = location.pathname.startsWith("/agent") ? "Agent" : location.pathname.startsWith("/skills") ? "Skill 库" : location.pathname.startsWith("/runs") ? "内容验收" : "栏目";
  return (
    <div className={`studio-shell ${location.pathname === "/" ? "workspace-studio" : ""} ${location.pathname.startsWith("/series/") ? "series-studio" : ""} ${location.pathname === "/agent" ? "agent-studio" : ""}`}>
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
