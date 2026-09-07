import type { Dispatch, SetStateAction } from "react";

import type { QueryFilters, WebSession } from "../types";

export interface ConsoleContextValue {
  session: WebSession;
  filters: QueryFilters;
  setFilters: Dispatch<SetStateAction<QueryFilters>>;
  dark: boolean;
  setDark: Dispatch<SetStateAction<boolean>>;
  handleFailure: (caught: unknown, fallback: string) => string;
  logout: () => Promise<void>;
}

export function defaultFilters(): QueryFilters {
  const toMs = Date.now();
  return {
    newRelease: "2.0.0",
    baselineRelease: "1.0.0",
    fromMs: toMs - 24 * 60 * 60 * 1_000,
    toMs,
  };
}

