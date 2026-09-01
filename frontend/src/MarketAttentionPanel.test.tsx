import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import MarketAttentionPanel from "./MarketAttentionPanel";

afterEach(() => vi.restoreAllMocks());

describe("MarketAttentionPanel", () => {
  it("shows the latest ranking and reloads a selected saved snapshot", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const url = String(input);
      if (url.endsWith("/odds-snapshots")) return Promise.resolve(new Response(JSON.stringify([
        { id: 9, observed_at: "2026-08-30T04:50:00Z", received_at: "2026-08-30T04:51:00Z" },
        { id: 10, observed_at: "2026-08-30T05:00:00Z", received_at: "2026-08-30T05:01:00Z" },
      ]), { status: 200 }));
      const id = url.includes("snapshot_id=9") ? 9 : 10;
      return Promise.resolve(new Response(JSON.stringify({
        snapshot_id: id, observed_at: id === 9 ? "2026-08-30T04:50:00Z" : "2026-08-30T05:00:00Z",
        received_at: "2026-08-30T05:01:00Z", status: "available", unavailable_reason: null,
        label: "市場評価による順位", disclaimer: "購入推奨ではありません。",
        runners: [{ rank: 1, horse_number: id === 9 ? 2 : 1, horse_name: "テスト馬", win_odds: 2.5, normalized_win_market_share: 0.4 }],
      }), { status: 200 }));
    });

    render(<MarketAttentionPanel raceId={3} />);
    fireEvent.click(screen.getByRole("button", { name: "市場注目順位を表示" }));
    expect(await screen.findByText("1 テスト馬")).toBeTruthy();
    expect(screen.getByText("購入推奨ではありません。")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("対象オッズ時点"), { target: { value: "9" } });
    fireEvent.click(screen.getByRole("button", { name: "選択時点を表示" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/races/3/market-attention?snapshot_id=9"));
    expect(screen.getByRole("option", { name: /#9 観測/ })).toBeTruthy();
  });

  it("explains when no snapshot is saved", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify([]), { status: 200 }));
    render(<MarketAttentionPanel raceId={4} />);
    fireEvent.click(screen.getByRole("button", { name: "市場注目順位を表示" }));
    await waitFor(() => expect(screen.getByText("保存済みのオッズ時点がないため順位を表示できません。")).toBeTruthy());
  });
});
