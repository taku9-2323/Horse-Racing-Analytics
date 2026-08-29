import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import JraRaceAcquisitionPanel from "./JraRaceAcquisitionPanel";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it("acquires a JRA race card and shows provenance and runners", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
    card_id: 1,
    race: { racecourse: "架空", race_date: "2026-08-30", race_number: 7, start_time: "13:25", surface: "芝", distance_m: 1800, going: "良", field_size: 1 },
    runners: [{ gate: 1, horse_number: 1, horse_name: "アサヒノソラ", age: 3, sex: "牡", assigned_weight: 56, status: "出走" }],
    source: { url: "https://www.jra.go.jp/JRADB/accessD.html", received_at: "2026-08-29T04:00:00Z", source_updated_at: null, parser_version: "jra-race-entry/1", response_sha256: "a".repeat(64), validation_status: "valid" },
  }), { status: 201, headers: { "Content-Type": "application/json" } })));

  render(<JraRaceAcquisitionPanel />);
  fireEvent.change(screen.getByLabelText("JRAレースページURL"), { target: { value: "https://www.jra.go.jp/JRADB/accessD.html?CNAME=test" } });
  fireEvent.click(screen.getByRole("button", { name: "取得して登録" }));

  expect(await screen.findByRole("heading", { name: "架空 7R" })).toBeTruthy();
  expect(screen.getByText("アサヒノソラ")).toBeTruthy();
  expect(screen.getByText("検証済み / jra-race-entry/1")).toBeTruthy();
  expect(screen.getByText(/JRA更新 不明/)).toBeTruthy();
  expect(within(screen.getByRole("table")).getByText("出走")).toBeTruthy();
});

it("shows the CSV fallback when acquisition is stopped", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: {
    code: "acquisition_stopped", message: "JRAからの取得を停止しました。CSV取込を使用してください。",
  }}), { status: 503, headers: { "Content-Type": "application/json" } })));
  render(<JraRaceAcquisitionPanel />);
  fireEvent.change(screen.getByLabelText("JRAレースページURL"), { target: { value: "https://www.jra.go.jp/JRADB/accessD.html?CNAME=test" } });
  fireEvent.click(screen.getByRole("button", { name: "取得して登録" }));
  const alert = await screen.findByRole("alert");
  expect(within(alert).getByText("CSV取込は恒久的な代替手段です。")).toBeTruthy();
});
