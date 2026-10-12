export type SnapshotOddsValue = number | string | null;

export type SnapshotRunner = {
  horse_number: number;
  win_odds?: SnapshotOddsValue;
  place_odds_min?: SnapshotOddsValue;
  place_odds_max?: SnapshotOddsValue;
};

export type OddsSnapshot = {
  id: number;
  race_id: number;
  observed_at: string | null;
  received_at: string;
  source_updated_at?: string | null;
  source: string;
  runners: SnapshotRunner[];
};

export type ComparisonMetric = {
  key: "win_odds" | "place_odds_min" | "place_odds_max";
  label: string;
  previousValue: SnapshotOddsValue | undefined;
  selectedValue: SnapshotOddsValue | undefined;
  difference: number | null;
  percentChange: number | null;
  unavailableReason: string | null;
};

export type ComparisonRow = {
  horseNumber: number;
  runnerState: "matched" | "selected_only" | "previous_only";
  metrics: ComparisonMetric[];
};

export type SnapshotComparison = {
  selected: OddsSnapshot;
  previous: OddsSnapshot | null;
  timeComparable: boolean;
  reason: string | null;
  intervalMilliseconds: number | null;
  rows: ComparisonRow[];
};

const METRICS = [
  { key: "win_odds", label: "単勝" },
  { key: "place_odds_min", label: "複勝下限" },
  { key: "place_odds_max", label: "複勝上限" },
] as const;

const OFFSET_TIMESTAMP = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?(Z|([+-])(\d{2}):(\d{2}))$/i;
const DECIMAL_VALUE = /^[+-]?(?:\d+\.?\d*|\.\d+)(?:e[+-]?\d+)?$/i;

export function parseStrictTimestamp(value: string | null): number | null {
  if (value === null) return null;
  const parts = OFFSET_TIMESTAMP.exec(value);
  if (parts === null) return null;
  const year = Number(parts[1]);
  const month = Number(parts[2]);
  const day = Number(parts[3]);
  const hour = Number(parts[4]);
  const minute = Number(parts[5]);
  const second = Number(parts[6] ?? "0");
  if (month < 1 || month > 12 || day < 1 || hour > 23 || minute > 59 || second > 59) return null;
  const localDate = new Date(0);
  localDate.setUTCFullYear(year, month - 1, day);
  localDate.setUTCHours(hour, minute, second, 0);
  if (localDate.getUTCFullYear() !== year || localDate.getUTCMonth() !== month - 1
    || localDate.getUTCDate() !== day) return null;
  if (parts[9] !== undefined && (Number(parts[9]) > 23 || Number(parts[10]) > 59)) return null;
  const timestamp = Date.parse(value);
  return Number.isFinite(timestamp) ? timestamp : null;
}

function comparisonGate(
  previous: OddsSnapshot | null,
  selected: OddsSnapshot,
): { timeComparable: boolean; reason: string | null; intervalMilliseconds: number | null } {
  if (previous === null) {
    return {
      timeComparable: false,
      reason: "直前の保存済み時点がありません。",
      intervalMilliseconds: null,
    };
  }
  if (previous.observed_at === null || selected.observed_at === null) {
    return {
      timeComparable: false,
      reason: "観測時刻が不明なため時系列比較できません。",
      intervalMilliseconds: null,
    };
  }
  const previousTime = parseStrictTimestamp(previous.observed_at);
  const selectedTime = parseStrictTimestamp(selected.observed_at);
  if (previousTime === null || selectedTime === null) {
    return {
      timeComparable: false,
      reason: "観測時刻を解釈できないため時系列比較できません。",
      intervalMilliseconds: null,
    };
  }
  if (previousTime === selectedTime) {
    return {
      timeComparable: false,
      reason: "観測時刻が同じため時系列比較できません。",
      intervalMilliseconds: null,
    };
  }
  if (previousTime > selectedTime) {
    return {
      timeComparable: false,
      reason: "観測時刻が保存ID順と逆転しているため時系列比較できません。",
      intervalMilliseconds: null,
    };
  }
  return {
    timeComparable: true,
    reason: null,
    intervalMilliseconds: selectedTime - previousTime,
  };
}

function positiveFiniteValue(value: SnapshotOddsValue | undefined): {
  number: number | null;
  reason: string | null;
} {
  if (value === null || value === undefined) {
    return { number: null, reason: "値が欠損しているため比較できません。" };
  }
  let number: number;
  if (typeof value === "number") {
    number = value;
  } else if (DECIMAL_VALUE.test(value.trim())) {
    number = Number(value);
  } else {
    return { number: null, reason: "数値として読み取れないため比較できません。" };
  }
  if (!Number.isFinite(number) || number <= 0) {
    return { number: null, reason: "正の有限値ではないため比較できません。" };
  }
  return { number, reason: null };
}

