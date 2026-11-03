import { useQuery } from "@tanstack/react-query";
import { studioApi } from "./client";
import type { CreatorDirectoryItem } from "./types";

const emptyDirectory: CreatorDirectoryItem[] = [];

// The switcher reads a compact, single-response snapshot. Detailed account
// information continues to come from the account-specific endpoint.
export function useCreatorDirectory() {
  const directory = useQuery({
    queryKey: ["creators", "directory"],
    queryFn: ({ signal }) => studioApi.creatorDirectory(signal),
    staleTime: 5_000,
    retry: false,
  });
  return { ...directory, items: directory.data?.items ?? emptyDirectory };
}
