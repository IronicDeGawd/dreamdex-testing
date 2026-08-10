import { SomniaMarkets, SOMNIA_TESTNET_ADDRESSES } from "@somnia-chain/markets-sdk";
import { somniaTestnet } from "viem/chains";
const ex = new SomniaMarkets({
  indexerUrl: "https://187.124.114.32.nip.io/v1/graphql",
  chain: somniaTestnet, wsRpcUrl: "wss://api.infra.testnet.somnia.network/ws",
  addresses: SOMNIA_TESTNET_ADDRESSES,
});
await ex.loadMarkets();
const bins = await ex.client.listBinaryMarkets({});
const now = Math.floor(Date.now()/1000);
const trading = bins.filter(m=>m.status==="Trading").sort((a,b)=>Number(a.expiry-a.tradingStart)-Number(b.expiry-b.tradingStart));
for (const m of trading) {
  const win = Number(m.expiry)-Number(m.tradingStart);
  try {
    const bids = (await ex.client.getAllOpenOrdersOnchain(m.poolAddress,{isBid:true})).orders||[];
    const asks = (await ex.client.getAllOpenOrdersOnchain(m.poolAddress,{isBid:false})).orders||[];
    const makers = new Set([...bids,...asks].map(o=>o.owner));
    console.log(`${m.asset} ${win/60}min  expiresIn=${Number(m.expiry)-now}s  bids=${bids.length} asks=${asks.length} makers=${makers.size} trades=${m.tradeCount}`);
  } catch(e){ console.log(`${m.asset} ${win/60}min  book ERR ${e.message.slice(0,50)}`); }
}
process.exit(0);
