# ADR 0004: 独立予測モデルは市場基準と型を分けて固定する

- Status: Accepted
- Date: 2026-09-01

## Context

既存の固定予測は、単勝オッズから計算した市場投票シェアだけを保存していた。将来の独立モデルは単勝確率、複勝確率、または両方を返せる必要があるが、市場投票シェアや複勝損益分岐率を予測確率として扱ってはならない。

## Decision

- 固定予測に `prediction_kind` を持たせ、`market_baseline` と `independent` を区別する。
- 独立モデルの公開境界は `POST /api/odds-snapshots/{snapshot_id}/independent-predictions/freeze` とする。
- 独立モデルは識別子、版、モデル時点、根拠、全出走馬の出力を一度に渡す。
- 出力は `win_probability`、`place_probability`、または両方とする。同じ予測実行内では全出走馬が同じ出力能力を持ち、単勝確率の合計は1とする。
- 市場基準の出力は `raw_inverse_win_odds` と `win_market_share`、独立モデルの出力は `win_probability` と `place_probability` とし、APIとUIのフィールド名を共有しない。
- 固定後の直接編集は禁止する。独立モデルの訂正はモデルを再実行し、新しい出力を固定する。
- 公式評価では単勝と複勝を別の `bet_type` として集計する。複勝の実績は公式複勝払戻の有無から判定する。

## Consequences

モデル実装はHTTP契約へ出力を渡すだけで、既存の時点固定、結果接続、モデル版フィルター、Brier集計を利用できる。期待値候補や仮想購入の生成はこの境界には含めず、独立モデルの検証後に別判断とする。
