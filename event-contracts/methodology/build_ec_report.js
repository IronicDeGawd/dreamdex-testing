const fs = require("fs");
const path = require("path");
const {
  Document, Packer, Paragraph, TextRun, AlignmentType,
  Table, TableRow, TableCell, WidthType, BorderStyle, ImageRun, ShadingType,
} = require("docx");

const BASE = "/Users/adityasrivastava/Project/Somniaforge/Dreamdex-Contest+Smartwatch/event-contracts";
const OUT = path.join(BASE, "DreamDEX-Event-Contracts-Report.docx");
const INK="1A1A2E", ACCENT="5B3DF5", MUT="6B7280", GREEN="0B7A46", RED="B42318", AMBER="B25E09", RULE="E4E4EC", CODEBG="F4F4F8", HEADBG="1A1A2E";

const H1=(t)=>new Paragraph({spacing:{before:340,after:140},border:{bottom:{color:ACCENT,size:14,style:BorderStyle.SINGLE,space:6}},children:[new TextRun({text:t,bold:true,size:30,color:INK,font:"Calibri"})]});
const run=(t,o={})=>new TextRun({text:t,size:20,color:INK,font:"Calibri",...o});
const mono=(t,o={})=>new TextRun({text:t,size:15,font:"Consolas",color:"3A3A50",...o});
const P=(runs,opts={})=>new Paragraph({spacing:{after:120,line:276},...opts,children:(Array.isArray(runs)?runs:[runs]).map(r=>typeof r==="string"?run(r):r)});
const bullet=(runs)=>new Paragraph({bullet:{level:0},spacing:{after:90,line:270},children:(Array.isArray(runs)?runs:[runs]).map(r=>typeof r==="string"?run(r):r)});
const codeBlock=(text)=>new Paragraph({spacing:{before:60,after:140},shading:{type:ShadingType.CLEAR,fill:CODEBG,color:"auto"},border:{left:{color:ACCENT,size:18,style:BorderStyle.SINGLE,space:8},top:{color:RULE,size:6,style:BorderStyle.SINGLE,space:4},bottom:{color:RULE,size:6,style:BorderStyle.SINGLE,space:4},right:{color:RULE,size:6,style:BorderStyle.SINGLE,space:4}},children:text.split("\n").flatMap((ln,i)=>i===0?[mono(ln)]:[new TextRun({break:1}),mono(ln)])});

const noBorder={top:{style:BorderStyle.NONE},bottom:{style:BorderStyle.NONE},left:{style:BorderStyle.NONE},right:{style:BorderStyle.NONE},insideHorizontal:{style:BorderStyle.SINGLE,color:RULE,size:6},insideVertical:{style:BorderStyle.SINGLE,color:RULE,size:6}};
function tbl(widths, head, rows){
  const cell=(children,{header=false,w}={})=>new TableCell({width:w?{size:w,type:WidthType.PERCENTAGE}:undefined,margins:{top:60,bottom:60,left:90,right:90},shading:header?{type:ShadingType.CLEAR,fill:HEADBG,color:"auto"}:undefined,children});
  const headRow=new TableRow({tableHeader:true,children:head.map((h,i)=>cell([new Paragraph({children:[new TextRun({text:h,bold:true,size:16,color:"FFFFFF",font:"Calibri"})]})],{header:true,w:widths[i]}))});
  const bodyRows=rows.map(r=>new TableRow({children:r.map((c,i)=>cell([new Paragraph({spacing:{after:0},children:Array.isArray(c)?c:[c]})],{w:widths[i]}))}));
  return new Table({width:{size:100,type:WidthType.PERCENTAGE},borders:noBorder,rows:[headRow,...bodyRows]});
}
const st=(sym,color)=>[new TextRun({text:sym,bold:true,size:17,color,font:"Calibri"})];
function shot(file,caption,w=520){
  const data=fs.readFileSync(path.join(BASE,"evidence",file));
  return [
    new Paragraph({spacing:{before:100,after:30},alignment:AlignmentType.CENTER,children:[new ImageRun({type:"jpg",data,transformation:{width:w,height:Math.round(w*834/1456)}})]}),
    new Paragraph({spacing:{after:150},alignment:AlignmentType.CENTER,children:[new TextRun({text:caption,italics:true,size:16,color:MUT,font:"Calibri"})]}),
  ];
}

