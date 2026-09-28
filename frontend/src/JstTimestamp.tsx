const formatter = new Intl.DateTimeFormat("ja-JP", {
  timeZone: "Asia/Tokyo", year: "numeric", month: "2-digit", day: "2-digit",
  hour: "2-digit", minute: "2-digit", hourCycle: "h23",
});

export function formatJst(value: string | null | undefined, unknown = "時刻不明"): string {
  if (!value) return unknown;
  // Offset-free values are ambiguous; never interpret them in the browser timezone.
  if (!/(Z|[+-]\d{2}:\d{2})$/i.test(value)) return "時刻不正";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "時刻不正" : `${formatter.format(date)} JST`;
}

export default function JstTimestamp({ value, unknown }: { value: string | null | undefined; unknown?: string }) {
  return <span title={value ?? undefined}>{formatJst(value, unknown)}</span>;
}
