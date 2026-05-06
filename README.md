# Solana Token Tracker

A self-hosted dashboard for tracking a single SPL token across multiple Solana wallets. Reconciles regular trades, Jupiter DCA orders, Jupiter Limit V1/V2 orders (with keeper fills and cancellation refunds), Meteora DLMM LP activity, and Metaplex airdrops into a unified P/L view. Includes a forward-looking "Cardholder Projection" calculator for NFT cardholder airdrop yield estimation.

## Features

- Multi-wallet aggregation across one target token
- Auto-detected airdrops, escrow funding, and DCA setups (configurable program whitelist)
- Limit-order pairing: matches setup transactions to fills via shared Reserve token accounts
- Trade Insights: best/worst entries by category, with per-trade P/L impact
- Cardholder Projection: forward emissions and yield estimator for NFT-airdrop holders

## Run locally

Requirements: Python 3.10+, a Helius API key (free tier available at <https://helius.xyz>).

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
echo "HELIUS_API_KEY=your_key_here" > .env
python app.py
```

Open <http://localhost:5000>.

Users can also paste their own Helius key in the UI to bypass the server's default — useful for shared deployments where you don't want to share your quota.

## Deploy to Render

This repo includes `render.yaml` for one-click deployment to Render.com (free tier).

1. Push this repo to GitHub.
2. On Render, create a new Web Service from your GitHub repo. Render will detect `render.yaml` and configure itself.
3. In Render's dashboard, set the `HELIUS_API_KEY` environment variable to your key (or leave empty if you want every user to bring their own key via the UI).
4. Click Deploy.

The free tier sleeps after 15 min of inactivity; first request after a sleep takes ~30s to wake up.

## Notes

- Cache is local-only (`cache/` dir). On Render's ephemeral filesystem, the cache is wiped on each redeploy. The "Force Refresh" button in the UI rebuilds the cache for any wallet.
- The free Helius tier is 100k credits/day — shared across all users hitting your server's default key. For a public deployment, encourage users to bring their own key.
- This is a personal tool, not a financial advice product. The math is best-effort reconciliation of on-chain data; verify before making decisions.