// [num, area, feature, statusSym, color, evidence]
const M=[
 ["1","App UI","Event market loads and is selectable","✔ Working",GREEN,"working/RETEST-01"],
 ["2","App UI","Chart: correct asset + strike line + Up/Down zones","✔ Working",GREEN,"working/RETEST-02"],
 ["3","App UI","Order book renders (spread 3¢, last 58¢)","✔ Working",GREEN,"working/RETEST-02"],
 ["4","App UI","Max-stake, question/asset labels, balances","✔ Working",GREEN,"working/RETEST-01"],
 ["5","App UI","\u201cRecently published\u201d results feed","✔ Working",GREEN,"working/RETEST-01"],
 ["6","SDK read","Discovery from chain logs (no indexer)","✔ Working",GREEN,"12 events; BTC+ETH"],
 ["7","SDK read","Market status on-chain (getMarketOnchain)","✔ Working",GREEN,"status/finalized/winner"],
 ["8","SDK read","Order book on-chain (getAllOpenOrdersOnchain)","✔ Working",GREEN,"3 bids + 3 asks"],
 ["9","SDK read","Outcome balances on-chain (getOutcomeBalance)","✔ Working",GREEN,"ERC-6909 Up/Down"],
 ["10","SDK read","Own resting orders on-chain","✔ Working",GREEN,"1 resting"],
 ["11","SDK helper","probabilityToPrice / priceToProbability","✔ Working",GREEN,"0.62→620000→0.62"],
 ["12","Trade","faucet TestUSDC","✔ Working",GREEN,"0xeab04b…54b1"],
 ["13","Trade","mintSet — 1 collateral → 1 Up + 1 Down","✔ Working",GREEN,"ΔUp+6 ΔDown+6"],
 ["14","Trade","burnSet — merge Up + Down → collateral","✔ Working",GREEN,"ΔUp−2 ΔDown−2"],
 ["15","Trade","Maker order rests (PostOnly SELL Up)","✔ Working",GREEN,"orderId returned"],
 ["16","Trade","Taker order fills (IOC BUY Up crosses)","✔ Working",GREEN,"filled 1 @ 0.368"],
 ["17","Trade","cancelOrder (cancel resting maker)","✔ Working",GREEN,"0xf0e282…3afe"],
 ["18","Trade","Settle → redeem winner after resolution","✔ Working",GREEN,"Down won; 10→10 USDC"],
 ["19","SDK","PostOnly crossing order rejected SILENTLY","⚠ Issue",AMBER,"success + orderId undefined"],
 ["20","SDK","import under native Node (node bot.mjs)","✘ Not working",RED,"ERR_MODULE_NOT_FOUND"],
 ["21","SDK","loadMarkets / unified createOrder","✘ Not working",RED,"documented indexer HTTP 000"],
 ["22","SDK","listBinaryMarkets (indexer discovery)","✘ Not working",RED,"documented indexer HTTP 000"],
];

