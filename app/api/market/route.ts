const symbols = ["BNBUSDT", "BTCUSDT", "ETHUSDT"];
const origin = "https://data-api.binance.vision";
type Snapshot = { quotes: { symbol: string; price: number; change: number; time: number }[]; candles: { time: number; open: number; high: number; low: number; close: number; volume: number }[]; fetchedAt: number; symbol: string; source: string };
const cache = new Map<string, { value: Snapshot; expires: number }>();
const pending = new Map<string, Promise<Snapshot>>();

async function json(path: string) {
  const response = await fetch(origin + path, { signal: AbortSignal.timeout(8000), cache: "no-store" });
  if (!response.ok) throw new Error("Market data unavailable");
  return response.json();
}

async function snapshot(symbol: string): Promise<Snapshot> {
  const [tickers, bars] = await Promise.all([
    json(`/api/v3/ticker/24hr?symbols=${encodeURIComponent(JSON.stringify(symbols))}&type=MINI`),
    json(`/api/v3/klines?symbol=${symbol}&interval=5m&limit=45`),
  ]);
  if (!Array.isArray(tickers) || !Array.isArray(bars) || tickers.length !== 3 || !bars.length) throw new Error("Invalid market data");
  const quotes = tickers.map((t: Record<string, string>) => ({ symbol: t.symbol.replace("USDT", ""), price: Number(t.lastPrice), change: (Number(t.lastPrice) / Number(t.openPrice) - 1) * 100, time: Number(t.closeTime) }));
  const candles = bars.map((b: (string | number)[]) => ({ time: Number(b[0]), open: Number(b[1]), high: Number(b[2]), low: Number(b[3]), close: Number(b[4]), volume: Number(b[5]) }));
  if (quotes.some(q => !Number.isFinite(q.price) || q.price <= 0 || !Number.isFinite(q.change)) || candles.some(c => !Number.isFinite(c.close) || c.close <= 0)) throw new Error("Invalid market data");
  return { quotes, candles, symbol: symbol.replace("USDT", ""), fetchedAt: Date.now(), source: "Binance spot · USDT" };
}

export async function GET(request: Request) {
  const symbol = new URL(request.url).searchParams.get("symbol") || "BNBUSDT";
  if (!symbols.includes(symbol)) return Response.json({ error: "Unsupported symbol" }, { status: 400 });
  try {
    let value = cache.get(symbol)?.value;
    if (!value || (cache.get(symbol)?.expires || 0) < Date.now()) {
      let job = pending.get(symbol);
      if (!job) {
        job = snapshot(symbol).then(result => { cache.set(symbol, { value: result, expires: Date.now() + 4000 }); return result; }).finally(() => pending.delete(symbol));
        pending.set(symbol, job);
      }
      value = await job;
    }
    return Response.json(value, { headers: { "Cache-Control": "no-store" } });
  } catch {
    return Response.json({ error: "Market feed temporarily unavailable. Reconnecting." }, { status: 502, headers: { "Cache-Control": "no-store" } });
  }
}
