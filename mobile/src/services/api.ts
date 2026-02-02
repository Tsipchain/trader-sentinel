import axios from 'axios';
import { CONFIG } from '../config';
import type { MarketData, Signal } from '../store/useStore';

const api = axios.create({
  baseURL: CONFIG.API_URL,
  timeout: 30000,
  headers: {
    'Content-Type': 'application/json',
  },
});

// Types
export interface MarketSnapshot {
  symbol: string;
  venues: {
    venue: string;
    last: number | null;
    bid: number | null;
    ask: number | null;
    ts: number;
    error?: string;
  }[];
}

export interface ArbitrageData {
  symbol: string;
  best_bid: number;
  best_bid_venue: string;
  best_ask: number;
  best_ask_venue: string;
  spread: number;
  dex_last: number | null;
  dex_minus_best_ask: number | null;
}

// API Functions
export const marketAPI = {
  // Health check
  async checkHealth(): Promise<{ ok: boolean; ts: number }> {
    const response = await api.get('/health');
    return response.data;
  },

  // Get market snapshot for a symbol
  async getSnapshot(symbol: string): Promise<MarketSnapshot> {
    const response = await api.get('/api/market/snapshot', {
      params: { symbol },
    });
    return response.data;
  },

  // Get arbitrage opportunities
  async getArbitrage(symbol: string): Promise<ArbitrageData> {
    const response = await api.get('/api/market/arb', {
      params: { symbol },
    });
    return response.data;
  },

  // Subscribe to market stream (SSE)
  createMarketStream(
    symbol: string,
    intervalMs: number = 1000,
    onData: (data: MarketSnapshot) => void,
    onError: (error: Event) => void
  ): EventSource {
    const url = `${CONFIG.API_URL}/api/market/stream?symbol=${encodeURIComponent(symbol)}&interval_ms=${intervalMs}`;
    const eventSource = new EventSource(url);

    eventSource.addEventListener('snapshot', (event: MessageEvent) => {
      try {
        const data = JSON.parse(event.data);
        onData(data);
      } catch (e) {
        console.error('Failed to parse market data:', e);
      }
    });

    eventSource.onerror = onError;

    return eventSource;
  },

  // Transform snapshot to MarketData
  transformToMarketData(snapshot: MarketSnapshot): MarketData {
    const prices = snapshot.venues
      .filter((v) => v.last !== null)
      .map((v) => ({
        venue: v.venue,
        price: v.last!,
        timestamp: v.ts,
      }));

    const validAsks = snapshot.venues.filter((v) => v.ask !== null);
    const validBids = snapshot.venues.filter((v) => v.bid !== null);

    const bestAsk = validAsks.length > 0
      ? validAsks.reduce((min, v) => (v.ask! < min.ask! ? v : min))
      : null;

    const bestBid = validBids.length > 0
      ? validBids.reduce((max, v) => (v.bid! > max.bid! ? v : max))
      : null;

    return {
      symbol: snapshot.symbol,
      prices,
      bestBid: bestBid?.bid || 0,
      bestBidVenue: bestBid?.venue || '',
      bestAsk: bestAsk?.ask || 0,
      bestAskVenue: bestAsk?.venue || '',
      spread: bestAsk && bestBid ? bestAsk.ask! - bestBid.bid! : 0,
    };
  },

  // Detect arbitrage signals
  detectArbitrageSignal(data: ArbitrageData, minSpreadPercent: number = 0.5): Signal | null {
    const spreadPercent = (data.spread / data.best_ask) * 100;

    if (spreadPercent >= minSpreadPercent) {
      return {
        id: `arb-${Date.now()}`,
        type: 'arbitrage',
        symbol: data.symbol,
        message: `Buy on ${data.best_ask_venue} at $${data.best_ask.toFixed(2)}, sell on ${data.best_bid_venue} at $${data.best_bid.toFixed(2)}`,
        profit: spreadPercent,
        timestamp: Date.now(),
        venues: [data.best_ask_venue, data.best_bid_venue],
      };
    }

    return null;
  },
};

export default api;
