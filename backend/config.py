# backend/config.py
import os
from dotenv import load_dotenv
load_dotenv()  # loads backend/.env automatically

# ── Network Mode ─────────────────────────────────────────
# Set DREAMDEX_ENV=mainnet when competition starts. Default = testnet.
ENV = os.environ.get("DREAMDEX_ENV", "testnet")

# ── Wallet keys (kept separate to avoid mixing testnet/mainnet funds) ────
# export TESTNET_PRIVATE_KEY=0x...   ← testnet deployer wallet
# export MAINNET_PRIVATE_KEY=0x...   ← competition wallet (set just before contest)

# Default address (overridden below per network)
MY_ADDRESS   = "0xe21c64a04562D53EA6AfFeB1c1561e49397B42dd"  # testnet deployer
PRIVATE_KEY  = ""  # resolved below per-network

# ── Capital Split ─────────────────────────────────────────
TOTAL_CAPITAL   = 50.0   # USDso
AGENT_CAPITAL   = 30.0
MANUAL_CAPITAL  = 20.0

# ── Agent Risk Rules ──────────────────────────────────────
AGENT_MIN_TRADE      = 7.00   # main agent min — any LLM-emitted amount below is clamped up
AGENT_MAX_TRADE      = 15.0   # main agent cap; supports bigger volume per fill
AGENT_STOP_BELOW     = 20.0   # capital floor; lowered to leave room for $15 main + $5 micro concurrent exposure

# Micro-agent (parallel, same wallet, shared nonce). Smaller faster trades so
# the leaderboard sees a steady stream of fills alongside the main agent's
# bigger swings.
MICRO_AGENT_MIN_TRADE = 2.0
MICRO_AGENT_MAX_TRADE = 5.0
MICRO_AGENT_LOOP_SECS = 90    # faster than main to keep tx-count rising
AGENT_CONFIDENCE_MIN = 65
MAX_CONCURRENT_POS   = 3
# Hard cap on total trades the agent will execute before auto-holding.
# 0 = unlimited. Adjustable at runtime via POST /agent/max_orders.
AGENT_MAX_ORDERS     = int(os.environ.get("AGENT_MAX_ORDERS", 100))
AGENT_FUNDING_SOURCE = os.environ.get("AGENT_FUNDING_SOURCE", "wallet")  # "vault" or "wallet" — wallet is the only path that actually fills on mainnet

# ── Timing ────────────────────────────────────────────────
AGENT_LOOP_SECONDS = int(os.environ.get("AGENT_LOOP_SECONDS", 300))
PRICE_POLL_SECONDS = 30
LEADERBOARD_POLL   = 300
PRICE_HISTORY_LEN  = 12

# ── OpenAI (R2 legacy — used only by archived agent/brain.py) ─────────────
OPENAI_API   = "https://api.openai.com/v1"
OPENAI_MODEL = "gpt-4o-mini"

# ── Gemini strategist (R3) — Vertex AI via Application Default Credentials ─
# Auth on the server with: gcloud auth application-default login  (NO key in repo).
# google-genai uses Vertex when GOOGLE_GENAI_USE_VERTEXAI=true + project/location set.
GEMINI_MODEL          = os.environ.get("GEMINI_MODEL", "gemini-2.5-pro")
GEMINI_USE_VERTEX     = os.environ.get("GOOGLE_GENAI_USE_VERTEXAI", "true").lower() == "true"
GEMINI_PROJECT        = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
GEMINI_LOCATION       = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
STRATEGIST_INTERVAL_S = int(os.environ.get("STRATEGIST_INTERVAL_S", 480))  # ~8 min between LLM calls
STRATEGIST_ENABLED    = os.environ.get("STRATEGIST_ENABLED", "true").lower() == "true"

