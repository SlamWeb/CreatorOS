import { Link, NavLink, Outlet, useLocation, useSearchParams } from "react-router-dom";
import { Activity, ClipboardCheck, LayoutGrid, Layers, Sparkles, SquarePen } from "lucide-react";
import { OperationDrawer } from "../features/operations/OperationDrawer";
import { BrandMark } from "./BrandMark";
import { AgentChatProvider } from "./AgentChatHost";
import { TaskInbox, UnseenDot, useWorkInbox, WorkInboxProvider } from "./TaskInbox";

const navItems = [
  { to: "/workspace", label: "账号与栏目", icon: LayoutGrid },
  { to: "/skills", label: "Skill", icon: Layers },
  { to: "/agent", label: "Agent", icon: Sparkles },
  { to: "/observation", label: "Observation", icon: Activity },
  { to: "/eval", label: "Eval", icon: ClipboardCheck },
];

export function Layout() {
  return <WorkInboxProvider><LayoutContent /></WorkInboxProvider>;
}

export function PrimaryNavigation() {
  const location = useLocation();
  const inbox = useWorkInbox();
  return <nav className="primary-nav" aria-label="主导航">
    {navItems.map((item) => (
      <NavLink key={item.to} to={item.to} aria-label={item.label} end={item.to === "/workspace"} className={({ isActive }) => `nav-item ${isActive || (item.to === "/workspace" && location.pathname === "/") ? "active" : ""}`}>
        <span className="nav-icon" aria-hidden="true"><item.icon size={17} strokeWidth={1.8} /></span><span>{item.label}</span>
        {((item.to === "/skills" && inbox.skillUnread) || (item.to === "/workspace" && inbox.seriesUnread)) && <UnseenDot />}
      </NavLink>
    ))}
  </nav>;
}

function LayoutContent() {
  const location = useLocation();
  const [, setParams] = useSearchParams();
  const section = location.pathname.startsWith("/eval") ? "Eval" : location.pathname.startsWith("/observation") ? "Observation" : location.pathname.startsWith("/agent") ? "Agent" : location.pathname.startsWith("/skills") ? "Skill 库" : location.pathname.startsWith("/runs") ? "内容验收" : "账号与栏目";
  return (
    <AgentChatProvider><div className={`studio-shell ${["/", "/workspace"].includes(location.pathname) ? "workspace-studio" : ""} ${["/", "/workspace"].includes(location.pathname) && new URLSearchParams(location.search).has("series") ? "column-workspace" : ""} ${location.pathname.startsWith("/series/") ? "series-studio" : ""} ${location.pathname === "/agent" ? "agent-studio" : ""}`}>
      <a href="#studio-main" className="studio-skip-link">跳到正文</a>
      <aside className="sidebar">
        <Link to="/" className="creative-brand" aria-label="CreatorOS 首页"><BrandMark /><span>CreatorOS</span></Link>
        <PrimaryNavigation />
      </aside>
      <main id="studio-main" className="main-content" tabIndex={-1}>
        <header className="topbar">
          <div>
            <p className="topbar-title">{section}</p>
          </div>
          <div className="topbar-actions"><TaskInbox /><button className="command-trigger" type="button" aria-label="运营指令" title="运营指令 (Ctrl K)" onClick={() => setParams(p => { p.delete("operation"); p.set("command", "new"); return p; })}><SquarePen size={15} strokeWidth={1.8} /></button></div>
        </header>
        <div className="page-container"><Outlet /></div>
        <OperationDrawer />
      </main>
    </div></AgentChatProvider>
  );
}
