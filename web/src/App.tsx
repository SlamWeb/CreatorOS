import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Navigate, Outlet, Route, Routes, useLocation } from "react-router-dom";
import { Layout } from "./components/Layout";
import { WorkspacePage, WorkspaceRedirect } from "./pages/WorkspacePage";
import { SkillsPage } from "./pages/SkillsPage";
import { RunDetailPage } from "./pages/RunsPage";
import { AgentPage } from "./pages/AgentPage";
import { StudioPreview } from "./pages/StudioPreview";
import { ObservationPage } from "./pages/ObservationPage";
import { EvalPage } from "./pages/EvalPage";
import { CalibrationPage } from "./pages/CalibrationPage";
import { HomePage } from "./pages/HomePage";

const queryClient = new QueryClient({ defaultOptions: { queries: { staleTime: 5_000, refetchOnWindowFocus: true } } });

export function App() {
  return <QueryClientProvider client={queryClient}><BrowserRouter><Routes>
    <Route path="/studio-preview" element={<StudioPreview />} />
    <Route element={<AppShell />}>
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
  </Routes></BrowserRouter></QueryClientProvider>;
}

function HomeEntry() {
  // Saved account/series/operation URLs keep their existing workspace behavior.
  return useLocation().search ? <WorkspacePage /> : <HomePage />;
}

function AppShell() {
  const location = useLocation();
  // Keep the same Layout/Agent provider between workspace and legacy deep links.
  return location.pathname === "/" && !location.search ? <Outlet /> : <Layout />;
}
