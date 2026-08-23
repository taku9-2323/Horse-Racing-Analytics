---
id: horse-racing-analytics-01
title: ローカルアプリを起動する
triage: ready-for-agent
---

# 01 — ローカルアプリを起動する

**What to build:** 利用者がPC上でアプリを起動し、ブラウザからAPIとデータベースの稼働状態を確認できる、最小のエンドツーエンド経路を作る。

**Blocked by:** None — can start immediately

**Status:** complete

- [x] FastAPI、React、SQLiteを含むアプリを再現可能な手順で起動できる
- [x] APIの稼働確認がHTTP境界のテストを通る
- [x] ブラウザ画面にAPIとデータベースの状態が日本語で表示される
- [x] サーバーが既定でloopbackだけにバインドされる
- [x] バックエンドテスト、フロントエンド型検査、ビルドが成功する
