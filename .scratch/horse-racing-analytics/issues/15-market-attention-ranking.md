---
id: horse-racing-analytics-15
title: 市場基準から注目順位を表示する
triage: done
---

# 15 — 市場基準から注目順位を表示する

**What to build:** 利用者が登録済みレースについて、同一オッズ時点の正規化市場シェアから算出した注目順位と市場内での相対的位置を確認できるようにする。既存の市場基準計算を再利用し、独自勝率、期待値、購入候補は生成しない。

**Blocked by:** 14 — 登録済みレースを選択して市場分析と固定予測履歴を見る

**Status:** complete

## Completion

Implemented deterministic market-attention ranking for a selected odds snapshot, including competition ranking for ties, observation/receipt timestamps, API error states, saved-snapshot selection in the UI, and an explicit boundary from objective probability, independent prediction, expected value, and purchase recommendations.

- [ ] 選択したオッズスナップショットについて、有効出走馬を正規化市場シェアの降順で順位付けし、同値時の順位規則が固定される
- [ ] 各馬について単勝オッズ、生逆オッズ、正規化市場シェア、市場順位、対象スナップショットの観測時刻と受付時刻をAPIから取得できる
- [ ] 取消・除外馬、単勝オッズ欠損、馬番集合不一致を黙って順位へ含めず、分析不能理由をAPIと画面に表示する
- [ ] 登録済みレース画面で最新時点と保存済み時点を選択し、その時点だけを使った注目順位を確認できる
- [ ] 表示名と説明文が「市場評価による順位」であることを明示し、客観的勝率、独立予測、期待値、購入推奨と表現しない
- [ ] 同じレース・同じオッズスナップショットからは再実行しても同じ順位と値が返る
- [ ] 順位、同値、取消・除外、欠損、時点選択を検証するHTTP APIテストとUI受入テストが成功する
