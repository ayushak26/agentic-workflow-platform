import { createContext, useContext } from "react";

export const RunCostContext = createContext<(usd: number) => void>(() => {});
export const useSetRunCost = () => useContext(RunCostContext);