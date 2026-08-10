import { SomniaMarkets, SOMNIA_TESTNET_ADDRESSES } from "@somnia-chain/markets-sdk";
import { somniaTestnet } from "viem/chains";
import { readFileSync, writeFileSync } from "fs";

const pk = readFileSync("./bot.key", "utf8").trim();
const acct = (await import("viem/accounts")).privateKeyToAccount(pk);
const me = acct.address;
const MARKET_ID = "0x0000000000000000000000000000000000000000000000000000000000003e04";
const RESULT = "/Users/adityasrivastava/Project/Somniaforge/Dreamdex-Contest+Smartwatch/event-contracts/methodology/redeem-result.json";

const ex = new SomniaMarkets({
  indexerUrl: "https://187.124.114.32.nip.io/v1/graphql",
  chain: somniaTestnet, wsRpcUrl: "wss://api.infra.testnet.somnia.network/ws",
  addresses: SOMNIA_TESTNET_ADDRESSES, privateKey: pk,
});

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const log = (...a) => console.log(new Date().toISOString(), ...a);

async function loadMkt() {
  await ex.loadMarkets();
  const bins = await ex.client.listBinaryMarkets({});
  return bins.find((m) => m.marketId === MARKET_ID);
}

let done = false;
for (let i = 0; i < 200 && !done; i++) {
  let m;
  try { m = await loadMkt(); } catch (e) { log("load err", e.message.slice(0, 80)); await sleep(120000); continue; }
  if (!m) { log("market not found yet"); await sleep(120000); continue; }
  log(`poll ${i}: status=${m.status} winningOutcome=${m.winningOutcome} voided=${m.voided}`);

  if (m.status === "Resolved" || m.status === "Finalized" || m.winningOutcome !== null && m.winningOutcome !== undefined) {
    const out = { market: MARKET_ID, status: m.status, winningOutcome: m.winningOutcome, voided: m.voided, steps: [] };
    // cancel any resting orders to free escrow
    try {
      const mine = await ex.client.getOwnOpenOrdersOnchain(m.poolAddress, me);
      const ids = Array.isArray(mine) ? mine : (mine?.orders || []).map((o) => o.orderId);
      for (const id of ids) {
        try { const r = await ex.trader.cancelOrder({ pool: m.poolAddress, orderId: BigInt(id) }); out.steps.push({ cancel: String(id), tx: r.hash, status: r.receipt?.status }); log("canceled", id, r.hash); }
        catch (e) { out.steps.push({ cancel: String(id), err: e.message.slice(0, 120) }); }
      }
    } catch (e) { out.steps.push({ cancelPhase: "err", err: e.message.slice(0, 120) }); }

    const win = (m.voided ? null : m.winningOutcome);
    const D = 10n ** BigInt(m.quoteDecimals);
    // redeem: if voided, redeem both sides at 0.5; else redeem winning side
    const tryRedeem = async (outcomeIdx, label) => {
      // redeem a generous amount; the module pulls only what we hold
      for (const amt of [13n * D, 10n * D, 5n * D, 3n * D, 1n * D]) {
        try {
          const r = await ex.trader.redeem({ marketId: MARKET_ID, amount: amt, outcomeIdx, market: m.marketAddress });
          out.steps.push({ redeem: label, outcomeIdx, amount: amt.toString(), tx: r.hash, status: r.receipt?.status });
          log("REDEEMED", label, "amt", amt.toString(), r.hash);
          return true;
        } catch (e) { /* try smaller */ if (amt === 1n * D) out.steps.push({ redeem: label, outcomeIdx, err: e.message.slice(0, 140) }); }
      }
      return false;
    };
    if (m.voided) { await tryRedeem(0, "void-Up"); await tryRedeem(1, "void-Down"); }
    else if (win === 0 || win === 1) { await tryRedeem(win, win === 0 ? "Up-wins" : "Down-wins"); }
    else { out.steps.push({ note: "no winningOutcome resolved yet despite status", status: m.status }); }

    writeFileSync(RESULT, JSON.stringify(out, null, 2));
    log("RESULT written:", RESULT);
    done = true;
    break;
  }
  await sleep(120000); // 2 min
}
if (!done) { log("gave up waiting for resolution"); writeFileSync(RESULT, JSON.stringify({ market: MARKET_ID, note: "not resolved within watch window" }, null, 2)); }
process.exit(0);
