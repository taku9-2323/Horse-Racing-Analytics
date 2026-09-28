# Horse Racing Analytics

[GitHub repository](https://github.com/taku9-2323/Horse-Racing-Analytics)

JRAの単勝・複勝を対象に、レース前の市場情報と将来の独立予測モデルを記録・検証する、ローカルファーストの分析アプリです。

現段階では利益を生む予測モデルではなく、予測を事前固定し、確率校正と収支を正しく評価するための基盤を段階的に開発します。

## Project documents

- [CI/CDとGitHub Release配布](docs/ci-cd.md)
- [配布ZIPのWindowsセットアップ](docs/release-package.md)
- [開発計画](DEVELOPMENT_PLAN.md)
- [製品境界と用語](CONTEXT.md)
- [実装仕様](.scratch/horse-racing-analytics/spec.md)
- [UI改善計画](.scratch/horse-racing-analytics/ui-improvement-plan.md)
- [初期予測ルールの文献調査](docs/research/initial-prediction-rules.md)
- [複勝オッズに関するADR](docs/adr/0001-place-odds-are-not-probabilities.md)
- [JRA公開Web取得に関するADR](docs/adr/0002-user-initiated-jra-public-web-acquisition.md)
- [JRA正式レース登録に関するADR](docs/adr/0003-jra-race-registration-requires-odds.md)
- [独立予測モデル契約に関するADR](docs/adr/0004-independent-prediction-model-contract.md)

## Current status

チケット13「JRA公開ページから結果・払戻を取得する」まで実装しました。JRAレースカードと単勝・複勝オッズから正式登録したレースに結果URLを指定すると、着順、出走状態、単勝・複勝の100円当たり払戻、返還対象を全件検証し、結果版と購入精算を原子的に保存します。再取得で公式値が変わった場合は旧版、差分、訂正理由、最新有効版を保持します。取得停止時は既存のCSV取込を使用します。

チケット14「登録済みレースを選択して市場分析と固定予測履歴を見る」も実装済みです。JRAまたはCSVから登録したレースを開催日時順の一覧から選び、最新オッズによる市場分析、オッズ時点、固定予測履歴をCSVの再取込なしで確認できます。ここで表示する予測は市場オッズ由来の基準と固定履歴であり、独立した勝ち馬予測モデルではありません。

チケット07「予測精度と収支を評価する」も実装済みです。確定結果のある公式評価対象予測について、全出走馬の単勝Brierスコア、確率帯別の件数・平均予測確率・実的中率、少数標本表示を確認できます。購入時に明示した固定予測とオッズ文脈を保存し、候補内実購入と候補外裁量の収支を分離して、モデル版、タグ版、競馬場、券種、オッズ帯、人気帯、予測固定時刻で絞り込めます。複勝のオッズ帯は保存した下限オッズを使います。

チケット09「複数レースの受入フローを完成させる」まで実装済みです。通常払戻、購入なしの候補なし、除外返還を含む複数レースをCSV取込から予測固定、精算、評価まで処理し、バックアップ復元後もレース件数、固定予測、結果、台帳、集計値が一致することを受入テストで確認しています。JRA公開Web取得が停止した場合も、レース情報と結果のCSV取込を使って同じローカル運用を継続できます。

チケット10「独立予測モデルの追加境界を固める」も実装済みです。将来のモデルは、単勝確率、複勝確率、または両方を、モデル識別子・版・モデル時点・根拠とともにHTTP APIから固定できます。市場基準は `win_market_share`、独立モデルは `win_probability` / `place_probability` として型と画面表示を分離し、確定結果後は単勝・複勝を別々に評価できます。この境界はモデル追加用であり、現時点で利益優位性のあるモデルが完成したことを意味しません。

出馬表URLは通常表示の `pw01dde01...` と詳細表示の `pw01dde10...` に対応します。ただしレース日が過去なら登録せず、レース結果URLの指定を求めます。

過去レースは `accessS.html` のレース結果URLを指定します。正式レース登録後は「実購入・結果・収支」から同じレースの結果URLを指定し、公式結果と払戻を取得して精算できます。

画面から整合したSQLiteバックアップを作成・再検証・復元でき、復元前の現在データは自動保全されます。監査・移行用に全業務テーブルをJSONまたはテーブル別CSV ZIPとしてダウンロードできます。

## Improvement issues

- [#9 判定理由で未達条件と現在値を区別する](https://github.com/taku9-2323/Horse-Racing-Analytics/issues/9)
- [#10 券種・モデル版別の確率校正](https://github.com/taku9-2323/Horse-Racing-Analytics/issues/10)
- [#11 事前固定ルールの版別成績](https://github.com/taku9-2323/Horse-Racing-Analytics/issues/11)
- [#12 注目頭数と判定対象頭数の表示](https://github.com/taku9-2323/Horse-Racing-Analytics/issues/12)
- [#13 PC・スマートフォンでの出馬表比較](https://github.com/taku9-2323/Horse-Racing-Analytics/issues/13)
- [#14 保存済みオッズの推移と差分](https://github.com/taku9-2323/Horse-Racing-Analytics/issues/14)
- [#15 個人の印・メモ・振り返り](https://github.com/taku9-2323/Horse-Racing-Analytics/issues/15)
- [#16 許可データ源と独立モデル導入条件の調査](https://github.com/taku9-2323/Horse-Racing-Analytics/issues/16)

## CSV contract

チケット02の入力例は [`examples/sample-race.csv`](examples/sample-race.csv) です。1ファイルに1レースの全出走馬を記載し、UTF-8で保存します。

結果は [`examples/sample-results.csv`](examples/sample-results.csv) と同じ見出しで取り込みます。`status` は `確定`、`取消`、`除外` のいずれか、払戻額はJRA発表の100円当たり金額です。取消・除外では着順を空欄、払戻額を0にすると購入額が同額返還されます。

## Sample data

`examples/sample-race.csv` and `examples/sample-results.csv` use fabricated demonstration values. They are not real JRA races, odds, or results and must not be treated as historical or model-evaluation data.

## CI/CD

The workflow in `.github/workflows/ci.yml` is configured to check pull requests and pushes to `main` with backend type-checking/tests and frontend type-checking/tests/build. A push of a `v*` tag on a commit in `main` is configured to publish the verified ZIP and checksum as a GitHub Release. This distributes files through GitHub; it does not deploy the application to an external production host, and the workflow does not call an LLM.

Check [GitHub Actions](https://github.com/taku9-2323/Horse-Racing-Analytics/actions) and [GitHub Releases](https://github.com/taku9-2323/Horse-Racing-Analytics/releases) for actual run and publication status. This README describes the configured flow; it does not claim that a run has succeeded or a release has been published.

## Prerequisites

- Python 3.12
- Node.js 24.21.0
- Git 2.55

## Setup

初回だけ、PowerShellで依存関係を導入します。

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"

cd ..\frontend
npm install
npm run build
```

## Start locally

プロジェクトルートから実行します。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\start-backend.ps1
```

ブラウザで `http://127.0.0.1:8000` を開きます。FastAPIがビルド済みReactとAPIを同一オリジンで配信し、外部ネットワークへ公開しません。

UIを変更した場合は、再起動前に `frontend` で `npm run build` を実行します。

SQLiteの実データはOneDrive同期対象外の `%LOCALAPPDATA%\HorseRacingAnalytics` に保存します。
バックアップは同じローカル領域の `backups` に保存されます。復元操作では対象を検証し、現在DBの保全バックアップを作ってから内容を入れ替えます。

## Verify

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\verify.ps1
```

## License

There is currently no `LICENSE` file in this repository; no license has been selected or added here.
