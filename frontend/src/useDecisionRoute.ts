import { useEffect, useState } from "react";
import { useViewState } from "./ViewState";
import type { DecisionView } from "./RaceDecisionDetail";
export function useDecisionRoute(scope: "weekly" | "past") {
  const [reference, setReference] = useViewState<string | null>(`${scope}Detail`, null);
  const [detail, setDetail] = useState<DecisionView | null>(null);
  const [state, setState] = useState<"idle" | "loading" | "error">("idle");
  const ids = typeof reference === "string" ? reference.split(",").map(Number) : [];
  const horseNumber = ids[3];
  useEffect(() => {
    setDetail(null);
    if (reference === null) { setState("idle"); return; }
    const parts = typeof reference === "string" ? reference.split(",").map(Number) : [];
    if ((parts.length !== 3 && parts.length !== 4) || parts.some((id) => !Number.isSafeInteger(id) || id <= 0)) {
      setState("error"); return;
    }
    const [raceId, snapshotId, judgementId, horse] = parts;
    const controller = new AbortController();
    setState("loading");
    void (async () => {
      try {
        const response = await fetch(`/api/races/${raceId}/weekly-decision-view?snapshot_id=${snapshotId}&judgement_id=${judgementId}`, { signal: controller.signal });
        if (!response.ok) throw new Error();
        const loaded = await response.json() as DecisionView;
        if (loaded.race_id !== raceId || loaded.snapshot_id !== snapshotId || loaded.judgement_id !== judgementId
          || (horse !== undefined && !loaded.runners.some((runner) => runner.horse_number === horse))) throw new Error();
        if (controller.signal.aborted) return;
        setDetail(loaded); setState("idle");
      } catch { if (!controller.signal.aborted) setState("error"); }
    })();
    return () => controller.abort();
  }, [reference]);
  return { detail, state, horseNumber, setReference };
}
