import { marketCreatorEventsAbi } from "./node_modules/@somnia-chain/markets-sdk/dist/eventsAbi.js";
import { SomniaMarkets, SOMNIA_TESTNET_ADDRESSES as A } from "@somnia-chain/markets-sdk";
import { createPublicClient, http } from "viem";
import { somniaTestnet } from "viem/chains";
import { readFileSync } from "fs";
const pk = readFileSync("./bot.key","utf8").trim();
const me = (await import("viem/accounts")).privateKeyToAccount(pk).address;
const pub = createPublicClient({ chain: somniaTestnet, transport: http() });
const mc = marketCreatorEventsAbi.find(e=>e.name==="MarketCreated");
const head = await pub.getBlockNumber(); const now = Math.floor(Date.now()/1000);
let found=[]; for(let i=0;i<40;i++){const to=head-BigInt(i*1000);try{found.push(...(await pub.getLogs({event:mc,fromBlock:to-999n,toBlock:to})).map(l=>l.args));}catch(e){}}
// pick BTC ~60min with the most headroom
const cands = found.filter(a=>a.asset==="BTC" && Number(a.intervalSec)===3600 && Number(a.expiry)>now+300).sort((x,y)=>Number(y.expiry)-Number(x.expiry));
const m = cands[0];
console.log(`chosen: ${m.asset} ${Number(m.intervalSec)/60}min pool=${m.pool} marketId=${m.marketId} expiresIn=${Number(m.expiry)-now}s`);
const ex = new SomniaMarkets({ indexerUrl:"https://187.124.114.32.nip.io/v1/graphql", chain:somniaTestnet, wsRpcUrl:"wss://api.infra.testnet.somnia.network/ws", addresses:A, privateKey:pk });
const pool = m.pool, D = 1000000n;
const fmt = o => (o.orders||[]).map(x=>`${x.isBid?"B":"A"} ${(Number(x.price)/1e6).toFixed(3)}x${Number(x.quantityRemaining)/1e6} ${x.owner.slice(0,6)}`).join(" | ");
console.log("\n[book before]");
console.log("  bids:", fmt(await ex.client.getAllOpenOrdersOnchain(pool,{isBid:true})));
console.log("  asks:", fmt(await ex.client.getAllOpenOrdersOnchain(pool,{isBid:false})));
console.log("\n[1] faucet"); try{const r=await ex.trader.faucet();console.log("  ",r.hash,r.receipt?.status);}catch(e){console.log("  ERR",e.message.slice(0,120));}
console.log("[2] mintSet 10"); try{const r=await ex.trader.mintSet({pool,amount:10n*D});console.log("  ",r.hash,r.receipt?.status);}catch(e){console.log("  ERR",e.message.slice(0,160));}
console.log("[3] maker SELL Up @0.90 x5 (rest above book)"); try{const r=await ex.trader.placeOrder({pool,side:"SELL_YES",price:900000n,quantity:5n*D,orderType:3});console.log("  ",r.hash,"orderId",r.orderId,"status",r.receipt?.status);}catch(e){console.log("  ERR",e.message.slice(0,160));}
console.log("[4] taker BUY Up IOC px<=0.99 x2 (cross maker asks)"); try{const r=await ex.trader.placeOrder({pool,side:"BUY_YES",price:990000n,quantity:2n*D,orderType:2});console.log("  ",r.hash,"fills",(r.fills||[]).length,JSON.stringify(r.fills,(k,v)=>typeof v==="bigint"?v.toString():v).slice(0,300));}catch(e){console.log("  ERR",e.message.slice(0,180));}
console.log("\n[book after]");
console.log("  my orders:", JSON.stringify(await ex.client.getOwnOpenOrdersOnchain(pool,me),(k,v)=>typeof v==="bigint"?v.toString():v));
process.exit(0);
