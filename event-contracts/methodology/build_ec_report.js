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
const H2=(t)=>new Paragraph({spacing:{before:220,after:90},children:[new TextRun({text:t,bold:true,size:24,color:ACCENT,font:"Calibri"})]});
const run=(t,o={})=>new TextRun({text:t,size:20,color:INK,font:"Calibri",...o});
const mono=(t,o={})=>new TextRun({text:t,size:15,font:"Consolas",color:"3A3A50",...o});
const P=(runs,opts={})=>new Paragraph({spacing:{after:120,line:276},...opts,children:(Array.isArray(runs)?runs:[runs]).map(r=>typeof r==="string"?run(r):r)});
const bullet=(runs)=>new Paragraph({numbering:undefined,bullet:{level:0},spacing:{after:90,line:270},children:(Array.isArray(runs)?runs:[runs]).map(r=>typeof r==="string"?run(r):r)});
const numItem=(n,runs)=>new Paragraph({spacing:{after:100,line:272},children:[new TextRun({text:n+"  ",bold:true,color:ACCENT,size:20,font:"Calibri"}),...(Array.isArray(runs)?runs:[runs]).map(r=>typeof r==="string"?run(r):r)]});
const codeBlock=(text)=>new Paragraph({spacing:{before:60,after:140},shading:{type:ShadingType.CLEAR,fill:CODEBG,color:"auto"},border:{left:{color:ACCENT,size:18,style:BorderStyle.SINGLE,space:8},top:{color:RULE,size:6,style:BorderStyle.SINGLE,space:4},bottom:{color:RULE,size:6,style:BorderStyle.SINGLE,space:4},right:{color:RULE,size:6,style:BorderStyle.SINGLE,space:4}},children:text.split("\n").flatMap((ln,i)=>i===0?[mono(ln)]:[new TextRun({break:1}),mono(ln)])});

const noBorder={top:{style:BorderStyle.NONE},bottom:{style:BorderStyle.NONE},left:{style:BorderStyle.NONE},right:{style:BorderStyle.NONE},insideHorizontal:{style:BorderStyle.SINGLE,color:RULE,size:6},insideVertical:{style:BorderStyle.SINGLE,color:RULE,size:6}};
function tbl(widths, head, rows){
  const cell=(children,{header=false,w,fill}={})=>new TableCell({width:w?{size:w,type:WidthType.PERCENTAGE}:undefined,margins:{top:60,bottom:60,left:90,right:90},shading:(header||fill)?{type:ShadingType.CLEAR,fill:header?HEADBG:fill,color:"auto"}:undefined,children});
  const headRow=new TableRow({tableHeader:true,children:head.map((h,i)=>cell([new Paragraph({children:[new TextRun({text:h,bold:true,size:16,color:"FFFFFF",font:"Calibri"})]})],{header:true,w:widths[i]}))});
  const bodyRows=rows.map(r=>new TableRow({children:r.map((c,i)=>cell([new Paragraph({spacing:{after:0},children:Array.isArray(c)?c:[c]})],{w:widths[i]}))}));
  return new Table({width:{size:100,type:WidthType.PERCENTAGE},borders:noBorder,rows:[headRow,...bodyRows]});
}
const status=(sym,color)=>[new TextRun({text:sym,bold:true,size:18,color,font:"Calibri"})];
function shot(file,caption,w=520){
  const data=fs.readFileSync(path.join(BASE,"evidence",file));
  return [
    new Paragraph({spacing:{before:100,after:30},alignment:AlignmentType.CENTER,children:[new ImageRun({type:"jpg",data,transformation:{width:w,height:Math.round(w*834/1456)}})]}),
    new Paragraph({spacing:{after:150},alignment:AlignmentType.CENTER,children:[new TextRun({text:caption,italics:true,size:16,color:MUT,font:"Calibri"})]}),
  ];
}

