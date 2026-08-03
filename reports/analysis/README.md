# On-chain trade dataset & analysis

Complete on-chain trade history for wallet `0x0000000000000000000000000000000000000000`,
scraped from the Somnia mainnet Blockscout v2 API (the direct-burst engine fires
raw RPC and never logged to `agent.db`, so the chain is the only complete record).

## Dataset snapshot (2026-06-02)
- **63,569 transactions** total, stored in `/app/data/onchain_trades.db` (table `onchain_tx`).
- **Total gas spent: 136.34 SOMI** — the real cost of the whole campaign.
- Status: 58,062 ok · 5,507 reverted (8.7% revert rate).
- Actions: 31,244 buy · 30,874 sell · 1,451 other (approvals etc.).

### Fill rate by pair (fill = settled tokens; gas-heuristic, gas_used ≥ 250k)
| Pair | Txs | Filled | Fill rate |
|---|---|---|---|
| USDC.e:USDso | 13,836 | 13,761 | **99.5%** |
| SOMI:USDso | 8,419 | 7,041 | 83.6% |
| WETH:USDso | 39,703 | 28,751 | 72.4% |
| WBTC:USDso | 160 | 91 | 56.9% |

**Bot-tuning takeaway:** the direct-burst + skip-sim + slip-0 config on **USDC.e**
hit ~99.5% fills — far above WETH (72%) and the early REST phase (~35–53%). The
post-contest profit bot should start from that config.

## Scripts
- `scrape_trades.py` — scrape (resumable; upserts by tx hash) + behavioral analysis
  + burst-stall gap detection. Flags: `--scrape`, `--analyze`, `--gap-seconds`,
  `--fill-gas`. Run in the container so `config.MARKETS` imports.
  ```
  docker exec dreamdex-agent python3 /app/analysis/scrape_trades.py --scrape --analyze
  ```

## Notes / limitations
- **Fill flag is a gas heuristic** — the v2 list endpoint omits `token_transfers`.
  A clear bimodal split (no-fill <250k gas, fill ~380–460k) makes this reliable;
  tune with `--fill-gas`.
- **The burst-stall gap method is noisy for MM-outage detection** — a tx-stream gap
  can mean "MM died" OR "we stopped trading," and the burst has its own ~2.5–5 min
  restart cadence. It confirmed the two proven blackouts but over-counts. For
  authoritative liquidity-outage timing use `../evidence/scan_blackout_history.py`
  (reads the order book on-chain directly, independent of our activity).
