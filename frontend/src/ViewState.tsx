import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode, type SetStateAction } from "react";

type Value = string | number | boolean | null;
type Values = Record<string, Value>;
const keys = new Set(["area", "importRace", "weeklyDate", "weeklyCourse", "weeklyFilter", "weeklyCompleted", "weeklyDetail", "pastPage", "pastDate", "pastCourse", "pastResult", "pastTiming", "pastDetail", "evaluation"]);
function read(): Values {
  try {
    const raw = JSON.parse(new URLSearchParams(window.location.search).get("view") ?? "{}");
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) return { routeError: true };
    const parsed: Values = {};
    const enums: Record<string, string[]> = {
      area: ["weekly", "past", "evaluation", "import", "settings"],
      weeklyFilter: ["all", "attention", "waiting"], pastResult: ["all", "first", "placed", "other", "withdrawn", "missing"], pastTiming: ["all", "pre", "post"],
    };
    for (const [key, value] of Object.entries(raw)) {
      if (!keys.has(key)) continue;
      const valid = key === "pastPage" || key === "importRace" ? (key === "importRace" && value === null) || (typeof value === "number" && Number.isSafeInteger(value) && value > 0)
        : key === "weeklyCompleted" ? typeof value === "boolean"
        : enums[key] ? typeof value === "string" && enums[key].includes(value)
        : (["weeklyDate", "weeklyDetail", "pastDetail", "evaluation"].includes(key) && value === null) || (typeof value === "string" && value.length < 8000);
      if (valid) parsed[key] = value as Value;
      else parsed.routeError = true;
    }
    return parsed;
  } catch { return { routeError: true }; }
}
const Context = createContext<{ values: Values; update: (key: string, value: SetStateAction<Value>, fallback: Value, replace: boolean) => void } | null>(null);
export function ViewStateProvider({ children }: { children: ReactNode }) {
  const [values, setValues] = useState(read);
  const latest = useRef(values);
  const pending = useRef<"push" | "replace" | null>(null);
  const update = useCallback((key: string, action: SetStateAction<Value>, fallback: Value, replace: boolean) => {
    const value = typeof action === "function" ? action(latest.current[key] ?? fallback) : action;
    if (latest.current[key] === value) return;
    latest.current = { ...latest.current, [key]: value };
    setValues(latest.current);
    if (pending.current) { if (!replace) pending.current = "push"; return; }
    pending.current = replace ? "replace" : "push";
    queueMicrotask(() => {
      const mode = pending.current;
      pending.current = null;
      if (!mode) return;
      const url = new URL(window.location.href);
      url.searchParams.set("view", JSON.stringify(latest.current));
      window.history[mode === "push" ? "pushState" : "replaceState"](null, "", url);
    });
  }, []);
  useEffect(() => {
    const restore = () => { pending.current = null; latest.current = read(); setValues(latest.current); };
    window.addEventListener("popstate", restore);
    return () => { pending.current = null; window.removeEventListener("popstate", restore); };
  }, []);
  return <Context.Provider value={{ values, update }}>{children}</Context.Provider>;
}
export function useViewState<T extends Value>(key: string, initial: T): [T, (action: SetStateAction<T>, replace?: boolean) => void] {
  const context = useContext(Context);
  const [local, setLocal] = useState(initial);
  const value = context && key in context.values ? context.values[key] as T : local;
  const update = useCallback((action: SetStateAction<T>, replace = false) => {
    if (context) context.update(key, action as SetStateAction<Value>, initial, replace);
    else setLocal(action);
  }, [context?.update, key, initial]);
  return [value, update];
}
