import { SomniaMarkets, SOMNIA_TESTNET_ADDRESSES } from "@somnia-chain/markets-sdk";
import { somniaTestnet } from "viem/chains";
import { readFileSync } from "fs";
const pk = readFileSync("./bot.key","utf8").trim();
const me = (await import("viem/accounts")).privateKeyToAccount(pk).address;
const ex = new SomniaMarkets({
  indexerUrl: "https://187.124.114.32.nip.io/v1/graphql",
  chain: somniaTestnet, wsRpcUrl: "wss://api.infra.testnet.somnia.network/ws",
  addresses: SOMNIA_TESTNET_ADDRESSES, privateKey: pk,
});
await ex.loadMarkets();
const bins = await ex.client.listBinaryMarkets({});
const m = bins.find(x=>x.marketId==="0x0000000000000000000000000000000000000000000000000000000000003e04");
const pool = m.poolAddress;
console.log("market:", m.asset, "status:", m.status, "yesId:", m.yesTokenId, "noId:", m.noTokenId, "outcomeToken:", (m.outcomeToken||"n/a"));
// balances of my Up / Down tokens
try {
  const yes = await ex.client.getOutcomeBalance(m.outcomeToken ?? SOMNIA_TESTNET_ADDRESSES.outcomeToken, me, BigInt(m.yesTokenId));
  const no  = await ex.client.getOutcomeBalance(m.outcomeToken ?? SOMNIA_TESTNET_ADDRESSES.outcomeToken, me, BigInt(m.noTokenId));
  console.log("my Up balance:", yes.toString(), " my Down balance:", no.toString(), "(raw, 6dp)");
} catch(e){ console.log("balance ERR:", e.message.slice(0,160)); }
// full book, both sides
try {
  const bids = await ex.client.getAllOpenOrdersOnchain(pool, { isBid:true });
  const asks = await ex.client.getAllOpenOrdersOnchain(pool, { isBid:false });
  const fmt = a => (a.orders||a||[]).map(o=>`  #${String(o.orderId).slice(-6)} ${o.isBid?"BID":"ASK"} px=${(Number(o.price)/1e6).toFixed(3)} qty=${(Number(o.quantityRemaining)/1e6)} ud=${o.userData} own=${o.owner.slice(0,6)}`).join("\n");
  console.log("BIDS:\n"+fmt(bids));
  console.log("ASKS:\n"+fmt(asks));
} catch(e){ console.log("book ERR:", e.message.slice(0,160)); }
// my open orders
try { const mine = await ex.client.getOwnOpenOrdersOnchain(pool, me);
  console.log("my resting orderIds:", JSON.stringify(mine,(k,v)=>typeof v==="bigint"?v.toString():v)); } catch(e){ console.log("mine ERR:", e.message.slice(0,120)); }
process.exit(0);