// matrix rows: [num, area, test, statusSym, statusColor, evidence]
const M=[
 ["1","discovery","Market discovery from chain logs (no indexer)","✔ Yes",GREEN,"12 events; BTC+ETH; 15m/1h/4h"],
 ["2","read","Market status on-chain (getMarketOnchain)","✔ Yes",GREEN,"status/finalized/winner"],
 ["3","read","Order book on-chain (getAllOpenOrdersOnchain)","✔ Yes",GREEN,"3 bids + 3 asks"],
 ["4","read","Outcome balances on-chain (getOutcomeBalance)","✔ Yes",GREEN,"ERC-6909 Up/Down"],
 ["5","helper","probabilityToPrice / priceToProbability","✔ Yes",GREEN,"0.62→620000→0.62"],
 ["6","write","faucet TestUSDC","✔ Yes",GREEN,"0xeab04b…54b1"],
 ["7","write","mintSet — 1 collateral → 1 Up + 1 Down","✔ Yes",GREEN,"ΔUp+6 ΔDown+6"],
 ["8","write","burnSet — merge Up + Down → collateral","✔ Yes",GREEN,"ΔUp−2 ΔDown−2"],
 ["9","write","Maker order rests (PostOnly SELL Up)","✔ Yes",GREEN,"orderId returned"],
 ["10","read","Own resting orders on-chain","✔ Yes",GREEN,"1 resting"],
 ["11","write","Taker order fills (IOC BUY Up crosses)","✔ Yes",GREEN,"filled 1 @ 0.368"],
 ["12","write","cancelOrder (cancel resting maker)","✔ Yes",GREEN,"0xf0e282…3afe"],
 ["13","write","Settle → redeem winner after resolution","✔ Yes",GREEN,"Down won; 10→10 USDC 0xbfd0c9…"],
 ["14","bug","PostOnly crossing order rejected SILENTLY","⚠ Repro",AMBER,"success + orderId undefined"],
 ["15","packaging","import under native Node (node bot.mjs)","✘ No",RED,"ERR_MODULE_NOT_FOUND"],
 ["16","indexer","loadMarkets / unified createOrder","✘ Down",RED,"indexer HTTP 000"],
 ["17","indexer","listBinaryMarkets (indexer discovery)","✘ Down",RED,"indexer HTTP 000"],
];

