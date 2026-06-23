"""Periodic strategist — Gemini 2.5 Pro via Vertex AI (Application Default Creds).

NOT in the hot loop. Every STRATEGIST_INTERVAL_S it reviews recent trade context
+ live book stats and proposes parameters. The deterministic loop applies HARD
guardrails to whatever it returns: the LLM can never disable the reserve, set a
loss-making spread, or oversize a leg. If Gemini is unavailable, we fall back to
safe defaults and keep trading.
"""
import json

import config

# Hard guardrails — the strategist's output is clamped into these.
_SPREAD_MULT_RANGE = (0.5, 5.0)
_LEG_USD_RANGE = (2.0, 25.0)
_MAX_INV_RANGE = (5.0, 60.0)


def default_decision() -> dict:
    return {
        "active_pairs": list(config.ELIGIBLE_PAIRS),
        "per_pair": {
            p: {
                "spread_mult": 1.0,
                "leg_usd": config.MAKER_LEG_USD,
                "max_inv_usd": config.MAKER_MAX_INV_USD,
                "pause": False,
            }
            for p in config.ELIGIBLE_PAIRS
        },
        "rationale": "default (strategist disabled or unavailable)",
    }


class Strategist:
    def __init__(self):
        self._client = None
        self._init_error = None
        if config.STRATEGIST_ENABLED:
            self._try_init()

    def _try_init(self):
        try:
            from google import genai  # google-genai
            if config.GEMINI_USE_VERTEX and not config.GEMINI_PROJECT:
                print("[strategist] ⚠️  GOOGLE_CLOUD_PROJECT unset — Vertex/ADC will likely fail", flush=True)
            self._client = genai.Client(
                vertexai=config.GEMINI_USE_VERTEX,
                project=config.GEMINI_PROJECT or None,
                location=config.GEMINI_LOCATION,
            )
            self._ping()
        except Exception as e:
            self._init_error = str(e)
            self._client = None
            print(f"[strategist] init failed, using defaults: {e}", flush=True)

    def _ping(self):
        """One cheap call so deploy logs confirm ADC works (or fail loud)."""
        resp = self._client.models.generate_content(
            model=config.GEMINI_MODEL, contents="reply with: ok",
            config={"temperature": 0.0},
        )
        print(f"[strategist] Gemini reachable via {'Vertex/ADC' if config.GEMINI_USE_VERTEX else 'API'} "
              f"(model={config.GEMINI_MODEL}, project={config.GEMINI_PROJECT or '-'}): {(resp.text or '').strip()[:20]}",
              flush=True)

    @property
    def available(self) -> bool:
        return self._client is not None

    def decide(self, state: dict) -> dict:
        """Return a guardrailed decision dict. Never raises."""
        if not self._client:
            return default_decision()
        try:
            prompt = self._build_prompt(state)
            resp = self._client.models.generate_content(
                model=config.GEMINI_MODEL,
                contents=prompt,
                config={"response_mime_type": "application/json", "temperature": 0.2},
            )
            raw = json.loads(resp.text)
            return self._guardrail(raw)
        except Exception as e:
            print(f"[strategist] decide failed, using defaults: {e}", flush=True)
            return default_decision()

    # ── prompt ───────────────────────────────────────────────────────────
    def _build_prompt(self, state: dict) -> str:
        return (
            "You are the strategist for a profit-first market-making bot on DreamDEX, "
            "a zero-fee on-chain order book. Contest ranking is "
            "Effective Volume = Raw Volume * (1 + PnL%); a wiped balance scores 0. "
            "Makers also earn ~3.3% APY yield weighted by closeness to mid, and only "
            "while quoting BOTH sides. Capital is fixed at $150 with no top-ups; a "
            f"${config.RESERVE_USDSO:.0f} USDso reserve is OFF-LIMITS. Eligible pairs: "
            f"{config.ELIGIBLE_PAIRS}.\n\n"
            "Decide which pairs to quote and per-pair parameters to maximize PnL-weighted "
            "volume without risking the balance. Favor tighter spreads on liquid, "
            "low-volatility pairs (more yield + fills); widen or pause on volatile or "
            "adversely-trending pairs to protect PnL.\n\n"
            f"State:\n{json.dumps(state, default=str, indent=2)}\n\n"
            "Respond with ONLY this JSON shape:\n"
            "{\n"
            '  "active_pairs": ["WETH:USDso", ...],\n'
            '  "per_pair": {"WETH:USDso": {"spread_mult": 1.0, "leg_usd": 12.0, '
            '"max_inv_usd": 40.0, "pause": false}, ...},\n'
            '  "rationale": "one sentence"\n'
            "}"
        )

    # ── guardrails ─────────────────────────────────────────────────────────
    def _guardrail(self, raw: dict) -> dict:
        out = default_decision()
        active = raw.get("active_pairs")
        if isinstance(active, list):
            valid = [p for p in active if p in config.ELIGIBLE_PAIRS]
            if valid:
                out["active_pairs"] = valid
        per = raw.get("per_pair", {})
        for p in config.ELIGIBLE_PAIRS:
            cfg = out["per_pair"][p]
            r = per.get(p, {}) if isinstance(per, dict) else {}
            cfg["spread_mult"] = _clamp(r.get("spread_mult", 1.0), *_SPREAD_MULT_RANGE)
            cfg["leg_usd"] = _clamp(r.get("leg_usd", config.MAKER_LEG_USD), *_LEG_USD_RANGE)
            cfg["max_inv_usd"] = _clamp(r.get("max_inv_usd", config.MAKER_MAX_INV_USD), *_MAX_INV_RANGE)
            cfg["pause"] = bool(r.get("pause", False))
        out["rationale"] = str(raw.get("rationale", ""))[:300]
        return out


def _clamp(v, lo, hi):
    try:
        return max(lo, min(hi, float(v)))
    except (TypeError, ValueError):
        return lo
