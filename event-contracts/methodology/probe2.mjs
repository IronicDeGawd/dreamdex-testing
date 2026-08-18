import { SomniaMarkets, SOMNIA_TESTNET_ADDRESSES } from "@somnia-chain/markets-sdk";
import { somniaTestnet } from "viem/chains";
const ex = new SomniaMarkets({
  indexerUrl: "https://187.124.114.32.nip.io/v1/graphql",
  chain: somniaTestnet, wsRpcUrl: "wss://api.infra.testnet.somnia.network/ws",
  addresses: SOMNIA_TESTNET_ADDRESSES,
});
const bins = await ex.client.listBinaryMarkets({});
const trading = bins.filter(m => m.status === "Trading");
console.log("Trading markets:", trading.length);
const m = trading[0];
console.log("Inspecting:", JSON.stringify(Object.keys(m)));
console.log("  marketId:", m.marketId);
console.log("  symbol:", m.symbol);
console.log("  underlying/asset fields:", m.underlying, m.asset, m.baseSymbol, m.title, m.question);
console.log("  full sample:", JSON.stringify(m, (k,v)=>typeof v==="bigint"?v.toString():v).slice(0,900));
try {
  const ob = await ex.client.getBinaryOrderBook(m.marketId ?? m.pool, 5);
  console.log("  ORDERBOOK:", JSON.stringify(ob, (k,v)=>typeof v==="bigint"?v.toString():v).slice(0,500));
} catch(e){ console.log("  orderbook err:", e.message); }
process.exit(0);
