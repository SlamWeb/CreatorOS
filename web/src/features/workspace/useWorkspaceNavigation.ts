import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { useLocation, useSearchParams } from "react-router-dom";
import type { CreatorDirectoryItem, SeriesView } from "../../api/types";
import { useAgentChat } from "../agent";
import { useWorkInbox } from "../../components/TaskInbox";
import { removeDeletedAccountParams, removeDeletedSeriesParams, resolveWorkspaceSelection, selectAccountParams, selectSeriesParams } from "./navigation";

const noAccounts: CreatorDirectoryItem[] = [];
const noSeries: SeriesView[] = [];

// This module owns the URL-to-workspace projection and all navigation effects.
// Business writes call the completion methods only after the server accepts them.
export function useWorkspaceNavigation(accountData?: CreatorDirectoryItem[], seriesData?: SeriesView[], accountVerified = true) {
  const [params, setParams] = useSearchParams();
  const location = useLocation();
  const routePath = `${location.pathname}${location.search}`;
  const [treeOpen, setTreeOpen] = useState(false);
  const [confirmingAccount, setConfirmingAccount] = useState<string | null>(null);
  const inbox = useWorkInbox();
  const chat = useAgentChat();
  const visitedSeries = useRef<string | null>(null);
  const paramsRef = useRef(params);
  const mounted = useRef(true);
  paramsRef.current = params;
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);

  const accounts = accountData ?? noAccounts;
  const allSeries = seriesData ?? noSeries;
  const resolved = resolveWorkspaceSelection(params, accounts, allSeries);
  const selection = accountVerified ? resolved : { ...resolved, account: undefined, accountSeries: noSeries };

  useEffect(() => {
    const id = params.get("series");
    if (!id) { visitedSeries.current = null; return; }
    const selected = seriesData?.find(item => item.id === id);
    if (!selected || !inbox.data || (params.get("creator") && params.get("creator") !== selected.creator_id)
        || visitedSeries.current === id) return;
    visitedSeries.current = id;
    inbox.markObjectSeen("series", id);
  }, [params, seriesData, inbox.data]);
  useLayoutEffect(() => {
    if (!accountData || !seriesData || !accountVerified) { chat.clearDefault(routePath); return; }
    const { account, scopedCreatorId } = resolveWorkspaceSelection(params, accountData, seriesData);
    if (!account) {
      if (!scopedCreatorId && !params.has("creator")) chat.setDefault({ creatorId: null }, routePath);
      else chat.clearDefault(routePath);
      return;
    }
    if (params.has("skill_edit")) chat.open({ creatorId: account.id, accountName: account.display_name });
    else chat.setDefault({ creatorId: account.id, accountName: account.display_name }, routePath);
  }, [params, accountData, seriesData, accountVerified, chat.isOpen, chat.open, chat.setDefault, chat.clearDefault, routePath]);

  function selectSeries(id: string, owner?: string | null, keepFilter = false) {
    setTreeOpen(false);
    inbox.markObjectSeen("series", id);
    const target = allSeries.find(item => item.id === id);
    const creatorId = owner === undefined ? target?.creator_id : owner;
    const creator = accounts.find(item => item.id === creatorId);
    if (chat.isOpen && creator) chat.open({ creatorId: creator.id, accountName: creator.display_name });
    setParams(previous => selectSeriesParams(previous, id, creatorId, keepFilter));
  }

  function selectAccount(id: string, resetView = false) {
    setTreeOpen(false);
    setConfirmingAccount(null);
    const account = accounts.find(item => item.id === id);
    if (chat.isOpen && account) chat.open({ creatorId: account.id, accountName: account.display_name });
    setParams(previous => selectAccountParams(previous, id, resetView));
  }

  function completeAssignment(id: string, owner: string | null) {
    // A response can arrive after the user has left this column. Refresh its
    // server data, but never take over the newer location or chat scope.
    if (mounted.current && paramsRef.current.get("series") === id) selectSeries(id, owner);
  }
  function accountDeleted(id: string) {
    setConfirmingAccount(null);
    if (mounted.current) setParams(previous => removeDeletedAccountParams(previous, id));
  }
  function seriesDeleted(id: string) {
    if (mounted.current) setParams(previous => removeDeletedSeriesParams(previous, id));
  }

  return { params, setParams, ...selection, treeOpen, setTreeOpen, confirmingAccount, setConfirmingAccount,
    selectAccount, selectSeries, completeAssignment, accountDeleted, seriesDeleted };
}
