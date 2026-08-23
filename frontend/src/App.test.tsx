import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import App from "./App";

const healthResponse = (databaseStatus: "ok" | "error", status = 200) =>
  new Response(
    JSON.stringify({
      service: "Horse Racing Analytics",
      status: databaseStatus === "ok" ? "ok" : "degraded",
      api: { status: "ok" },
      database: { status: databaseStatus, engine: "sqlite" },
    }),
    {
      status,
      headers: { "Content-Type": "application/json" },
    },
  );

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("system status", () => {
  it("shows the API and SQLite as running when the health endpoint is ready", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(healthResponse("ok")));

    render(<App />);

    const apiCard = await screen.findByRole("article", { name: "APIの状態" });
    const databaseCard = screen.getByRole("article", { name: "データベースの状態" });
    expect(within(apiCard).getByText("稼働中")).toBeTruthy();
    expect(within(databaseCard).getByText("稼働中")).toBeTruthy();
    expect(within(databaseCard).getByText("SQLiteへ接続できています。")).toBeTruthy();
  });

  it("shows that only SQLite is stopped when the API returns degraded health", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(healthResponse("error", 503)));

    render(<App />);

    const apiCard = await screen.findByRole("article", { name: "APIの状態" });
    const databaseCard = screen.getByRole("article", { name: "データベースの状態" });
    expect(within(apiCard).getByText("稼働中")).toBeTruthy();
    expect(within(databaseCard).getByText("停止中")).toBeTruthy();
    expect(within(databaseCard).getByText("SQLiteへ接続できません。")).toBeTruthy();
  });
});

