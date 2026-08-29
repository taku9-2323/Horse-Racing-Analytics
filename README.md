# Horse Racing Analytics

JRAの単勝・複勝を対象に、レース前の市場情報と将来の独立予測モデルを記録・検証する、ローカルファーストの分析アプリです。

現段階では利益を生む予測モデルではなく、予測を事前固定し、確率校正と収支を正しく評価するための基盤を段階的に開発します。

## Project documents

- [開発計画](DEVELOPMENT_PLAN.md)
- [製品境界と用語](CONTEXT.md)
- [実装仕様](.scratch/horse-racing-analytics/spec.md)
- [UI改善計画](.scratch/horse-racing-analytics/ui-improvement-plan.md)
- [初期予測ルールの文献調査](docs/research/initial-prediction-rules.md)
- [複勝オッズに関するADR](docs/adr/0001-place-odds-are-not-probabilities.md)
- [JRA公開Web取得に関するADR](docs/adr/0002-user-initiated-jra-public-web-acquisition.md)
- [JRA正式レース登録に関するADR](docs/adr/0003-jra-race-registration-requires-odds.md)

## Current status

チケット12「JRA公開ページからオッズを取得する」まで実装しました。JRAレースカード取得後に単勝・複勝オッズURLを指定すると、固定URL、robots、HTTP応答、馬番集合、単勝、複勝範囲を検証し、正式レースと新しいオッズ時点、取得元の監査情報を保存します。取得停止時は既存のCSV取込を使用します。

チケット14「登録済みレースを選択して市場分析と固定予測履歴を見る」を実装しました。JRAまたはCSVから登録したレースを開催日時順の一覧から選び、最新オッズによる市場分析、オッズ時点、固定予測履歴をCSVの再取込なしで確認できます。JRAオッズの正式登録直後は、そのレースを自動選択して表示します。ここで表示する予測は市場オッズ由来の基準と固定履歴であり、独立した勝ち馬予測モデルではありません。次のfrontierはチケット13「JRA公開ページから結果・払戻を取得する」です。

出馬表URLは通常表示の `pw01dde01...` と詳細表示の `pw01dde10...` に対応します。ただしレース日が過去なら登録せず、レース結果URLの指定を求めます。

過去レースは `accessS.html` のレース結果URLを指定します。この段階ではレース基本情報、出走馬、取消・除外状態だけを保存し、着順と払戻は結果取得チケットで扱います。

画面から整合したSQLiteバックアップを作成・再検証・復元でき、復元前の現在データは自動保全されます。監査・移行用に全業務テーブルをJSONまたはテーブル別CSV ZIPとしてダウンロードできます。

## CSV contract

チケット02の入力例は [`examples/sample-race.csv`](examples/sample-race.csv) です。1ファイルに1レースの全出走馬を記載し、UTF-8で保存します。

結果は [`examples/sample-results.csv`](examples/sample-results.csv) と同じ見出しで取り込みます。`status` は `確定`、`取消`、`除外` のいずれか、払戻額はJRA発表の100円当たり金額です。取消・除外では着順を空欄、払戻額を0にすると購入額が同額返還されます。

## Prerequisites

- Python 3.12
- Node.js 24 LTS
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