# ── R3 Profit-Maker rules ─────────────────────────────────────────────────
# Scoring is Effective Volume = Raw Volume × (1 + PnL%); a wipe = 0. Profit first.
STARTING_CAPITAL   = 150.0   # USDso, fixed by rules — no top-ups ever
STARTING_GAS_SOMI  = 50.0    # SOMI given for gas at start (part of the starting basis)
WBTC_ADDRESS       = "0xC5098b3cA516784323872F17235fa074E167D3D2"  # WBTC token (8 decimals)
RESERVE_USDSO      = float(os.environ.get("RESERVE_USDSO", 20.0))  # held untouched (gas + PnL cushion)
# Contest-eligible pairs we actually quote. SOMI EXCLUDED — its gradual grind
# kept slipping under the trend guard and bled via inventory accumulation. Trade
# only WBTC + WETH (lower-volatility majors); trend guard still gates both.
# Override via ELIGIBLE_PAIRS env (comma-separated) to scope a run, e.g. "WETH:USDso".
ELIGIBLE_PAIRS     = [p.strip() for p in os.environ.get("ELIGIBLE_PAIRS", "WBTC:USDso,WETH:USDso").split(",") if p.strip()]
# Working-capital allocation per pair (fractions of the non-reserve balance).
_DEFAULT_ALLOC     = {"WBTC:USDso": 0.5, "WETH:USDso": 0.5, "SOMI:USDso": 0.5}
MAKER_PAIR_ALLOC   = {p: _DEFAULT_ALLOC.get(p, 1.0 / max(1, len(ELIGIBLE_PAIRS))) for p in ELIGIBLE_PAIRS}

# Two-sided PostOnly market-making
MAKER_LEG_USD      = float(os.environ.get("MAKER_LEG_USD", 65.0))   # USDso notional per resting leg
MAKER_MARGIN_TICKS = int(os.environ.get("MAKER_MARGIN_TICKS", 1))   # SELL ≥ avg_cost + this → no realized loss
MAKER_MAX_INV_USD  = float(os.environ.get("MAKER_MAX_INV_USD", 90.0))  # base-inventory cap for the top-allocated pair (SOMI); WBTC scales to ~$22
MAKER_POLL_S       = int(os.environ.get("MAKER_POLL_S", 8))         # fill-poll / reconcile interval
MAKER_DRIFT_TICKS  = int(os.environ.get("MAKER_DRIFT_TICKS", 2))    # touch drift that triggers a re-quote

# Per-pair stop-loss. If mid drops to avg_cost*(1-pct), cut the whole position
# (IOC into the bid), overriding the no-realized-loss rule, then pause re-entry
# for the cooldown so we don't catch a falling knife. Set pct=0 to disable.
MAKER_STOP_LOSS_PCT   = float(os.environ.get("MAKER_STOP_LOSS_PCT", 0.10))   # trigger: 10% below avg cost
MAKER_STOP_MAX_SLIP_PCT = float(os.environ.get("MAKER_STOP_MAX_SLIP_PCT", 0.03))  # don't sell below cost*(1-pct-slip); defer if book gapped lower
MAKER_STOP_COOLDOWN_S = int(os.environ.get("MAKER_STOP_COOLDOWN_S", 900))    # re-entry pause after a stop

# Trend guard: spread capture needs a two-way (oscillating) market. In a one-way
# DOWNtrend only our bid fills → we accumulate a bleeding bag. So pause BUYING a
# coin while it's trending down (mid fell > pct over the lookback); the SELL side
# stays on to offload. Auto-resumes buying when the coin goes flat/up. Set pct=0 off.
TREND_GUARD_PCT   = float(os.environ.get("TREND_GUARD_PCT", 0.015))   # 1.5% drop over lookback = downtrend
TREND_LOOKBACK_S  = int(os.environ.get("TREND_LOOKBACK_S", 3600))     # compare mid to ~1h ago
KEEPALIVE_LEG_USD = float(os.environ.get("KEEPALIVE_LEG_USD", 1.0))   # tiny buy to reset the idle clock when trend-guarded into cash (avoids 24h DQ)

# Maker+hold mode (trend-gated). Calibration showed: in a sell-skewed market a maker
# just accumulates a bag (the trap sinking rivals). So gate the WHOLE maker on the
# 24h candle trend — only HOLD inventory when the trend is up/flat; in a confirmed
# DOWNtrend, FLATTEN to USDso and idle (protects the live free-USDso multiplier),
# leaving the flat-churn engine (volume_climb.py) to defend rank. Hysteresis via the
# dead-band between DOWN and UP thresholds avoids flip-flop churn. Off by default.
MAKER_HOLD_MODE     = os.environ.get("MAKER_HOLD_MODE", "0") == "1"
MAKER_TREND_UP_PCT  = float(os.environ.get("MAKER_TREND_UP_PCT", 0.02))    # 24h >= +2% → UP (hold/make)
MAKER_TREND_DOWN_PCT= float(os.environ.get("MAKER_TREND_DOWN_PCT", -0.01)) # 24h <= -1% → DOWN (flatten+idle)
MAKER_TREND_CACHE_S = int(os.environ.get("MAKER_TREND_CACHE_S", 300))      # cache candle fetch (don't hit REST every tick)

