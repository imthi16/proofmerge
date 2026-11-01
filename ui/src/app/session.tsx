import { createContext, useContext } from "react";
import type { Session } from "../api/types";

export const SessionContext = createContext<Session | null>(null);

export function useSession(): Session | null {
  return useContext(SessionContext);
}
