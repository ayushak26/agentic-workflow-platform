import { createContext, useContext } from "react";
import type { RunCostSummary } from "./api/types";

export const RunCostContext = createContext<(summary: RunCostSummary | null) => void>(() => {});
export const useSetRunCost = () => useContext(RunCostContext);
