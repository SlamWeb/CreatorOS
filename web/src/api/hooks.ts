import { useQuery } from "@tanstack/react-query";
import { studioApi } from "./client";

const queryOptions = { staleTime: 5_000, retry: 1 };

export const useOverview = () => useQuery({ queryKey: ["overview"], queryFn: studioApi.overview, ...queryOptions });
export const useCreators = () => useQuery({ queryKey: ["creators"], queryFn: studioApi.creators, ...queryOptions });
export const useCreator = (id: string | undefined) => useQuery({
  queryKey: ["creator", id], queryFn: () => studioApi.creator(id ?? ""), enabled: Boolean(id), ...queryOptions,
});
export const useSeries = (id: string | undefined) => useQuery({
  queryKey: ["series", id], queryFn: () => studioApi.series(id ?? ""), enabled: Boolean(id), ...queryOptions,
});
export const useTopics = (id: string | undefined) => useQuery({
  queryKey: ["topics", id], queryFn: () => studioApi.topics(id ?? ""), enabled: Boolean(id), ...queryOptions,
});
export const useRuns = () => useQuery({ queryKey: ["runs"], queryFn: studioApi.runs, ...queryOptions });
export const useRun = (id: string | undefined) => useQuery({
  queryKey: ["run", id], queryFn: () => studioApi.run(id ?? ""), enabled: Boolean(id), ...queryOptions,
});
