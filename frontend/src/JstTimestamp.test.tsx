import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import JstTimestamp, { formatJst } from "./JstTimestamp";
afterEach(cleanup);
it("formats the same instant consistently in Tokyo across midnight", () => {
  expect(formatJst("2026-09-05T18:30:00Z")).toBe("2026/09/06 03:30 JST");
  expect(formatJst("2026-09-06T03:30:00+09:00")).toBe("2026/09/06 03:30 JST");
});
it("preserves unknown observations and rejects invalid or offset-free input", () => {
  expect(formatJst(null, "観測時刻不明")).toBe("観測時刻不明");
  expect(formatJst("invalid")).toBe("時刻不正");
  expect(formatJst("2026-09-05T18:30:00")).toBe("時刻不正");
});
it("exposes original timestamps without altering displayed meaning", () => {
  render(<JstTimestamp value="2026-09-05T18:30:00Z" />);
  expect(screen.getByText("2026/09/06 03:30 JST").title).toBe("2026-09-05T18:30:00Z");
});
