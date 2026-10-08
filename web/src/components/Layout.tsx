import { Link, NavLink, Outlet, useLocation, useSearchParams } from "react-router-dom";
import { Activity, LayoutGrid, Layers, Sparkles, SquarePen } from "lucide-react";
import { OperationDrawer } from "../features/operations/OperationDrawer";
import { BrandMark } from "./BrandMark";

const navItems = [
  { to: "/", label: "账号与栏目", icon: LayoutGrid },
  { to: "/skills", label: "Skill", icon: Layers },
  { to: "/agent", label: "Agent", icon: Sparkles },
  { to: "/observation", label: "Observation", icon: Activity },
];

export function Layout() {
  const location = useLocation();
  const [, setParams] = useSearchParams();
  const section = location.pathname.startsWith("/observation") ? "Observation" : location.pathname.startsWith("/agent") ? "Agent" : location.pathname.startsWith("/skills") ? "Skill 库" : location.pathname.startsWith("/runs") ? "内容验收" : "账号与栏目";
  return (
    <div className={`studio-shell ${location.pathname === "/" ? "workspace-studio" : ""} ${location.pathname.startsWith("/series/") ? "series-studio" : ""} ${location.pathname === "/agent" ? "agent-studio" : ""}`}>
      <aside className="sidebar">
        <Link to="/" className="creative-brand" aria-label="CreatorOS 首页"><BrandMark /><span>CreatorOS</span></Link>
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