function metricResult(
  previousValue: SnapshotOddsValue | undefined,
  selectedValue: SnapshotOddsValue | undefined,
): Omit<ComparisonMetric, "key" | "label"> {
  const previous = positiveFiniteValue(previousValue);
  const selected = positiveFiniteValue(selectedValue);
  if (previous.number === null || selected.number === null) {
    return {
      previousValue,
      selectedValue,
      difference: null,
      percentChange: null,
      unavailableReason: previous.reason ?? selected.reason,
    };
  }
  const difference = selected.number - previous.number;
  if (!Number.isFinite(difference)) {
    return {
      previousValue,
      selectedValue,
      difference: null,
      percentChange: null,
      unavailableReason: "差を有限値で表せないため比較できません。",
    };
  }
  const percentChange = (difference / previous.number) * 100;
  return {
    previousValue,
    selectedValue,
    difference,
    percentChange: Number.isFinite(percentChange) ? percentChange : null,
    unavailableReason: Number.isFinite(percentChange)
      ? null
      : "前値比を有限値で表せないため割合を表示できません。",
  };
}

export function buildSnapshotComparison(
  snapshots: OddsSnapshot[],
  selectedSnapshotId: number,
): SnapshotComparison | null {
  const selectedInHistory = snapshots.find((snapshot) => snapshot.id === selectedSnapshotId);
  if (!selectedInHistory) return null;

  const ordered = snapshots
    .filter((snapshot) => snapshot.race_id === selectedInHistory.race_id)
    .slice()
    .sort((left, right) => left.id - right.id);
  const selectedIndex = ordered.findIndex((snapshot) => snapshot.id === selectedSnapshotId);
  if (selectedIndex < 0) return null;

  const selected = ordered[selectedIndex];
  const previous = selectedIndex === 0 ? null : ordered[selectedIndex - 1];
  const gate = comparisonGate(previous, selected);
  const previousByHorse = new Map(
    (previous?.runners ?? []).map((runner) => [runner.horse_number, runner]),
  );
  const selectedByHorse = new Map(
    selected.runners.map((runner) => [runner.horse_number, runner]),
  );
  const horseNumbers = [...new Set([...previousByHorse.keys(), ...selectedByHorse.keys()])]
    .sort((left, right) => left - right);

  const rows = horseNumbers.map((horseNumber): ComparisonRow => {
    const previousRunner = previousByHorse.get(horseNumber);
    const selectedRunner = selectedByHorse.get(horseNumber);
    const runnerState = previousRunner === undefined
      ? "selected_only"
      : selectedRunner === undefined
        ? "previous_only"
        : "matched";
    const metrics = METRICS.map(({ key, label }): ComparisonMetric => {
      const previousValue = previousRunner?.[key];
      const selectedValue = selectedRunner?.[key];
      if (!gate.timeComparable) {
        return {
          key, label, previousValue, selectedValue,
          difference: null, percentChange: null, unavailableReason: null,
        };
      }
      if (previousRunner === undefined || selectedRunner === undefined) {
        return {
          key, label, previousValue, selectedValue,
          difference: null, percentChange: null,
          unavailableReason: previousRunner === undefined
            ? "前時点にこの馬番がありません。"
            : "選択時点にこの馬番がありません。",
        };
      }
      return { key, label, ...metricResult(previousValue, selectedValue) };
    });
    return { horseNumber, runnerState, metrics };
  });

  return {
    selected,
    previous,
    timeComparable: gate.timeComparable,
    reason: gate.reason,
    intervalMilliseconds: gate.intervalMilliseconds,
    rows,
  };
}

export function getSnapshotAge(
  observedAt: string | null,
  asOfMilliseconds: number,
): { elapsedMilliseconds: number | null; reason: string | null } {
  if (observedAt === null) {
    return { elapsedMilliseconds: null, reason: "観測時刻不明のため経過を表示できません。" };
  }
  const observationTime = parseStrictTimestamp(observedAt);
  if (observationTime === null) {
    return { elapsedMilliseconds: null, reason: "観測時刻を解釈できないため経過を表示できません。" };
  }
  if (!Number.isFinite(asOfMilliseconds)) {
    return { elapsedMilliseconds: null, reason: "表示時点が不明のため経過を表示できません。" };
  }
  if (observationTime > asOfMilliseconds) {
    return { elapsedMilliseconds: null, reason: "観測時刻が表示を開いた時点より未来です。" };
  }
  return { elapsedMilliseconds: asOfMilliseconds - observationTime, reason: null };
}
