import axios from 'axios';
import { CONFIG } from '../config';
import type { MarketData, Signal } from '../store/useStore';

const _authHeaders = CONFIG.API_KEY
  ? { 'Content-Type': 'application/json', 'X-API-Key': CONFIG.API_KEY }
  : { 'Content-Type': 'application/json' };

const api = axios.create({
  baseURL: CONFIG.API_URL,
  timeout: 30000,
  headers: _authHeaders,
});

const analystApi = axios.create({
  baseURL: CONFIG.ANALYST_URL,
  timeout: 30000,
  headers: _authHeaders,
});

const brainApi = axios.create({
  baseURL: CONFIG.BRAIN_URL,
  timeout: 30000,
  headers: _authHeaders,
});

// ── React Native SSE client (XHR-based, no native EventSource needed) ────────
export function createRNStream(
  url: string,
  onData: (data: unknown) => void,
  onError: () => void,
): () => void {
  const xhr = new XMLHttpRequest();
  xhr.open('GET', url, true);
  xhr.setRequestHeader('Accept', 'text/event-stream');
  xhr.setRequestHeader('Cache-Control', 'no-cache');
  if (CONFIG.API_KEY) {
    xhr.setRequestHeader('X-API-Key', CONFIG.API_KEY);
  }

  let processed = 0;
  let buffer = '';

  xhr.onreadystatechange = () => {
    if (
      xhr.readyState === XMLHttpRequest.LOADING ||
      xhr.readyState === XMLHttpRequest.DONE
    ) {
      buffer += xhr.responseText.slice(processed);
      processed = xhr.responseText.length;

      const chunks = buffer.split('\n\n');
      buffer = chunks.pop() ?? '';

      for (const chunk of chunks) {
        let dataLine = '';
        for (const line of chunk.split('\n')) {
          if (line.startsWith('data: ')) dataLine = line.slice(6);
        }
        if (dataLine) {
          try { onData(JSON.parse(dataLine)); } catch { /* skip malformed */ }
        }
      }
    }
    if (xhr.readyState === XMLHttpRequest.DONE && xhr.status !== 200) {
      onError();
    }
  };

  xhr.onerror = onError;
  xhr.send();

  return () => xhr.abort();
}

// ── Types ────────────────────────────────────────────────────────────────────

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

export interface RiskReport {
  ok: boolean;
  composite_score: number;
  recommendation: {
    level: string;
    description: string;
    action: string;
  };
  alerts: { message: string; severity: string }[];
  scores: {
    calendar: number;
    geo: number;
    technical: number;
  };
  ts: number;
}

export interface AnalystBriefing {
  ok: boolean;
  briefing: string;
  model: string;
  usage?: { input: number; output: number };
}

export interface BrainPrediction {
  ok: boolean;
  prediction: 'profitable' | 'risky';
  probability: number;
  confidence: 'high' | 'medium' | 'low';
  model: string;
  model_accuracy?: number;
  trades_trained_on?: number;
  user_win_rate?: number;
  message?: string;
}

// ── Market API ───────────────────────────────────────────────────────────────

export const marketAPI = {
  async checkHealth(): Promise<{ ok: boolean; ts: number }> {
    const response = await api.get('/health');
    return response.data;
  },

  async getSnapshot(symbol: string): Promise<MarketSnapshot> {
    const response = await api.get('/api/market/snapshot', { params: { symbol } });
    return response.data;
  },

  async getArbitrage(symbol: string): Promise<ArbitrageData> {
    const response = await api.get('/api/market/arb', { params: { symbol } });
    return response.data;
  },

  async getRiskReport(symbol: string = 'BTC/USDT'): Promise<RiskReport> {
    const response = await api.get('/api/sentinel/risk', { params: { symbol } });
    return response.data;
  },

  createMarketStream(
    symbol: string,
    intervalMs: number = 1000,
    onData: (data: MarketSnapshot) => void,
    onError: (error: Event) => void,
  ): EventSource {
    const url = `${CONFIG.API_URL}/api/market/stream?symbol=${encodeURIComponent(symbol)}&interval_ms=${intervalMs}`;
    const eventSource = new EventSource(url);
    eventSource.addEventListener('snapshot', (event: MessageEvent) => {
      try {
        onData(JSON.parse(event.data));
      } catch (e) {
        console.error('Failed to parse market data:', e);
      }
    });
    eventSource.onerror = onError;
    return eventSource;
  },

  transformToMarketData(snapshot: MarketSnapshot): MarketData {
    const prices = snapshot.venues
      .filter((v) => v.last !== null)
      .map((v) => ({ venue: v.venue, price: v.last!, timestamp: v.ts }));

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

// ── LLM Analyst API ──────────────────────────────────────────────────────────

export const analystAPI = {
  async getBriefing(): Promise<AnalystBriefing> {
    const response = await analystApi.get('/api/analyst/briefing');
    return response.data;
  },

  async ask(question: string): Promise<{ ok: boolean; answer: string }> {
    const response = await analystApi.get('/api/analyst/ask', {
      params: { q: question },
    });
    return response.data;
  },

  async checkHealth(): Promise<{ ok: boolean; context_age_s: number }> {
    const response = await analystApi.get('/health');
    return response.data;
  },
};

// ── Brain Prediction API ─────────────────────────────────────────────────────

export const brainAPI = {
  async predict(params: {
    user_id: string;
    rsi: number;
    atr_score: number;
    geo_score: number;
    calendar_score: number;
  }): Promise<BrainPrediction> {
    const response = await brainApi.post('/api/brain/predict', params);
    return response.data;
  },

  async syncTrades(params: {
    user_id: string;
    exchange: string;
    api_key: string;
    api_secret: string;
    symbol?: string;
    days?: number;
  }): Promise<{ ok: boolean; trained: boolean; trade_count?: number; model_accuracy?: number }> {
    const response = await brainApi.post('/api/brain/sync', params);
    return response.data;
  },

  async getStats(userId: string) {
    const response = await brainApi.get(`/api/brain/stats/${userId}`);
    return response.data;
  },
};

export default api;
