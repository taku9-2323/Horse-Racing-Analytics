export type RuleCondition = {
  field: "market_rank" | "win_odds";
  operator: string | null;
  threshold: number | null;
  state: "satisfied" | "failed" | "unknown";
  observed_value: number | null;
};

const fieldLabels = { market_rank: "市場順位", win_odds: "単勝オッズ" } as const;
const stateLabels = { satisfied: "達成", failed: "未達", unknown: "判定状態不明" } as const;

function observedLabel(condition: RuleCondition): string {
  if (condition.observed_value === null) return "実測値不明";
  return condition.field === "market_rank"
    ? `実測値${condition.observed_value}位`
    : `実測値${condition.observed_value.toFixed(1)}`;
}

function thresholdLabel(condition: RuleCondition): string {
  if (condition.threshold === null || condition.operator !== "lte") return "条件不明";
  return condition.field === "market_rank"
    ? `条件${condition.threshold}位以内`
    : `条件${condition.threshold.toFixed(1)}以下`;
}

export default function RuleConditionList({ conditions }: { conditions: RuleCondition[] }) {
  if (conditions.length === 0) return null;
  return <ul className="rule-condition-list" aria-label="判定条件">
    {conditions.map((condition) => <li key={condition.field}>
      {fieldLabels[condition.field]}: {stateLabels[condition.state]}（{observedLabel(condition)} / {thresholdLabel(condition)}）
    </li>)}
  </ul>;
}
