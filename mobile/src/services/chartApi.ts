// Fetches OHLC candles from the Binance public REST API (no auth needed).
// Supports the five timeframes the app exposes: 15m, 1h, 4h, 1d, 1M.

export type Timeframe = '15m' | '1h' | '4h' | '1d' | '1M';

export const TIMEFRAMES: { label: string; value: Timeframe }[] = [
  { label: '15m', value: '15m' },
  { label: '1H',  value: '1h'  },
  { label: '4H',  value: '4h'  },
  { label: '1D',  value: '1d'  },
  { label: '1M',  value: '1M'  },
];

export interface Candle {
  timestamp: number; // open-time in ms
  open:      number;
  high:      number;
  low:       number;
  close:     number;
  volume:    number;
}

// Convert "BTC/USDT" → "BTCUSDT" (Binance format)
function toBinanceSymbol(symbol: string): string {
  return symbol.replace('/', '').toUpperCase();
}

export const chartAPI = {
  async getCandles(
    symbol: string,
    timeframe: Timeframe,
    limit = 60,
  ): Promise<Candle[]> {
    const s = toBinanceSymbol(symbol);
    const url =
      `https://api.binance.com/api/v3/klines` +
      `?symbol=${s}&interval=${timeframe}&limit=${limit}`;

    const res = await fetch(url);
    if (!res.ok) {
      throw new Error(`Binance ${res.status}: ${await res.text()}`);
    }

    // Binance kline format:
    // [openTime, open, high, low, close, volume, closeTime, …]
    const raw: any[][] = await res.json();
    return raw.map((k) => ({
      timestamp: k[0] as number,
      open:      parseFloat(k[1]),
      high:      parseFloat(k[2]),
      low:       parseFloat(k[3]),
      close:     parseFloat(k[4]),
      volume:    parseFloat(k[5]),
    }));
  },
};
