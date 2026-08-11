import { marketCreatorEventsAbi } from "./node_modules/@somnia-chain/markets-sdk/dist/eventsAbi.js";
import { SomniaMarkets, SOMNIA_TESTNET_ADDRESSES as A, probabilityToPrice, priceToProbability } from "@somnia-chain/markets-sdk";
import { createPublicClient, http } from "viem";
import { somniaTestnet } from "viem/chains";
import { readFileSync, writeFileSync } from "fs";

const pk = readFileSync("./bot.key", "utf8").trim();
const me = (await import("viem/accounts")).privateKeyToAccount(pk).address;
const pub = createPublicClient({ chain: somniaTestnet, transport: http() });
const ex = new SomniaMarkets({ indexerUrl: "https://187.124.114.32.nip.io/v1/graphql", chain: somniaTestnet, wsRpcUrl: "wss://api.infra.testnet.somnia.network/ws", addresses: A, privateKey: pk });
const D = 1000000n;
const R = [];
const rec = (name, ok, detail) => { R.push({ name, ok, detail }); console.log(`${ok ? "PASS" : "FAIL"}  ${name} — ${detail}`); };

// 1. on-chain discovery
const mc = marketCreatorEventsAbi.find(e => e.name === "MarketCreated");
const head = await pub.getBlockNumber(); const now = Math.floor(Date.now() / 1000);
let found = []; for (let i = 0; i < 40; i++) { const to = head - BigInt(i * 1000); try { found.push(...(await pub.getLogs({ event: mc, fromBlock: to - 999n, toBlock: to })).map(l => l.args)); } catch {} }
rec("Market discovery via chain logs (no indexer)", found.length > 0, `${found.length} MarketCreated events; ${new Set(found.map(f=>f.asset)).size} assets`);
// choose BTC 240min (max headroom, separate from the settling 60min)
const m = found.filter(a => a.asset === "BTC" && Number(a.intervalSec) === 14400 && Number(a.expiry) > now + 300).sort((x, y) => Number(y.expiry) - Number(x.expiry))[0];
const pool = m.pool, mid = m.marketId;

// 2. getMarketOnchain
let mo; try { mo = await ex.client.getMarketOnchain(mid); rec("Read market status on-chain (getMarketOnchain)", mo.status === 1, `status=${mo.status} finalized=${mo.finalized} decimals=${mo.decimals}`); } catch (e) { rec("Read market status on-chain", false, e.message.slice(0, 80)); }

// 3. order book on-chain
try { const b = await ex.client.getAllOpenOrdersOnchain(pool, { isBid: true }); const a = await ex.client.getAllOpenOrdersOnchain(pool, { isBid: false }); const nb = (b.orders || []).length, na = (a.orders || []).length; rec("Read order book on-chain (getAllOpenOrdersOnchain)", nb + na > 0, `${nb} bids, ${na} asks; makers=${new Set([...(b.orders||[]),...(a.orders||[])].map(o=>o.owner)).size}`); } catch (e) { rec("Read order book on-chain", false, e.message.slice(0, 80)); }

// 4. price helpers
try { const p = probabilityToPrice(0.62); const q = priceToProbability(p); rec("Price/probability helpers", Math.abs(q - 0.62) < 0.001, `0.62→${p}→${q}`); } catch (e) { rec("Price/probability helpers", false, e.message.slice(0, 80)); }

// 5. faucet
try { const r = await ex.trader.faucet(); rec("faucet TestUSDC (write)", r.receipt?.status === "success", r.hash); } catch (e) { rec("faucet TestUSDC", false, e.message.slice(0, 100)); }

// 6. balance before mint
const bal = async () => { const y = await ex.client.getOutcomeBalance({ outcomeToken: mo.outcomeToken, account: me, id: BigInt(mo.yesId) }); const n = await ex.client.getOutcomeBalance({ outcomeToken: mo.outcomeToken, account: me, id: BigInt(mo.noId) }); return [y, n]; };
let b0; try { b0 = await bal(); rec("Read outcome balances on-chain (getOutcomeBalance)", true, `Up=${Number(b0[0])/1e6} Down=${Number(b0[1])/1e6}`); } catch (e) { rec("Read outcome balances on-chain", false, e.message.slice(0, 80)); b0 = [0n, 0n]; }

