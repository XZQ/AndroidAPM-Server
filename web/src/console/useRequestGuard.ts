import { useCallback, useEffect, useRef } from "react";

/** Fence late responses on retries and unmounts without cancelling audited server writes. */
export function useRequestGuard() {
  const generation = useRef(0);
  useEffect(() => () => { generation.current += 1; }, []);
  return useCallback(() => {
    const request = ++generation.current;
    return () => generation.current === request;
  }, []);
}
