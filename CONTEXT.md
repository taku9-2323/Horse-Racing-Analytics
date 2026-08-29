# Horse-racing analytics context

## Product boundary

The first release is a single-user, local-only JRA analytics prototype. It accepts user-prepared CSV files and user-initiated acquisition from login-free JRA public pages, generates a win market baseline and experimental analysis tags, preserves place break-even information, identifies expected-value candidates only when an independent probability model exists, freezes predictions before results are known, settles real and hypothetical bets, and evaluates calibration and returns.

It does not place bets, scrape netkeiba, access authenticated JRA pages, crawl arbitrary URLs, run unattended collection, authenticate app users, charge subscriptions, redistribute acquired JRA data, or claim guaranteed profit. CSV remains the fallback whenever public-page acquisition is unavailable or disallowed.

## Domain glossary

- **Race**: A contest identified independently of any data provider. It records organizer, country, racecourse, local start time, UTC start time, surface, distance, going, and field size.
- **JRA race registration**: The formal creation of a Race and its Runners from a validated JRA race-card version together with a validated JRA odds snapshot. It preserves the card and odds observations as the provider evidence for that Race.
- **Runner**: A horse entered in a race, including gate, horse number, age, sex, assigned weight, and status.
- **Odds snapshot**: The win and place odds observed at one recorded time. Place odds are a lower/upper range.
- **Source observation**: An immutable, versioned record of facts obtained from one source response, identified by source URL, receipt time, parser version, response hash, and validation outcome. A later correction creates another observation rather than overwriting it.
- **Prediction model**: A versioned method that produces a win probability, a place probability, or both. The initial market baseline produces only a win market-share proxy; there is no initial independent place-probability model.
- **Prediction run**: The immutable output of one prediction model for every runner in one race, based on specified input snapshots and an as-of time.
- **Market baseline**: A win-market consensus proxy inferred from odds. Raw inverse odds and normalized win betting-share proxies are kept separately and are not labeled objective win probabilities.
- **Place break-even range**: The reciprocals of the displayed place-odds range. It states the hit rate needed to break even at each possible payout, not a predicted place probability.
- **Experimental rule set**: A versioned, independently evaluated hypothesis that may tag observations or, only when an evidence-based magnitude exists, adjust a probability model. Initial literature-derived rules are disabled tags with no multiplier.
- **Rule tag**: A descriptive condition recorded for later analysis without changing probability when no evidence-based adjustment magnitude exists.
- **Expected value (EV)**: Predicted probability multiplied by decimal odds. A candidate threshold of 1.10 means an expected return of 110 yen per 100 yen staked before considering estimation error.
- **Expected-value candidate**: A bet whose eligible, independent probability model reaches EV 1.10 or more at the prediction-freeze odds. Place candidates require an independent place-probability model and use the lower odds bound; the initial release normally produces no candidates.
- **Frozen prediction**: A prediction run that cannot be edited. Corrections invalidate the old version and create a new version with a reason; post-deadline corrections are excluded from official evaluation.
- **Real bet**: A bet actually purchased by the user.
- **Hypothetical bet**: A simulated 100-yen bet automatically recorded for every candidate.
- **Settlement**: Applying an official result, payout, or refund to a bet.
- **Calibration**: Agreement between predicted probability and observed frequency, measured initially with Brier score and probability-band outcomes.

## Evaluation principle

Market-derived probabilities are a baseline, not evidence of an exploitable edge. Experimental rules remain hypotheses. Profitability claims require prospective, frozen predictions and must not be inferred from descriptive associations or in-sample fitting.
