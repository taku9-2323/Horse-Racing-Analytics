# ADR 0002: Use guarded, user-initiated JRA public-web acquisition

- Status: Accepted
- Date: 2026-08-28

## Context

User-prepared CSV files create too much input work for prospective personal evaluation, but the prototype has no contracted machine-readable feed. General web crawling would add usage-condition, availability, data-quality, and maintenance risks.

## Decision

For the single-user local prototype, the app may acquire race cards, win/place odds, results, and payouts only from login-free JRA public pages selected by the user. Acquisition is serial, low-frequency, cached, limited to allowlisted hosts and fixed URL shapes, and must stop on access refusal, robots refusal, unexpected responses, or unresolved usage conditions. CSV import remains a permanent fallback; netkeiba, authenticated pages, unattended crawling, access-control bypass, redistribution, and commercial use are excluded.

Each response becomes an immutable source observation with receipt time, any source-declared update time, source URL, parser version, response hash, and validation result. Registration is atomic and fail-closed. Corrections create new versions, real HTML is retained locally for at most seven days, and repository tests use minimal fictional fixtures.

## Consequences

- Backup and restore must be completed before acquisition is introduced.
- The implementation is split into race-card, odds, and result/payout vertical slices.
- HTML changes can stop acquisition; they must never silently create partial or default-filled records.
- Publication or monetization requires a separate data-use decision and written confirmation of the intended acquisition, storage, display, prediction, and charging model.

## Evidence

JRA's [website-use guidance](https://www.jra.go.jp/use/) requires checking the terms applicable to each JRA-operated site and directs uses beyond applicable private-use or quotation boundaries to its permission process. This ADR is a conservative product boundary for a personal prototype, not a conclusion that scraping is generally permitted.