const doc=new Document({
  styles:{default:{document:{run:{font:"Calibri",size:20,color:INK}}}},
  sections:[{
    properties:{page:{margin:{top:1000,bottom:1000,left:1000,right:1000}}},
    children:[
      new Paragraph({spacing:{after:40},children:[new TextRun({text:"DreamDEX",bold:true,size:26,color:ACCENT,font:"Calibri"})]}),
      new Paragraph({spacing:{after:60},children:[new TextRun({text:"Event Contracts — Test Report",bold:true,size:40,color:INK,font:"Calibri"})]}),
      new Paragraph({spacing:{after:60},border:{bottom:{color:RULE,size:8,style:BorderStyle.SINGLE,space:6}},children:[new TextRun({text:"Hands-on test of docs, SDK, app UI, and the full on-chain trading path. Every tested feature listed as working / not working / issue. Tested 2026-08-11 · Somnia Shannon testnet · SDK 0.25.0.",size:19,color:MUT,italics:true,font:"Calibri"})]}),

      new Paragraph({spacing:{before:140,after:120},shading:{type:ShadingType.CLEAR,fill:"F1EEFF",color:"auto"},border:{left:{color:ACCENT,size:18,style:BorderStyle.SINGLE,space:8},top:{color:"D9D2Fb",size:6,style:BorderStyle.SINGLE,space:4},bottom:{color:"D9D2Fb",size:6,style:BorderStyle.SINGLE,space:4},right:{color:"D9D2Fb",size:6,style:BorderStyle.SINGLE,space:4}},children:[new TextRun({text:"Context: ",bold:true,size:19,color:INK,font:"Calibri"}),new TextRun({text:"at the first look the app\u2019s backend was down, so it couldn\u2019t load event markets \u2014 the empty selector, \u201cno liquidity\u201d, the wrong chart price and the odd balance were symptoms of that outage, not UI defects. This report retests every feature with the backend back up. Separately, the SDK\u2019s documented testnet indexer was unreachable during this test, so indexer-backed SDK calls fail here and discovery was done from chain logs (a distinct availability issue).",size:19,color:INK,font:"Calibri"})]}),

      H1("Test matrix — every feature we tested"),
      tbl([4,12,44,17,23],
        ["#","Area","Feature tested","Status","Evidence"],
        M.map(r=>[run(r[0]),run(r[1],{color:MUT}),run(r[2]),st(r[3],r[4]),mono(r[5])])),
      P([run("Legend: \u2714 working \u00b7 \u26a0 works but has an issue \u00b7 \u2718 not working during this test. The programmatic SDK/trade rows ran in one automated pass (methodology/matrix.mjs \u2192 matrix-result.json).",{})],{spacing:{before:120,after:60}}),

      H1("Working"),
      bullet([run("All app UI features. ",{bold:true}),run("With the backend up, the market loads, the chart shows the correct asset with the strike line and Up/Down zones, the order book renders with a live spread and last-traded price, max-stake and balances compute, the question/asset are labelled, and the results feed populates. These were simply unavailable during the backend outage at the first look (compare evidence/outage/ with evidence/working/).")]),
      bullet([run("The full trading path, on-chain. ",{bold:true}),run("faucet \u2192 mintSet \u2192 burnSet/merge \u2192 maker rest \u2192 taker fill \u2192 cancel \u2192 settle \u2192 redeem, all verified with real transactions. A maker (0x789f\u2026) quoted a two-sided book around 50/50 with a ~0.03 spread.")]),
      bullet([run("Indexer-free SDK reads. ",{bold:true}),run("Discovery from chain logs and getMarketOnchain / getAllOpenOrdersOnchain / getOutcomeBalance read directly from the RPC.")]),
      ...shot("working/RETEST-02-orderbook-and-chart-working.jpg","Backend up: order book (bids 70\u201372\u00a2 / asks 75\u201376\u00a2, spread 3\u00a2), BTC chart with strike line and Up/Down zones, clear question."),
      ...shot("outage/03-no-liquidity-while-onchain-has-book.jpg","During the backend outage: the app could not load markets (\u201cNo event markets\u201d / \u201cno liquidity\u201d)."),

      H1("Not working (during this test)"),
      bullet([run("SDK won\u2019t import under native Node. ",{bold:true}),run("\u201ctype\u201d:\u201dmodule\u201d + extensionless relative imports \u2192 node bot.mjs fails with ERR_MODULE_NOT_FOUND. Runs only via a bundler or tsx.")]),
      bullet([run("The SDK\u2019s documented testnet indexer was down \u2014 ",{bold:true}),run("HTTP 000 over many minutes while app + RPC were up; a raw-IP nip.io URL with no stable DNS. Breaks loadMarkets, listBinaryMarkets, unified createOrder, getOutcomeBalances. Chain-log discovery is a working fallback but isn\u2019t the documented path.")]),

      H1("Issues (works, but rough edges)"),
      bullet([run("PostOnly rejection is silent. ",{bold:true}),run("A crossing PostOnly order returns success with orderId undefined, fills 0, and no error \u2014 detectable only by null-checking orderId.")]),
      bullet([run("Docs mismatch on getOutcomeBalance. ",{bold:true}),run("README shows positional (token, address, id); the real signature is an object { outcomeToken, account, id }. The plural getOutcomeBalances reads the (down) indexer; the singular reads on-chain.")]),
      bullet([run("Carried over: ",{bold:true}),run("getBinaryOrderBook wants a pool address (not marketId); the README binary example uses a fixed-strike symbol that finds nothing; tick/lot grid unpublished; window list says 15m/1h but 4h exists.")]),
      P([run("Credit: ",{bold:true}),run("the raw trader.placeOrder correctly throws on revert (it replays to recover the reason) \u2014 the \u201creverts don\u2019t throw\u201d caveat applies only to the higher-level unified verbs.")],{spacing:{before:60}}),

      H1("Transactions from this test"),
      P("Full trade re-run on BTC-60min (marketId 0x…3f04), RPC-only (indexer bypassed):"),
      codeBlock(
        "faucet                 0xcfaddaa5de777bda5887a4089be15e6665c5940c02944108fef4b18d01f9a062\n"+
        "mintSet                0xa80c0949cc4498613aa5a69c553371dc8a5c90c6376c432b01448ff5f8fdb6ab\n"+
        "maker rest (SELL Up)   0xf211d5aad05af43a189210a62149de841fd5a594f1677eaa256940ef7d95153c\n"+
        "taker fill (BUY Up, 2) 0x9e5f9c286247af357ae8928e63bf8ca8f980285555ecf4cdba12f0472d1120b9\n"+
        "settle → redeem       0xbfd0c93c44bef14b36657e7194ada9225befc2b58d9e307561f257fa3d0a8c24  (Down won, 10→10 USDC)"),
      P([run("Bottom line: with the backend up, the app and the full trading path work end to end. What remains is SDK-side \u2014 the native-Node packaging and an unreliable documented indexer \u2014 plus the smaller issues above.",{bold:true})]),
    ],
  }],
});
Packer.toBuffer(doc).then(b=>{fs.writeFileSync(OUT,b);console.log("WROTE",OUT,b.length,"bytes");});
