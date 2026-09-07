import { stateLabel } from "../format";
import type { QueryState } from "../types";

interface StateBadgeProps {
  state: QueryState;
}

export function StateBadge({ state }: StateBadgeProps) {
  return (
    <span className={`state-badge state-${state.toLowerCase()}`} data-state={state}>
      <span className="state-dot" aria-hidden="true" />
      {stateLabel(state)}
    </span>
  );
}
