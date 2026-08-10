import { SomniaMarkets, SOMNIA_TESTNET_ADDRESSES } from "@somnia-chain/markets-sdk";
import { somniaTestnet } from "viem/chains";
import { readFileSync } from "fs";
import { createPublicClient, http, formatEther } from "viem";

const pk = readFileSync("./bot.key","utf8").trim();
const pub = createPublicClient({ chain: somniaTestnet, transport: http() });
const me = (await import("viem/accounts")).privateKeyToAccount(pk).address;
console.log("BOT wallet:", me);
console.log("STT gas balance:", formatEther(await pub.getBalance({ address: me })));

const ex = new SomniaMarkets({
  indexerUrl: "https://187.124.114.32.nip.io/v1/graphql",
  chain: somniaTestnet, wsRpcUrl: "wss://api.infra.testnet.somnia.network/ws",
  addresses: SOMNIA_TESTNET_ADDRESSES, privateKey: pk,
});
await ex.loadMarkets();

// 1) faucet TestUSDC
console.log("\n[1] faucet TestUSDC...");
try { const r = await ex.trader.faucet(); console.log("   faucet tx:", r.hash, "status:", r.receipt?.status); }
catch(e){ console.log("   faucet ERR:", e.message.slice(0,160)); }

// 2) pick a live Trading market with the most headroom
const bins = await ex.client.listBinaryMarkets({});
const now = Math.floor(Date.now()/1000);
const trading = bins.filter(m=>m.status==="Trading" && Number(m.expiry) - now > 120)
                    .sort((a,b)=>Number(b.expiry)-Number(a.expiry));
const m = trading[0];
console.log(`\n[2] picked: ${m.asset} "${m.question}"`);
console.log(`   marketId=${m.marketId}`);
console.log(`   pool=${m.poolAddress}  decimals=${m.quoteDecimals}  expiresIn=${Number(m.expiry)-now}s`);
const D = 10n ** BigInt(m.quoteDecimals);
const pool = m.poolAddress;

// 3) mint a complete set: 10 collateral -> 10 YES + 10 NO
console.log("\n[3] mintSet 10 -> 10 Up + 10 Down...");
try { const r = await ex.trader.mintSet({ pool, amount: 10n*D }); console.log("   mint tx:", r.hash, "status:", r.receipt?.status); }
catch(e){ console.log("   mint ERR:", e.message.slice(0,200)); }

// 4) place resting maker orders (postOnly) — SELL YES @0.55, SELL NO @0.55
console.log("\n[4] place resting maker orders (PostOnly)...");
for (const [side,label] of [["SELL_YES","SELL Up @0.55 x5"],["SELL_NO","SELL Down @0.55 x5"]]) {
  try {
    const r = await ex.trader.placeOrder({ pool, side, price: 550_000n, quantity: 5n*D, orderType: 3 });
    console.log(`   ${label}: tx=${r.hash} orderId=${r.orderId} fills=${r.fills?.length ?? 0} status=${r.receipt?.status}`);
  } catch(e){ console.log(`   ${label} ERR:`, e.message.slice(0,180)); }
}

// 5) read the book / my orders back on-chain (proves the order landed & book now has liquidity)
console.log("\n[5] read back on-chain...");
try { const mine = await ex.client.getOwnOpenOrdersOnchain(pool, me); console.log("   my open orders on-chain:", JSON.stringify(mine,(k,v)=>typeof v==="bigint"?v.toString():v).slice(0,400)); }
catch(e){ console.log("   own-orders ERR:", e.message.slice(0,140)); }
try { const asks = await ex.client.getAllOpenOrdersOnchain(pool, { isBid:false }); console.log("   all ask-side orders now:", JSON.stringify(asks,(k,v)=>typeof v==="bigint"?v.toString():v).slice(0,400)); }
catch(e){ console.log("   all-orders ERR:", e.message.slice(0,140)); }

process.exit(0);
