import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { Layout } from "./components/Layout";
import { WorkspacePage, WorkspaceRedirect } from "./pages/WorkspacePage";
import { SkillsPage } from "./pages/SkillsPage";
import { RunDetailPage } from "./pages/RunsPage";
import { AgentPage } from "./pages/AgentPage";
import { StudioPreview } from "./pages/StudioPreview";

const queryClient = new QueryClient({ defaultOptions: { queries: { staleTime: 5_000, refetchOnWindowFocus: true } } });

export function App() {
  return <QueryClientProvider client={queryClient}><BrowserRouter><Routes><Route path="/studio-preview" element={<StudioPreview />} /><Route element={<Layout />}><Route path="/" element={<WorkspacePage />} /><Route path="/skills" element={<SkillsPage />} /><Route path="/studio" element={<Navigate to="/skills" replace />} /><Route path="/series/:seriesId" element={<WorkspaceRedirect />} /><Route path="/runs/:runId" element={<RunDetailPage />} /><Route path="/agent" element={<AgentPage />} /><Route path="*" element={<Navigate to="/" replace />} /></Route></Routes></BrowserRouter></QueryClientProvider>;
}