const doc=new Document({
  styles:{default:{document:{run:{font:"Calibri",size:20,color:INK}}}},
  sections:[{
    properties:{page:{margin:{top:1000,bottom:1000,left:1000,right:1000}}},
    children:[
      new Paragraph({spacing:{after:40},children:[new TextRun({text:"DreamDEX",bold:true,size:26,color:ACCENT,font:"Calibri"})]}),
      new Paragraph({spacing:{after:60},children:[new TextRun({text:"Event Contracts — Test Report",bold:true,size:40,color:INK,font:"Calibri"})]}),
      new Paragraph({spacing:{after:60},border:{bottom:{color:RULE,size:8,style:BorderStyle.SINGLE,space:6}},children:[new TextRun({text:"Hands-on test of docs, SDK, app UI, and the full on-chain trading path. Retested 2026-08-11 · Somnia Shannon testnet · SDK 0.25.0. Every matrix row was executed live and recorded.",size:19,color:MUT,italics:true,font:"Calibri"})]}),

      new Paragraph({spacing:{before:140,after:120},shading:{type:ShadingType.CLEAR,fill:"FFF6E9",color:"auto"},border:{left:{color:AMBER,size:18,style:BorderStyle.SINGLE,space:8},top:{color:"F0D9B5",size:6,style:BorderStyle.SINGLE,space:4},bottom:{color:"F0D9B5",size:6,style:BorderStyle.SINGLE,space:4},right:{color:"F0D9B5",size:6,style:BorderStyle.SINGLE,space:4}},children:[new TextRun({text:"Indexer note: ",bold:true,size:19,color:INK,font:"Calibri"}),new TextRun({text:"during this test the SDK's documented testnet indexer was unreachable (HTTP 000) while the app and RPC were up. Indexer-backed reads fail here; discovery was done from chain logs. This is a real availability finding, not a venue failure — the venue trades fine (matrix below).",size:19,color:INK,font:"Calibri"})]}),

      H1("Test matrix — what we tested and whether it works"),
      tbl([5,13,42,15,25],
        ["#","Area","What we tested","Works?","Evidence"],
        M.map(r=>[run(r[0]),run(r[1],{color:MUT}),run(r[2]),status(r[3],r[4]),mono(r[5])])),
      P([run("Programmatic matrix (rows 1–12, 14) passed ",{}),run("13/15 in one automated run",{bold:true}),run(" (methodology/matrix.mjs). The two non-passes are both the indexer outage. Legend: ✔ works · ⏳ pending settlement · ⚠ works-but-a-bug · ✘ broken/unavailable.",{})],{spacing:{before:120,after:60}}),

      H1("UI issues from the first review — now FIXED"),
      P("The app was updated since the first review; every UI issue reported is resolved. Verified live (before → after screenshots below)."),
      tbl([46,34,20],
        ["Issue (first review)","Status now","Evidence"],
        [
         [run("Selector showed \u201cNo event markets\u201d"),[run("Fixed",{bold:true,color:GREEN}),run(" — BTC market loads")],mono("01 → RETEST-01")],
         [run("Chart plotted SOMI on an ETH/BTC page"),[run("Fixed",{bold:true,color:GREEN}),run(" — BTC price + strike line")],mono("02 → RETEST-02")],
         [run("Both sides \u201cno liquidity\u201d"),[run("Fixed",{bold:true,color:GREEN}),run(" — full book, spread 3\u00a2")],mono("03 → RETEST-02")],
         [run("\u201cMax: -- USDso\u201d wouldn\u2019t compute"),[run("Fixed",{bold:true,color:GREEN}),run(" — Max 1.01 USDso")],mono("RETEST-01")],
         [run("Asset never labelled"),[run("Fixed",{bold:true,color:GREEN}),run(" — \u201cBTC\u201d + full question")],mono("RETEST-01")],
         [run("Balance showed 1.02 trillion USDso"),[run("Fixed",{bold:true,color:GREEN}),run(" — now 1.02 (decimals bug)")],mono("RETEST-01")],
        ]),
      ...shot("after/RETEST-02-orderbook-and-chart-working.jpg","AFTER: order book (bids 70–72\u00a2 / asks 75–76\u00a2, spread 3\u00a2), BTC chart with strike line and Up/Down zones, clear question."),
      ...shot("before/03-no-liquidity-while-onchain-has-book.jpg","BEFORE: \u201cNo event markets\u201d + \u201cno liquidity\u201d, shown while an on-chain book existed."),

      H1("Open issues (still worth fixing)"),
      numItem("1.",[run("SDK won\u2019t import under native Node. ",{bold:true}),run("\u201ctype\u201d:\u201dmodule\u201d + extensionless relative imports \u2192 node bot.mjs fails with ERR_MODULE_NOT_FOUND. Runs only via a bundler or tsx.")]),
      numItem("2.",[run("Documented testnet indexer is unreliable. ",{bold:true}),run("Fully down during this test (HTTP 000) while app + RPC were up; a raw-IP nip.io URL with no stable DNS. Breaks loadMarkets, listBinaryMarkets, unified createOrder, getOutcomeBalances.")]),
      numItem("3.",[run("PostOnly rejection is silent. ",{bold:true}),run("A crossing PostOnly order returns success with orderId undefined, fills 0, and no error \u2014 detectable only by null-checking orderId.")]),
      numItem("4.",[run("Docs mismatch on getOutcomeBalance. ",{bold:true}),run("README shows positional (token, address, id); the real signature is an object { outcomeToken, account, id }. The plural getOutcomeBalances reads the (down) indexer; the singular reads on-chain.")]),
      numItem("5.",[run("Carried over: ",{bold:true}),run("getBinaryOrderBook wants a pool address (not marketId); the README binary example uses a fixed-strike symbol that finds nothing; tick/lot grid unpublished; window list says 15m/1h but 4h exists.")]),
      P([run("Credit: ",{bold:true}),run("the raw trader.placeOrder correctly throws on revert (it replays to recover the reason) \u2014 the \u201creverts don\u2019t throw\u201d caveat applies only to the higher-level unified verbs.")],{spacing:{before:60}}),

      H1("Fresh transactions from this retest"),
      P("Full trade re-run on BTC-60min (marketId 0x…3f04), RPC-only (indexer bypassed):"),
      codeBlock(
        "faucet                 0xcfaddaa5de777bda5887a4089be15e6665c5940c02944108fef4b18d01f9a062\n"+
        "mintSet                0xa80c0949cc4498613aa5a69c553371dc8a5c90c6376c432b01448ff5f8fdb6ab\n"+
        "maker rest (SELL Up)   0xf211d5aad05af43a189210a62149de841fd5a594f1677eaa256940ef7d95153c\n"+
        "taker fill (BUY Up, 2) 0x9e5f9c286247af357ae8928e63bf8ca8f980285555ecf4cdba12f0472d1120b9\n"+
        "settle → redeem       0xbfd0c93c44bef14b36657e7194ada9225befc2b58d9e307561f257fa3d0a8c24  (Down won, 10→10 USDC)"),
      P([run("The maker on the book (0x789f…) quoted a two-sided market around 50/50 with a ~0.03 spread \u2014 tighter than the maker in the first review. ",{}),run("Bottom line: the team fixed the UI issues; the venue trades end-to-end; the remaining gaps are SDK packaging and an unreliable documented indexer.",{bold:true})]),
    ],
  }],
});
Packer.toBuffer(doc).then(b=>{fs.writeFileSync(OUT,b);console.log("WROTE",OUT,b.length,"bytes");});
