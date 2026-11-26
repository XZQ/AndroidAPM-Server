import { useCallback, useEffect, useRef, useState } from "react";
import { useOutletContext } from "react-router-dom";
import type { ConsoleContextValue } from "./context";
import { useRequestGuard } from "./useRequestGuard";

/** Discard stale reads and abort superseded GETs without cancelling audited writes. */
export function useProductRead<T>(read: (signal?: AbortSignal) => Promise<T>) {
  const { handleFailure } = useOutletContext<ConsoleContextValue>();
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const controllerRef = useRef<AbortController | null>(null);
  const begin = useRequestGuard();
  const beginAction = useRequestGuard();
  const reload = useCallback(async () => {
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    const current = begin();
    setError(null);
    try {
      const result = await read(controller.signal);
      if (current()) setData(result);
    } catch (caught) {
      if (controller.signal.aborted) return;
      if (current()) {
        setData(null);
        setError(handleFailure(caught, "查询失败"));
      }
    }
  }, [begin, handleFailure, read]);
  useEffect(() => {
    void reload();
    return () => { controllerRef.current?.abort(); };
  }, [reload]);
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
