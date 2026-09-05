# ADR 0002: Use guarded, user-initiated JRA public-web acquisition

- Status: Accepted
- Date: 2026-09-05

## Context

User-prepared CSV files create too much input work for prospective personal evaluation, but the prototype has no contracted machine-readable feed. General web crawling would add usage-condition, availability, data-quality, and maintenance risks.

## Decision

For the single-user local prototype, the app may acquire race cards, win/place odds, results, and payouts only from login-free JRA public pages selected by the user. A user may also select the current Asia/Tokyo meeting week as one bounded acquisition target. In that flow, discovery starts only from the fixed annual calendar URL, accepts only fixed-shape daily program links whose dates fall inside that week, posts the fixed public race-card selection action, and then follows only validated race-card and odds identifiers for those dates. It does not inspect arbitrary links or expand beyond the selected week.

Acquisition is serial, rate-limited to at least one second between HTTP requests, cached for 15 minutes while raw HTML evidence is retained for at most seven days, limited to the JRA allowlisted host and fixed URL shapes, and must stop on access refusal, robots refusal, unexpected responses, or unresolved usage conditions. CSV import remains a permanent fallback; netkeiba, authenticated pages, unattended crawling, access-control bypass, redistribution, and commercial use are excluded.

Each response becomes an immutable source observation with receipt time, any source-declared update time, source URL, parser version, response hash, and validation result. Registration is atomic and fail-closed. Corrections create new versions, real HTML is retained locally for at most seven days, and repository tests use minimal fictional fixtures.

## Consequences

- Backup and restore must be completed before acquisition is introduced.
- The implementation is split into race-card, odds, and result/payout vertical slices.
- A meeting-week run persists schedule, entry, odds, and judgement readiness separately so unpublished data is a visible waiting state rather than a false negative.
- HTML changes can stop acquisition; they must never silently create partial or default-filled records.
- Publication or monetization requires a separate data-use decision and written confirmation of the intended acquisition, storage, display, prediction, and charging model.

## Evidence

JRA's [website-use guidance](https://www.jra.go.jp/use/) requires checking the terms applicable to each JRA-operated site and directs uses beyond applicable private-use or quotation boundaries to its permission process. This ADR is a conservative product boundary for a personal prototype, not a conclusion that scraping is generally permitted.
