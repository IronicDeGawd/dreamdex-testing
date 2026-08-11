import { SomniaMarkets, SOMNIA_TESTNET_ADDRESSES as A } from "@somnia-chain/markets-sdk";
import { somniaTestnet } from "viem/chains";
import { readFileSync, writeFileSync } from "fs";

const pk = readFileSync("./bot.key", "utf8").trim();
const me = (await import("viem/accounts")).privateKeyToAccount(pk).address;
const MID = "0x0000000000000000000000000000000000000000000000000000000000003f04";
const RESULT = "/Users/adityasrivastava/Project/Somniaforge/Dreamdex-Contest+Smartwatch/event-contracts/methodology/redeem-result-retest.json";
const ex = new SomniaMarkets({ indexerUrl: "https://187.124.114.32.nip.io/v1/graphql", chain: somniaTestnet, wsRpcUrl: "wss://api.infra.testnet.somnia.network/ws", addresses: A, privateKey: pk });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const log = (...a) => console.log(new Date().toISOString(), ...a);
const D = 1000000n;

for (let i = 0; i < 220; i++) {
  let m;
  try { m = await ex.client.getMarketOnchain(MID); }
  catch (e) { log("read err", e.message.slice(0, 80)); await sleep(120000); continue; }
  log(`poll ${i}: status=${m.status} finalized=${m.finalized} resolved=${m.isResolved} voided=${m.isVoided} win=${m.winningOutcome}`);
  const ready = m.finalized || m.isResolved || m.isVoided;
  if (!ready) { await sleep(120000); continue; }

  const out = { marketId: MID, status: m.status, finalized: m.finalized, isResolved: m.isResolved, isVoided: m.isVoided, winningOutcome: String(m.winningOutcome), steps: [] };
  // free escrow from any resting orders
  try {
    const mine = await ex.client.getOwnOpenOrdersOnchain(m.pool, me);
    const ids = Array.isArray(mine) ? mine : (mine?.orders || []).map((o) => o.orderId);
    for (const id of ids) {
      try { const r = await ex.trader.cancelOrder({ pool: m.pool, orderId: BigInt(id) }); out.steps.push({ cancel: String(id), tx: r.hash, status: r.receipt?.status }); log("canceled", id); }
      catch (e) { out.steps.push({ cancel: String(id), err: e.message.slice(0, 120) }); }
    }
  } catch (e) { out.steps.push({ cancelPhase: e.message.slice(0, 120) }); }

  const doRedeem = async (idx, label) => {
    for (const amt of [12n * D, 10n * D, 7n * D, 5n * D, 2n * D, 1n * D]) {
      try { const r = await ex.trader.redeem({ marketId: MID, amount: amt, outcomeIdx: idx, market: m.marketAddress }); out.steps.push({ redeem: label, outcomeIdx: idx, amount: amt.toString(), tx: r.hash, status: r.receipt?.status }); log("REDEEMED", label, amt.toString(), r.hash); return true; }
      catch (e) { if (amt === 1n * D) out.steps.push({ redeem: label, err: e.message.slice(0, 140) }); }
    }
    return false;
  };
  const win = Number(m.winningOutcome);
  if (m.isVoided) { await doRedeem(0, "void-Up"); await doRedeem(1, "void-Down"); }
  else if (win === 0 || win === 1) { await doRedeem(win, win === 0 ? "Up-wins" : "Down-wins"); }
  else { out.steps.push({ note: "ready but winningOutcome unclear", win: String(m.winningOutcome) }); }

  writeFileSync(RESULT, JSON.stringify(out, null, 2));
  log("RESULT written", RESULT);
  process.exit(0);
}
writeFileSync(RESULT, JSON.stringify({ marketId: MID, note: "not finalized within watch window" }, null, 2));
process.exit(0);
