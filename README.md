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

## Current status

チケット05「文献由来の分析タグを管理する」まで完了しました。7種類のタグについて根拠と条件を確認し、確率を変更せず、有効・無効の監査履歴と条件新版を管理できます。次のfrontierはチケット06、08です。

## CSV contract

チケット02の入力例は [`examples/sample-race.csv`](examples/sample-race.csv) です。1ファイルに1レースの全出走馬を記載し、UTF-8で保存します。

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

## Verify

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\verify.ps1
```
