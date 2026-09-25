import { useParams } from "react-router-dom";
import { useRun } from "../api/hooks";
import { RunInspector } from "../components/RunInspector";
import { ErrorState, LoadingState } from "../components/PageState";

export function RunDetailPage() {
  const { runId } = useParams();
  const query = useRun(runId);
  if (query.isPending) return <LoadingState />;
  if (query.isError) return <ErrorState message={query.error.message} onRetry={() => void query.refetch()} />;
  const run = query.data;
  return <RunInspector key={run.id} run={run} />;
}
