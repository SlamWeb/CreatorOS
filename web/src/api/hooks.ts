import { useQuery } from "@tanstack/react-query";
import { studioApi } from "./client";

const queryOptions = { staleTime: 5_000, retry: 1 };
const activeRunStatuses = ["queued", "producing", "validating", "running"];

export const useHealth = () => useQuery({
  queryKey: ["health"], queryFn: studioApi.health, staleTime: 30_000, retry: false,
});
export const useOverview = (enabled = true) => useQuery({
  queryKey: ["overview"], queryFn: studioApi.overview,
  enabled,
  refetchInterval: (query) => query.state.data?.counts.producing_count ? 2_000 : false,
  ...queryOptions,
});
export const useCreators = () => useQuery({ queryKey: ["creators"], queryFn: studioApi.creators, ...queryOptions });
export const useCreator = (id: string | null | undefined) => useQuery({
  queryKey: ["creator", id], queryFn: () => studioApi.creator(id ?? ""), enabled: Boolean(id), ...queryOptions,
});
export const useSeries = (id: string | undefined) => useQuery({
  queryKey: ["series", id], queryFn: () => studioApi.series(id ?? ""), enabled: Boolean(id),
  refetchInterval: (query) => activeRunStatuses.includes(query.state.data?.latest_run_status ?? "") ? 2_000 : false,
  ...queryOptions,
});
export const useTopics = (id: string | undefined) => useQuery({
  queryKey: ["topics", id], queryFn: () => studioApi.topics(id ?? ""), enabled: Boolean(id),
  refetchInterval: (query) => query.state.data?.items.some((topic) => activeRunStatuses.includes(topic.existing_run_status ?? "")) ? 2_000 : false,
  ...queryOptions,
});
export const useRuns = () => useQuery({
  queryKey: ["runs"], queryFn: studioApi.runs,
  refetchInterval: (query) => query.state.data?.items.some((run) => activeRunStatuses.includes(run.status)) ? 2_000 : false,
  ...queryOptions,
});
export const useRun = (id: string | undefined) => useQuery({
  queryKey: ["run", id], queryFn: () => studioApi.run(id ?? ""), enabled: Boolean(id),
  refetchInterval: (query) => activeRunStatuses.includes(query.state.data?.status ?? "") ? 2_000 : false,
  ...queryOptions,
});
export const useOperation = (id: string | null) => useQuery({
  queryKey: ["operation", id], queryFn: () => studioApi.operation(id ?? ""),
  enabled: Boolean(id), ...queryOptions,
});
