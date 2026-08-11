import { marketCreatorEventsAbi } from "./node_modules/@somnia-chain/markets-sdk/dist/eventsAbi.js";
import { createPublicClient, http } from "viem";
import { somniaTestnet } from "viem/chains";
const pub = createPublicClient({ chain: somniaTestnet, transport: http() });
const mc = marketCreatorEventsAbi.find(e=>e.name==="MarketCreated");
const head = await pub.getBlockNumber();
const now = Math.floor(Date.now()/1000);
let found = [];
for (let i=0;i<40 && found.length<40;i++){
  const to = head - BigInt(i*1000); const from = to - 999n;
  try { const logs = await pub.getLogs({ event: mc, fromBlock: from, toBlock: to }); found.push(...logs.map(l=>l.args)); } catch(e){}
}
console.log("scanned ~40k blocks; MarketCreated found:", found.length);
const live = found.filter(a=>Number(a.expiry)>now+120).sort((x,y)=>Number(x.expiry)-Number(y.expiry));
for (const a of live) console.log(`  ${a.asset} ${Number(a.intervalSec)/60}min strike=${a.strike} expiresIn=${Number(a.expiry)-now}s pool=${a.pool} mid=${a.marketId.slice(0,10)}..`);
process.exit(0);
