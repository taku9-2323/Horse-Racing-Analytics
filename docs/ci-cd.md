# CI/CDとGitHub Release配布

対象workflowは [`.github/workflows/ci.yml`](../.github/workflows/ci.yml) です。ここでは設定内容を説明します。実際の実行結果は[GitHub Actions](https://github.com/taku9-2323/Horse-Racing-Analytics/actions)、公開済み配布物は[GitHub Releases](https://github.com/taku9-2323/Horse-Racing-Analytics/releases)で確認してください。

## ローカル検証

依存関係を入れた後、リポジトリのルートで `powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\verify.ps1` を実行します。このスクリプトは `backend/.venv` のPythonでmypyとpytestを実行し、`C:\Program Files\nodejs` のnpmでフロントエンドのtypecheck・test・buildを実行します。初回セットアップは [READMEの手順](../README.md#setup) を参照してください。

## GitHub Actions

Pull requestと `main` へのpush、`v*` タグのpush、手動実行で検証workflowが動きます。Python 3.12と `.nvmrc` に記載したNode.js 24.21.0を使い、バックエンドの型検査・テスト、フロントエンドの型検査・テスト・ビルドを行います。

検証に通ると、ビルド済みReact画面を含むランタイムZIPとSHA-256ファイルをworkflow artifactとして7日間保存します。通常の検証jobはリポジトリ読み取り権限です。LLMやモデルAPIは呼び出しません。

## GitHub Release

`v*` タグのpushでは、検証jobが成功した後にRelease jobが動きます。タグのcommitが `main` の履歴に含まれることを確かめ、検証jobが作ったZIPとチェックサムを取得・検証して、GitHub Releaseへ添付します。Release jobの書き込み権限はGitHub Release作成に使います。

リリース対象commitを `main` に取り込んだ後、PowerShellで `git switch main`、`git pull --ff-only`、`git tag -a v1.2.3 -m "v1.2.3"`、`git push origin v1.2.3` の順に実行します。`v1.2.3` は次のバージョンに置き換えてください。

workflowが成功してReleaseページにZIPと `.sha256` が表示されるまでは、そのバージョンを公開済みと案内しないでください。このCI/CDの配布先はGitHub Releasesです。外部の本番ホストへアプリを配備する処理はありません。
