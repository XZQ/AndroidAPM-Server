import type { WebSession } from "./types";

/** Administration includes investigation; aggregate viewers never inherit either role. */
export function canInvestigate(role: WebSession["role"]): boolean {
  return role === "investigator" || role === "admin";
}
