---
id: horse-racing-analytics-11
title: JRA公開ページからレースを取得する
triage: ready-for-human
---

# 11 — JRA公開ページからレースを取得する

**What to build:** 利用者が選んだJRAの公開ページからレース情報と出走馬を取得し、由来と検証結果を確認して登録できるようにする。

**Blocked by:** 08 — データをバックアップ・出力する

**Status:** ready-for-human — 架空fixtureによるAPI受入テスト、UIテスト、型検査を実装。実行環境のWindows Application Controlによりフロントエンド実行テスト／ビルドと実ブラウザ確認が未完了。

- [ ] ログイン不要な許可ホスト・固定URL形式だけを利用者操作で取得する
- [ ] レース、出走馬、状態を既存の正規化入力へ変換する
- [ ] 必須項目、レース識別子、頭数、馬番集合を全件検証し、異常時は何も登録しない
- [ ] URL、受付時刻、JRA側更新時刻、パーサー版、応答ハッシュ、検証結果を記録する
- [ ] HTTP拒否、robots拒否、想定外応答では停止し、CSVフォールバックを案内する
- [ ] 実HTMLをリポジトリへ含めず、架空値fixtureによるHTTP API受入テストが成功する
