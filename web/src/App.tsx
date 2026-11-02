import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { lazy, Suspense } from "react";
import { BrowserRouter, Navigate, Outlet, Route, Routes, useLocation } from "react-router-dom";
import { RouteErrorBoundary } from "./components/RouteErrorBoundary";

// Each page loads on demand. In particular the public home page does not need
// to download the workspace, Agent, evaluation and production interfaces.
const Layout = lazy(() => import("./components/Layout").then(module => ({ default: module.Layout })));
const WorkspacePage = lazy(() => import("./pages/WorkspacePage").then(module => ({ default: module.WorkspacePage })));
const WorkspaceRedirect = lazy(() => import("./pages/WorkspacePage").then(module => ({ default: module.WorkspaceRedirect })));
const SkillsPage = lazy(() => import("./pages/SkillsPage").then(module => ({ default: module.SkillsPage })));
const RunDetailPage = lazy(() => import("./pages/RunsPage").then(module => ({ default: module.RunDetailPage })));
const AgentPage = lazy(() => import("./pages/AgentPage").then(module => ({ default: module.AgentPage })));
const StudioPreview = lazy(() => import("./pages/StudioPreview").then(module => ({ default: module.StudioPreview })));
const ObservationPage = lazy(() => import("./pages/ObservationPage").then(module => ({ default: module.ObservationPage })));
const EvalPage = lazy(() => import("./pages/EvalPage").then(module => ({ default: module.EvalPage })));
const CalibrationPage = lazy(() => import("./pages/CalibrationPage").then(module => ({ default: module.CalibrationPage })));
const HomePage = lazy(() => import("./pages/HomePage").then(module => ({ default: module.HomePage })));

const pageLoading = <div className="state-panel loading-state" role="status">正在加载页面…</div>;

const queryClient = new QueryClient({ defaultOptions: { queries: { staleTime: 5_000, refetchOnWindowFocus: true } } });

export function App() {
  return <QueryClientProvider client={queryClient}><BrowserRouter><Routes>
    <Route path="/studio-preview" element={<RouteErrorBoundary><Suspense fallback={pageLoading}><StudioPreview /></Suspense></RouteErrorBoundary>} />
    <Route element={<AppShell />}>
      <Route element={<PageOutlet />}>
      <Route path="/" element={<HomeEntry />} />
      <Route path="/workspace" element={<WorkspacePage />} />
      <Route path="/skills" element={<SkillsPage />} />
      <Route path="/calibrate" element={<CalibrationPage />} />
      <Route path="/studio" element={<Navigate to="/skills" replace />} />
      <Route path="/series/:seriesId" element={<WorkspaceRedirect />} />
      <Route path="/runs/:runId" element={<RunDetailPage />} />
      <Route path="/agent" element={<AgentPage />} />
      <Route path="/observation" element={<ObservationPage />} />
      <Route path="/eval" element={<EvalPage />} />
      <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Route>
  </Routes></BrowserRouter></QueryClientProvider>;
}

function HomeEntry() {
  // Saved account/series/operation URLs keep their existing workspace behavior.
  return useLocation().search ? <WorkspacePage /> : <HomePage />;
}

function AppShell() {
  const location = useLocation();
  // Keep the same Layout/Agent provider between workspace and legacy deep links.
  return location.pathname === "/" && !location.search ? <Outlet /> : <RouteErrorBoundary><Suspense fallback={pageLoading}><Layout /></Suspense></RouteErrorBoundary>;
}

function PageOutlet() {
  // Keep the shell and its Agent session mounted while another page loads.
  const location = useLocation();
  return <RouteErrorBoundary key={location.pathname}><Suspense fallback={pageLoading}><Outlet /></Suspense></RouteErrorBoundary>;
}
