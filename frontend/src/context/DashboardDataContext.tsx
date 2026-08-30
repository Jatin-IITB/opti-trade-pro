import { createContext, useContext } from "react";
import type { ReactNode } from "react";
import { useLiveData } from "../hooks/useLiveData";
import type { LiveDataState } from "../hooks/useLiveData";

const DashboardDataContext = createContext<LiveDataState | null>(null);

export function DashboardDataProvider({ children }: { children: ReactNode }) {
  const state = useLiveData();
  return (
    <DashboardDataContext.Provider value={state}>
      {children}
    </DashboardDataContext.Provider>
  );
}

export function useDashboardData(): LiveDataState {
  const ctx = useContext(DashboardDataContext);
  if (!ctx) {
    throw new Error(
      "useDashboardData must be used within a DashboardDataProvider",
    );
  }
  return ctx;
}