// 7. mintSet
try { const r = await ex.trader.mintSet({ pool, amount: 6n * D }); const b1 = await bal(); rec("mintSet — 1 collateral → 1 Up + 1 Down", r.receipt?.status === "success" && b1[0] - b0[0] === 6n * D, `${r.hash} ΔUp=${Number(b1[0]-b0[0])/1e6} ΔDown=${Number(b1[1]-b0[1])/1e6}`); } catch (e) { rec("mintSet", false, e.message.slice(0, 100)); }

// 8. burnSet (merge)
try { const before = await bal(); const r = await ex.trader.burnSet({ pool, amount: 2n * D }); const after = await bal(); rec("burnSet — merge 1 Up + 1 Down → 1 collateral", r.receipt?.status === "success" && before[0] - after[0] === 2n * D, `${r.hash} ΔUp=${Number(after[0]-before[0])/1e6} ΔDown=${Number(after[1]-before[1])/1e6}`); } catch (e) { rec("burnSet (merge)", false, e.message.slice(0, 100)); }

// 9. maker rest (PostOnly)
let restId;
try { const r = await ex.trader.placeOrder({ pool, side: "SELL_YES", price: 900000n, quantity: 3n * D, orderType: 3 }); restId = r.orderId; rec("Maker order rests (PostOnly, SELL Up @0.90)", r.receipt?.status === "success" && !!r.orderId, `orderId=${r.orderId} fills=${r.fills?.length||0}`); } catch (e) { rec("Maker order rests (PostOnly)", false, e.message.slice(0, 100)); }

// 10. own orders on-chain
try { const mine = await ex.client.getOwnOpenOrdersOnchain(pool, me); const ids = Array.isArray(mine) ? mine : (mine.orders || []).map(o => o.orderId); rec("Read own resting orders on-chain", ids.length > 0, `${ids.length} resting`); } catch (e) { rec("Read own resting orders", false, e.message.slice(0, 80)); }

// 11. taker fill (IOC)
try { const r = await ex.trader.placeOrder({ pool, side: "BUY_YES", price: 990000n, quantity: 1n * D, orderType: 2 }); const f = (r.fills || [])[0]; rec("Taker order fills (IOC BUY Up crosses book)", (r.fills || []).length > 0, f ? `filled ${Number(f.quantityFilled)/1e6} @ ${Number(f.fillPrice)/1e6}` : "no fill"); } catch (e) { rec("Taker order fills (IOC)", false, e.message.slice(0, 100)); }

// 12. cancelOrder
try { if (restId) { const r = await ex.trader.cancelOrder({ pool, orderId: BigInt(restId) }); rec("cancelOrder (cancel resting maker)", r.receipt?.status === "success", r.hash); } else rec("cancelOrder", false, "no resting order to cancel"); } catch (e) { rec("cancelOrder", false, e.message.slice(0, 100)); }

// 13. PostOnly silent-reject repro (SELL Down @0.90 = buy Up @0.10, crosses → should not rest, returns success + no orderId)
try { const r = await ex.trader.placeOrder({ pool, side: "SELL_NO", price: 900000n, quantity: 1n * D, orderType: 3 }); rec("PostOnly crossing order rejected SILENTLY (bug)", r.receipt?.status === "success" && !r.orderId, `status=${r.receipt?.status} orderId=${r.orderId} (undefined+success = the silent reject)`); } catch (e) { rec("PostOnly crossing silent reject", false, "threw instead: " + e.message.slice(0, 60)); }

// 14. unified createOrder path (needs symbol/loadMarkets → indexer)
try { await ex.loadMarkets(); rec("Unified createOrder path (needs indexer snapshot)", true, "loadMarkets ok"); } catch (e) { rec("Unified path / loadMarkets (indexer)", false, "indexer down: " + e.message.slice(0, 60)); }

// 15. listBinaryMarkets (indexer)
try { const b = await ex.client.listBinaryMarkets({}); rec("listBinaryMarkets (indexer discovery)", b.length > 0, `${b.length} markets`); } catch (e) { rec("listBinaryMarkets (indexer discovery)", false, "indexer down: " + e.message.slice(0, 60)); }

writeFileSync("./matrix-result.json", JSON.stringify(R, null, 2));
console.log("\n=== SUMMARY:", R.filter(r => r.ok).length + "/" + R.length, "passed ===");
process.exit(0);
