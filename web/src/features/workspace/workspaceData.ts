import type { QueryClient } from "@tanstack/react-query";

// Account and series writes affect both the rail and overview. Keep their
// shared refresh contract here so feature owners do not maintain separate lists.
export async function refreshWorkspace(client: QueryClient) {
  await Promise.all(["series-all", "creators", "overview"].map(key => client.invalidateQueries({ queryKey: [key] })));
}