# Gas management (50 SOMI given, no refills — convert own USDso for more)
GAS_RESERVE_SOMI    = float(os.environ.get("GAS_RESERVE_SOMI", 5.0))   # floor before forced refuel
GAS_REFUEL_USDSO    = float(os.environ.get("GAS_REFUEL_USDSO", 5.0))   # USDso→SOMI per refuel (from working capital)
SOMI_BUY_GAS_LIMIT  = 5_000_000   # native SOMI buy needs ≥5M gas (docs §7a)
ERC20_GAS_LIMIT     = 2_000_000   # Somnia ERC20 transfers need 2M

# Liveness: >24h with no on-chain trade = auto-DQ. Force a tick well before that.
LIVENESS_MAX_IDLE_S = int(os.environ.get("LIVENESS_MAX_IDLE_S", 18 * 3600))  # 18h safety margin

# Capital safety floor: if total account value (USDso + inventory) drops below
# this, the agent auto-stops (flattens to USDso and idles until /start).
MIN_CAPITAL_STOP = float(os.environ.get("MIN_CAPITAL_STOP", 100.0))

# ── Flask server ──────────────────────────────────────────
FLASK_HOST = "0.0.0.0"
FLASK_PORT = int(os.environ.get("FLASK_PORT", 5001))
# Shared-secret API key for mutating POST endpoints. Watch firmware sends it as X-API-Key header.
# Set via `export FLASK_API_KEY=<random>` before launching backend; same value in firmware/wifi_secrets.h.
# If empty AND ENV == "mainnet", server refuses to start. On testnet, missing key disables auth (dev mode).
FLASK_API_KEY = os.environ.get("FLASK_API_KEY", "")

# ── Leaderboard (mainnet-only, always) ────────────────────
# The competition leaderboard lives on mainnet regardless of which network
# the bot is currently trading on. We pin both the URL and the address that's
# looked up so testnet runs still surface our mainnet standing.
# R3 uses a new leaderboard + a fresh registered wallet (rules require zero-TX wallet).
LEADERBOARD_URL     = os.environ.get("LEADERBOARD_URL", "https://dreamdex-leaderboard-new.vercel.app/api/leaderboard")
LEADERBOARD_ADDRESS = os.environ.get("WALLET_ADDRESS", "0xD84fE2a2220f0269e3d88dab908ADceb2d691E76")  # R3 wallet

# ═══════════════════════════════════════════════════════════
# NETWORK-SPECIFIC CONFIG
# ═══════════════════════════════════════════════════════════

