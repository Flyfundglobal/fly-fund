"use client";

import { useEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";
type Candle = { time: number; open: number; high: number; low: number; close: number; volume: number };
type Quote = { symbol: string; price: number; change: number; time: number };
type Market = { quotes: Quote[]; candles: Candle[]; fetchedAt: number; symbol: string; source: string };
type Point = [number, number];

// Project live DOM content onto each photographed CRT plane.
function screenTransform(width: number, height: number, points: Point[]) {
  const corners: Point[] = [[0, 0], [width, 0], [width, height], [0, height]];
  const rows: number[][] = [];
  corners.forEach(([x, y], i) => {
    const [u, v] = points[i];
    rows.push([x, y, 1, 0, 0, 0, -u*x, -u*y, u]);
    rows.push([0, 0, 0, x, y, 1, -v*x, -v*y, v]);
  });
  for (let col=0; col<8; col++) {
    let pivot=col;
    for(let r=col+1;r<8;r++) if(Math.abs(rows[r][col])>Math.abs(rows[pivot][col])) pivot=r;
    [rows[col],rows[pivot]]=[rows[pivot],rows[col]];
    const divisor=rows[col][col]; rows[col]=rows[col].map(n=>n/divisor);
    for(let r=0;r<8;r++) if(r!==col) { const factor=rows[r][col]; rows[r]=rows[r].map((n,c)=>n-factor*rows[col][c]); }
  }
  const [a,b,c,d,e,f,g,h]=rows.map(r=>r[8]);
  return `matrix3d(${a},${d},0,${g},${b},${e},0,${h},0,0,1,0,${c},${f},0,1)`;
}
function Screen({ width, height, points, children, className="" }: { width:number; height:number; points:Point[]; children:ReactNode; className?:string }) {
  return <div className={`crt-screen ${className}`} style={{width,height,transform:screenTransform(width,height,points)}}>{children}</div>;
}
const price = (n: number) => n.toLocaleString("en-US", { minimumFractionDigits:2, maximumFractionDigits:2 });
function Candles({ bars, symbol }: { bars:Candle[]; symbol:string }) {
  if (!bars.length) return <div className="screen-loading">Connecting to market…</div>;
  const low=Math.min(...bars.map(b=>b.low)),high=Math.max(...bars.map(b=>b.high));
  const pad=(high-low)*.12 || 1, min=low-pad,max=high+pad;
  const y=(n:number)=>12+(max-n)/(max-min)*174;
  const vol=Math.max(...bars.map(b=>b.volume));
  return <svg className="candles" viewBox="0 0 360 230" role="img" aria-label={`${symbol} real market candlestick chart, five minute candles`}>
    {[0,1,2,3,4].map(i=><g key={i}><line x1="0" y1={14+i*43} x2="305" y2={14+i*43} stroke="#163a3e" strokeDasharray="2 4"/><text x="309" y={18+i*43} fill="#68928f" fontSize="10">{(max-(max-min)*i/4).toFixed(symbol==="BTC"?0:1)}</text></g>)}
    {bars.map((b,i)=>{const x=5+i*6.55,color=b.close>=b.open?"#46e2a1":"#f0807c";return <g key={b.time}><line x1={x+2} x2={x+2} y1={y(b.high)} y2={y(b.low)} stroke={color}/><rect x={x} y={Math.min(y(b.open),y(b.close))} width="4" height={Math.max(1.5,Math.abs(y(b.open)-y(b.close)))} fill={color}/><rect x={x} y={222-b.volume/vol*26} width="4" height={b.volume/vol*26} fill={color} opacity=".4"/></g>})}
    <line x1="0" x2="304" y1={y(bars[bars.length-1].close)} y2={y(bars[bars.length-1].close)} stroke="#deb85c" strokeDasharray="3 3"/>
    <text x="3" y="230" fill="#7d9b99" fontSize="9">5 MIN · BINANCE SPOT</text>
  </svg>;
}

export default function TradingRoom({ onFeed, onBooks, onRoadmap, eating }: { onFeed:()=>void; onBooks:()=>void; onRoadmap:()=>void; eating:boolean }) {
  const room=useRef<HTMLDivElement>(null);
  const [scale,setScale]=useState(1);
  const [symbol,setSymbol]=useState("BNB");
  const [market,setMarket]=useState<Market|null>(null);
  const [connection,setConnection]=useState<"connecting"|"live"|"stale">("connecting");
  const [smoking,setSmoking]=useState(false);
  const [zoom,setZoom]=useState(false);
  const smokeTimer=useRef<ReturnType<typeof setTimeout>|null>(null);
  useEffect(()=>{
    const node=room.current;if(!node)return;
    const observer=new ResizeObserver(([entry])=>{
      const {width,height}=entry.contentRect;
      setScale(width<700?width/1672:Math.max(width/1672,height/941));
    });observer.observe(node);return()=>observer.disconnect();
  },[]);
  useEffect(()=>{
    let stopped=false,timer:ReturnType<typeof setTimeout>;
    const controller=new AbortController();
    async function update(){
      if(document.hidden){timer=setTimeout(update,8000);return;}
      try{
        const response=await fetch(`/api/market?symbol=${symbol}USDT`,{signal:controller.signal});
        if(!response.ok)throw new Error("offline");
        const value:Market=await response.json();
        if(!stopped){setMarket(value);setConnection(Date.now()-Math.min(...value.quotes.map(q=>q.time))>120000?"stale":"live");}
      }catch{if(!stopped)setConnection("stale");}
      if(!stopped)timer=setTimeout(update,8000);
    }
    update();return()=>{stopped=true;controller.abort();clearTimeout(timer);};
  },[symbol]);
  useEffect(()=>()=>{if(smokeTimer.current)clearTimeout(smokeTimer.current);},[]);
  const quote=market?.quotes.find(q=>q.symbol===symbol);
  const status=connection==="live"?"LIVE · 8s":connection==="stale"?"RECONNECTING":"CONNECTING";
  function chooseSymbol(next:string){
    if(next===symbol)return;
    setConnection("connecting");
    setSymbol(next);
  }
  function light(){
    if(smokeTimer.current)clearTimeout(smokeTimer.current);
    setSmoking(current=>{if(!current)smokeTimer.current=setTimeout(()=>setSmoking(false),14000);return !current;});
  }
  const watchlist=<div className="watchlist"><div className="quote-columns"><span>Asset</span><span>USDT</span><span>24h</span></div>{["FLY","BNB","BNCB","BTC","ETH"].map(ticker=>{
    const q=market?.quotes.find(v=>v.symbol===ticker);
    return <button key={ticker} className={`quote-row ${symbol===ticker?"selected-quote":""}`} disabled={!q} onClick={()=>chooseSymbol(ticker)} aria-label={q?`Show ${ticker} chart`:`${ticker} ${ticker==="FLY"?"not launched":"awaiting verified contract"}`}><b>{ticker}</b><span key={q?.price} className={q?"quote-value":"quote-pending"}>{q?price(q.price):ticker==="FLY"?"PRE-LAUNCH":"CA PENDING"}</span><span className={q?(q.change>=0?"market-up":"market-down"):"quote-pending"}>{q?`${q.change>=0?"+":""}${q.change.toFixed(2)}%`:"—"}</span></button>;
  })}<div className="market-source"><span>{status}</span><span>{market?new Date(market.fetchedAt).toLocaleTimeString("en-GB"):"--:--:--"}</span></div></div>;
  const chart=<><div className="chart-tabs">{["BNB","BTC","ETH"].map(t=><button key={t} onClick={()=>chooseSymbol(t)} className={symbol===t?"on":""}>{t}</button>)}<span>5m</span></div><div className="chart-price">{symbol}/USDT <b>{quote?price(quote.price):"—"}</b></div><Candles bars={market?.symbol===symbol?market.candles:[]} symbol={symbol}/></>;
  return <>
    <div ref={room} className={`trading-room ${smoking?"is-smoking":""} ${eating?"room-fed":""}`}>
      <div className="room-canvas" style={{"--scene-scale":scale} as CSSProperties}>
        <img className="room-art" src="/trading-room-v9-maodie-reference.webp" width={1672} height={941} fetchPriority="high" alt="A fruit fly trading room with crypto meme decorations, Maodie the cat and a Binance-themed tower outside" draggable="false"/>
        <Screen width={390} height={310} points={[[421,156],[716,107],[718,341],[426,402]]} className="chart-crt"><div className="crt-title">FLY FUND <span>▁ □ ×</span></div>{chart}</Screen>
        <Screen width={390} height={290} points={[[779,100],[1106,114],[1103,373],[779,348]]} className="quotes-crt"><div className="crt-title">MARKET WATCH <span>▁ □ ×</span></div>{watchlist}</Screen>
        <Screen width={310} height={285} points={[[1163,182],[1394,237],[1372,506],[1148,435]]} className="fund-crt"><div className="fund-terminal roadmap-terminal"><h2>ROADMAP</h2><p className="roadmap-kicker">FLY FUND / BNB CHAIN</p><ol>{[
          ["01", "FEED & RECORD", "IN DEVELOPMENT"],
          ["02", "MODEL & MEMORY", "PLANNED"],
          ["03", "ON-CHAIN ACTIONS", "PLANNED"],
          ["04", "YOUR OWN FLY", "PERSONAL FLY AIRDROP"],
        ].map(([step,title,status])=><li key={step}><span>{step}</span><div><b>{title}</b><small>{status}</small></div></li>)}</ol><button onClick={onRoadmap}>[ OPEN ROADMAP ]</button></div></Screen>
        <img className="fly-foreground" src="/trading-room-v9-maodie-reference.webp" alt="" aria-hidden="true"/>
        <button className="scene-hotspot fly-hotspot" aria-label="Feed the seated fly" onClick={onFeed}><span>Feed the fly</span></button>
        <button className="scene-hotspot lighter-hotspot" aria-label={smoking?"Put out cigarette":"Light a cigarette"} onClick={light}><span>{smoking?"Back to the charts":"Light a cigarette"}</span></button>
        <button className="scene-hotspot books-hotspot" aria-label="Explore the crypto books" onClick={onBooks}><span>The fly’s reading list</span></button>
        <button className="scene-hotspot founders-books-hotspot" aria-label="Explore the CZ, SBF and Justin Sun books" onClick={onBooks}><span>CZ, SBF & Justin Sun</span></button>
        <button className="screen-zoom" onClick={()=>setZoom(true)} aria-label="Enlarge live prices">↗</button>
        <div className="smoking-effect" aria-hidden="true">{smoking&&<img className="smoking-cigarette" src="/trading-room-smoking-source.webp" alt=""/>}<div className="smoke-puffs">{[0,1,2,3,4,5].map(i=><span key={i} style={{"--puff":i} as CSSProperties}>░</span>)}</div></div>
        <div className="desk-feed-hint"><span>{eating?"ALPHA RECEIVED.":smoking?"Touch grass.":"GM. WAGMI."}</span><button onClick={onFeed}>Feed $FLY</button></div>
      </div>
      <div className="mobile-market"><div className="mobile-market-title">MARKET WATCH <button onClick={()=>setZoom(true)}>Expand ↗</button></div>{watchlist}<div className="mobile-room-actions"><button onClick={onFeed}>Feed $FLY</button><button onClick={light}>{smoking?"Put it out":"Smoke break"}</button><button onClick={onBooks}>Books</button></div></div>
    </div>
    {zoom&&<section className="market-expanded app-window focused" role="dialog" aria-label="Live market terminal" onKeyDown={e=>{if(e.key==="Escape")setZoom(false);}}><header className="titlebar"><span className="window-title">Market Watch · Binance spot</span><button autoFocus aria-label="Close live market terminal" onClick={()=>setZoom(false)}>×</button></header><div className="expanded-market-content">{watchlist}{chart}<a href="https://github.com/binance/binance-spot-api-docs/blob/master/faqs/market_data_only.md" target="_blank" rel="noreferrer">Market data source ↗</a></div></section>}
  </>;
}
