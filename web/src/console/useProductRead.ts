import { useCallback, useEffect, useState } from "react";
import { useOutletContext } from "react-router-dom";
import type { ConsoleContextValue } from "./context";
import { useRequestGuard } from "./useRequestGuard";

/** Discard stale reads and action results when the route, scope or filters are replaced. */
export function useProductRead<T>(read: () => Promise<T>) {
  const { handleFailure } = useOutletContext<ConsoleContextValue>();
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const begin = useRequestGuard();
  const beginAction = useRequestGuard();
  const reload = useCallback(async () => {
    const current = begin();
    setData(null); setError(null);
    try { const result = await read(); if (current()) setData(result); }
    catch (caught) { if (current()) setError(handleFailure(caught, "查询失败")); }
  }, [begin, handleFailure, read]);
  useEffect(() => { void reload(); }, [reload]);
  async function run<R>(work: () => Promise<R>, success?: (result: R) => void) {
    if (busy) return;
    const current = beginAction();
    setBusy(true); setActionError(null);
    try {
      const result = await work();
      if (current()) { success?.(result); await reload(); }
    } catch (caught) { if (current()) setActionError(handleFailure(caught, "操作失败")); }
    finally { if (current()) setBusy(false); }
  }
  return { data, error, actionError, busy, reload, run };
}
