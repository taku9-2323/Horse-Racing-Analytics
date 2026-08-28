import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import DataMaintenancePanel from "./DataMaintenancePanel";


const jsonResponse = (value: object, status = 200) => new Response(JSON.stringify(value), {
  status, headers: { "Content-Type": "application/json" },
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("creates, verifies, and restores a backup while keeping exports downloadable", async () => {
  const original = {
    id: "manual-20260828T120000000000Z-aaaaaaaa", created_at: "2026-08-28T12:00:00Z",
    kind: "manual", size_bytes: 118784, verified: true,
  };
  const created = {
    ...original, id: "manual-20260828T123456000000Z-bbbbbbbb", created_at: "2026-08-28T12:34:56Z",
  };
  const safety = {
    ...original, id: "pre-restore-20260828T123500000000Z-cccccccc", kind: "pre_restore",
    created_at: "2026-08-28T12:35:00Z",
  };
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url === "/api/data/backups" && !init?.method) return jsonResponse([original]);
    if (url === "/api/data/backups" && init?.method === "POST") return jsonResponse(created, 201);
    if (url.endsWith("/verify")) return jsonResponse(created);
    if (url.endsWith("/restore")) return jsonResponse({ restored_backup_id: created.id, safety_backup: safety });
    throw new Error(`unexpected request: ${url}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  vi.stubGlobal("confirm", vi.fn(() => true));

  render(<DataMaintenancePanel />);

  fireEvent.click(screen.getByRole("button", { name: "バックアップ一覧を表示" }));
  expect(await screen.findByText("2026-08-28T12:00:00Z")).toBeTruthy();
  expect(screen.getByRole("link", { name: "JSONをダウンロード" }).getAttribute("href"))
    .toBe("/api/data/export?format=json");
  expect(screen.getByRole("link", { name: "CSV ZIPをダウンロード" }).getAttribute("href"))
    .toBe("/api/data/export?format=csv");

  fireEvent.click(screen.getByRole("button", { name: "バックアップを作成" }));
  const createdRow = await screen.findByRole("listitem", { name: `バックアップ ${created.id}` });
  expect(createdRow.textContent).toContain("検証済み");

  fireEvent.click(within(createdRow).getByRole("button", { name: "再検証" }));
  expect((await screen.findByRole("status")).textContent).toContain("バックアップを検証しました。");

  fireEvent.click(within(createdRow).getByRole("button", { name: "このバックアップを復元" }));
  expect((await screen.findByRole("status")).textContent).toContain("復元しました。復元前のデータも保全されています。");
  expect(fetchMock).toHaveBeenCalledWith(
    `/api/data/backups/${created.id}/restore`, { method: "POST" },
  );
});
