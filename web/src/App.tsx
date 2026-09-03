import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { Layout } from "./components/Layout";
import { CreatorDetailPage } from "./pages/CreatorDetailPage";
import { CreatorsPage } from "./pages/CreatorsPage";
import { SeriesPage } from "./pages/SeriesPage";
import { TodayPage } from "./pages/TodayPage";
import { RunDetailPage, RunsPage } from "./pages/RunsPage";

const queryClient = new QueryClient({ defaultOptions: { queries: { staleTime: 5_000, refetchOnWindowFocus: true } } });

export function App() {
  return <QueryClientProvider client={queryClient}><BrowserRouter><Routes><Route element={<Layout />}><Route path="/" element={<TodayPage />} /><Route path="/creators" element={<CreatorsPage />} /><Route path="/creators/:creatorId" element={<CreatorDetailPage />} /><Route path="/series/:seriesId" element={<SeriesPage />} /><Route path="/runs" element={<RunsPage />} /><Route path="/runs/:runId" element={<RunDetailPage />} /><Route path="*" element={<Navigate to="/" replace />} /></Route></Routes></BrowserRouter></QueryClientProvider>;
}
