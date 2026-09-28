import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import CsvImportGuide from "./CsvImportGuide";
import raceSample from "virtual:sample-race";
import resultSample from "virtual:sample-results";
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
it.each(["race", "results"] as const)("offers the canonical %s sample without registration", (kind) => {
  const fetchMock = vi.fn(); vi.stubGlobal("fetch", fetchMock);
  render(<CsvImportGuide kind={kind} />);
  const link = screen.getByRole("link") as HTMLAnchorElement;
  const sample = kind === "race" ? raceSample : resultSample;
  expect(decodeURIComponent(link.href.split(",").slice(1).join(","))).toBe(sample);
  expect(link.download).toBe(kind === "race" ? "sample-race.csv" : "sample-results.csv");
  expect(screen.getByText(sample.split(/\r?\n/)[0])).toBeTruthy();
  expect(screen.getByText(/UTF-8で保存/)).toBeTruthy();
  expect(fetchMock).not.toHaveBeenCalled();
});
