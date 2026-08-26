---
id: horse-racing-analytics-03
title: CSV取込を安全に再実行する
triage: ready-for-agent
---

# 03 — CSV取込を安全に再実行する

**What to build:** 利用者がCSVの問題をまとめて修正でき、同じファイルを安全に再実行できる取込体験を作る。

**Blocked by:** 02 — 1レースをCSVから分析する

**Status:** complete

- [x] 全エラーが行番号・列・コード・説明付きで一括表示される
- [x] 一件でもエラーがあればデータが一切登録されない
- [x] 同じ内容の再取込は重複を作らず成功する
- [x] 既存の自然キーと異なる内容は競合として拒否される
- [x] 正常・異常・再取込のHTTP APIテストが成功する
