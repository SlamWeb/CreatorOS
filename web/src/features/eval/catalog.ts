import { request } from "../../api/client";

export interface CatalogCase {
  instance_id: string;
  dataset_id: string;
  case_id: string;
  title: string;
  family: string | null;
  parent_instance_id: string | null;
  dependencies: string[];
  side_effects: string[];
  execution_readiness: "enabled" | "requires_review";
  resource_class: "routine" | "heavy" | "unknown";
  reason: string;
}

export interface CatalogSuite {
  id: string;
  version: string;
  title: string;
  description: string;
  cases: CatalogCase[];
  candidates?: CatalogCase[];
  scope_status?: "partial" | "complete";
  historical?: boolean;
}

export interface EvalCatalog { schema_version: string; suites: CatalogSuite[]; historical_error?: string | null }

export interface CatalogSuiteRun {
  round_id: string;
  round_title: string;
  run_id: string;
  suite_version: string;
  selection_kind: string;
  status: string;
  display_status: string;
  created_at: string;
  planned_count: number;
  selected_count: number;
  membership_sha256: string;
}

export async function loadCatalog(signal?: AbortSignal): Promise<EvalCatalog> {
  const result = await request<EvalCatalog>("/api/eval/v1/suites", { signal });
  if (!result || !Array.isArray(result.suites) || result.suites.some(suite => !suite || typeof suite.id !== "string" || !Array.isArray(suite.cases))) {
    throw new Error("评测集目录格式不完整，请稍后重试。");
  }
  return result;
}

export async function loadSuiteRuns(suiteId: string, signal?: AbortSignal): Promise<CatalogSuiteRun[]> {
  const result = await request<{ items: CatalogSuiteRun[] }>(`/api/eval/v1/suites/${encodeURIComponent(suiteId)}/runs`, { signal });
  if (!result || !Array.isArray(result.items)) throw new Error("运行记录格式不完整，请稍后重试。");
  return result.items;
}
