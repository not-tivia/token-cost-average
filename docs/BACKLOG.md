# Solana Tracker — Feature Backlog

Captured 2026-05-06 during the cardholder-projection branch. None of these are in flight; each gets its own brainstorm → spec → plan cycle when prioritized.

## Currently Prioritized

- **Foundation wallet buyback monitor** — real-time tracking of foundation wallet activity (USDC inflows, USDC→CARDS swaps, CARDS→burn, ecosystem grants). Polling job + alerts. Highest signal per line of code; doesn't depend on the hard cost-basis-for-others problem. Need from user: foundation wallet addresses (e.g., the one referred to as "red"), and decision on alert delivery (browser notification, email, Discord webhook, file-on-disk).

## Queued (independent)

- **P/L from past buys/sells vs current price tab** (queued from prior session). A new tab in the existing Trade Insights section showing what each past buy/sell would be worth at the current price vs. the price executed at. Cosmetic / mood feature per user.

## Larger sub-projects — share a "non-self wallet analytics" data layer

These four features all need the same expansion: enumerate top holders for the target token (Helius `getTokenLargestAccounts` + paginated `getProgramAccounts`), then scan each wallet's tx history. Expensive in API credits. Cost basis for wallets you don't own is approximate (on-chain DEX price at swap time), not exact.

- **Net flow per wallet** — over 30/60/90 days, classify each top-N wallet as net-buying or net-selling. Aggregate output: "X% of top wallets net accumulating in last 30 days." One extra column per wallet.
- **Wallet-level sell expectations model** — heuristic-based per-wallet "likely sell price" derived from cost basis + recent behavior. Buckets: cost-basis > price → break-even seller; cost-basis < $0.10 + active → psychological-level seller; cost-basis < $0.10 + dormant → conviction holder; else → next-round-number seller. Aggregate to "supply expected to hit market between $X and $Y" curve.
- **Time-weighted holdings** — average accumulation date per wallet. Distinguishes diamond hands (early, holding) from swing traders (recent, less price-sensitive at same cost basis).
- **Top 10 wallet specific tracker** — for the largest holders, individual tracking with user-assigned names/aliases, recent movement feed, inferred cost basis per wallet.

## Higher-order outputs (depend on the above)

- **Buyback simulation integrated with cost basis** — given assumed buyback rate $X/month, what % of expected sell pressure between $A and $B does it absorb? Per-price-bucket output. Depends on having cost-basis distribution data → depends on the wallet-analytics data layer above.
- **Streamflow vesting recipient tracker** — for wallets receiving from Streamflow program, track claimed vs still-in-wallet vs sold. Tells you what VCs are doing with unlocks. Pre-September signal for what to expect from team unlocks. Independent of the top-holder layer (Streamflow program ID is the entry point).
- **The unified output: net expected sell pressure by price bucket** — combines sell-expectations curve + buyback simulation into a single table showing where supply pressure clears vs. piles up. End-state product of the wallet analytics + buyback work.

## Notes

- Most of these are a different *class* of tool than the current tracker (which tracks YOUR wallets for ONE token). Building them in the same Flask app is fine for now, but at some point the codebase will want to split into "personal portfolio" vs. "market intel."
- Real-time monitoring features (foundation wallet, Streamflow) need a polling job, not a Flask page request. Decide between: a separate scheduled script that writes JSON to disk and the Flask page reads it, or a long-poll endpoint, or an external runner (cron + webhook).
