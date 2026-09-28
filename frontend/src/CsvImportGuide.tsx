import raceSample from "virtual:sample-race";
import resultSample from "virtual:sample-results";

export default function CsvImportGuide({ kind }: { kind: "race" | "results" }) {
  const sample = kind === "race" ? raceSample : resultSample;
  const label = kind === "race" ? "レース" : "結果";
  const filename = kind === "race" ? "sample-race.csv" : "sample-results.csv";
  return <div className="csv-import-guide">
    <a href={`data:text/csv;charset=utf-8,${encodeURIComponent(sample)}`} download={filename}>{label}CSVのサンプルをダウンロード</a>
    <details>
      <summary>{label}CSVの形式・入力例</summary>
      <p>UTF-8で保存し、1ファイルに1レースの全出走馬を1頭1行で記載してください。サンプルのダウンロードだけではデータは登録されません。</p>
      <p>必須列（見出しは変更せず、すべての列を用意）：</p>
      <code>{sample.split(/\r?\n/)[0]}</code>
      {kind === "race" ? <>
        <p>レース情報は全行で統一します。race_dateはYYYY-MM-DD、start_timeはHH:mm、timezoneはAsia/Tokyo、start_utcは対応するUTC日時です。win_odds・place_odds_min・place_odds_maxには単勝オッズ・複勝下限・上限を指定します。</p>
        <p>statusは「出走」（出走予定）、「取消」（出走取消）、「除外」（出走除外）です。</p>
      </> : <>
        <p>horse_numberは対象レースの馬番、finish_positionは着順です。win_payout_per_100・place_payout_per_100はJRA発表の100円当たり単勝・複勝払戻額（円）で、購入総額やオッズではありません。</p>
        <p>statusは「確定」（着順あり）、「取消」「除外」（返還対象）、「競走中止」（返還なし）です。取消・除外・競走中止の着順は空欄、払戻額は0にします。未確定の結果は取り込まないでください。</p>
        <p>入力例は同じサンプルレースの結果です。実際のレースへ取り込む前に馬番と公式結果へ置き換えてください。</p>
      </>}
    </details>
  </div>;
}