if ENV == "mainnet":
    # ── Mainnet (Somnia, chain ID 5031) ───────────────────
    CHAIN_ID      = 5031
    SOMNIA_RPC    = "https://api.infra.mainnet.somnia.network/"
    # Failover pool: the primary infra node is flaky under sustained load, so a
    # single blip no longer surfaces as an error — the provider rotates to a
    # healthy public node instead. Override/extend via SOMNIA_RPC_FALLBACKS (CSV).
    SOMNIA_RPCS   = [SOMNIA_RPC] + [u.strip() for u in os.environ.get(
        "SOMNIA_RPC_FALLBACKS",
        "https://somnia-rpc.publicnode.com,https://rpc.ankr.com/somnia_mainnet",
    ).split(",") if u.strip()]
    DREAMDEX_HTTP = "https://api.dreamdex.io"
    DREAMDEX_WS   = "wss://api.dreamdex.io/v0/ws/public"

    # R3 fresh wallet (zero-TX, registered). Override via WALLET_ADDRESS if rotated.
    MY_ADDRESS  = os.environ.get("WALLET_ADDRESS", "0xD84fE2a2220f0269e3d88dab908ADceb2d691E76")
    PRIVATE_KEY = os.environ.get("MAINNET_PRIVATE_KEY", "")    # export MAINNET_PRIVATE_KEY=0x...

    MARKETS = {
        "WETH:USDso": {
            "symbol":      "WETH:USDso",
            "ws_symbol":   "WETH-USDso",
            "contract":    "0xa936da11B57b50A344e1293AAaE5232885ea2bDE",
            "base":        "0x936Ab8C674bcb567CD5dEB85D8A216494704E9D8",
            "quote":       "0x00000022dA000002656c64D9eA6011ea952D008A",
            "baseDecimals": 18,
            "quoteDecimals": 18,
            "gasSponsored": False,
            "native":       False,
        },
        "WBTC:USDso": {
            "symbol":      "WBTC:USDso",
            "ws_symbol":   "WBTC-USDso",
            "contract":    "0x25bfF6B7B5E2243424F38E75de7ab03C0522a5EA",
            "base":        "0xC5098b3cA516784323872F17235fa074E167D3D2",
            "quote":       "0x00000022dA000002656c64D9eA6011ea952D008A",
            "baseDecimals": 8,
            "quoteDecimals": 18,
            "gasSponsored": False,
            "native":       False,
        },
        "SOMI:USDso": {
            "symbol":      "SOMI:USDso",
            "ws_symbol":   "SOMI-USDso",
            "contract":    "0x035De7403eac6872787779CCA7CCF1b4CDb61379",
            "base":        "0x0000000000000000000000000000000000000000",  # native
            "quote":       "0x00000022dA000002656c64D9eA6011ea952D008A",
            "baseDecimals": 18,
            "quoteDecimals": 18,
            "gasSponsored": True,
            "native":       True,   # use depositNative() + payable taker variant
        },
        "USDC.e:USDso": {
            "symbol":      "USDC.e:USDso",
            "ws_symbol":   "USDC.e-USDso",
            "contract":    "0x47fD2f18426f67106DBaC82F6d21D446c5F2120b",
            "base":        "0x28BEc7E30E6faee657a03e19Bf1128AaD7632A00",
            "quote":       "0x00000022dA000002656c64D9eA6011ea952D008A",
            "baseDecimals": 6,
            "quoteDecimals": 18,
            "gasSponsored": True,
            "native":       False,
        },
    }

else:
    # ── Testnet (Somnia Shannon, chain ID 50312) ──────────
    CHAIN_ID      = 50312
    SOMNIA_RPC    = "https://api.infra.testnet.somnia.network"
    SOMNIA_RPCS   = [SOMNIA_RPC] + [u.strip() for u in os.environ.get(
        "SOMNIA_RPC_FALLBACKS", "").split(",") if u.strip()]
    MY_ADDRESS    = "0xe21c64a04562D53EA6AfFeB1c1561e49397B42dd"  # testnet deployer wallet
    PRIVATE_KEY   = os.environ.get("TESTNET_PRIVATE_KEY", "")    # export TESTNET_PRIVATE_KEY=0x...
    DREAMDEX_HTTP = "https://stg.api.dreamdex.io"
    DREAMDEX_WS   = "wss://stg.api.dreamdex.io/v0/ws/public"

    # USDso on testnet (confirmed from sandbox scripts)
    USDSO_TESTNET = "0x9c32F3827A1a99f0cf9B213de8b53eC3d57bb171"

    # Testnet has 3 pairs only (no USDC.e)
    MARKETS = {
        "WETH:USDso": {
            "symbol":       "WETH:USDso",
            "ws_symbol":    "WETH-USDso",
            "contract":     "0xD180195da5459C7a0DEA188ed61216ec43682b50",
            "base":         "0x0000000000000000000000000000000000000000",  # query pool
            "quote":        USDSO_TESTNET,
            "baseDecimals":  18,
            "quoteDecimals": 18,
            "gasSponsored":  False,
            "native":        False,
        },
        "WBTC:USDso": {
            "symbol":       "WBTC:USDso",
            "ws_symbol":    "WBTC-USDso",
            "contract":     "0x3605f28aA7C50e7441211e77Cb0762d49539326C",
            "base":         "0x0000000000000000000000000000000000000000",
            "quote":        USDSO_TESTNET,
            "baseDecimals":  8,
            "quoteDecimals": 18,
            "gasSponsored":  False,
            "native":        False,
        },
        "SOMI:USDso": {
            "symbol":       "SOMI:USDso",
            "ws_symbol":    "SOMI-USDso",
            "contract":     "0x259fD6559214dd5aD3752322426eA9F9fABEFff4",
            "base":         "0x0000000000000000000000000000000000000000",  # native STT
            "quote":        USDSO_TESTNET,
            "baseDecimals":  18,
            "quoteDecimals": 18,
            "gasSponsored":  True,
            "native":        True,
        },
    }

# Convenience: USDso address (same for quote in all pairs)
USDSO_ADDRESS = list(MARKETS.values())[0]["quote"]
