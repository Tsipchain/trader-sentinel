import axios, { AxiosError, AxiosRequestConfig } from 'axios';
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

const brainFallbackApi = axios.create({
  baseURL: CONFIG.API_URL,
  timeout: 30000,
  headers: _authHeaders,
});

function _shouldFallbackToGateway(error: unknown): boolean {
  const err = error as AxiosError;
  const status = err?.response?.status;
  // Fallback when dedicated Brain URL is misrouted/unavailable.
  return !err?.response || status === 404 || status === 502 || status === 503;
}

async function _brainRequest<T = any>(config: AxiosRequestConfig): Promise<T> {
  try {
    const res = await brainApi.request<T>(config);
    return res.data;
  } catch (error) {
    if (CONFIG.BRAIN_URL !== CONFIG.API_URL && _shouldFallbackToGateway(error)) {
      const res = await brainFallbackApi.request<T>(config);
      return res.data;
    }
    throw error;
  }
}

const brainGet = <T = any>(url: string, config?: AxiosRequestConfig) =>
  _brainRequest<T>({ ...(config ?? {}), method: 'GET', url });

const brainPost = <T = any>(url: string, data?: any, config?: AxiosRequestConfig) =>
  _brainRequest<T>({ ...(config ?? {}), method: 'POST', url, data });

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

export interface AnalystWarmingUp {
  ok: false;
  warming_up: true;
  retry_s: number;
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
  async getBriefing(): Promise<AnalystBriefing | AnalystWarmingUp> {
    const response = await analystApi.get('/api/analyst/briefing');
    return response.data;
  },

  async ask(question: string): Promise<{ ok: boolean; answer?: string; warming_up?: boolean; retry_s?: number }> {
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

// ── AutoTrader & History types ────────────────────────────────────────────────

export interface ActiveTrade {
  id: string;
  symbol: string;
  side: 'BUY' | 'SELL';
  entryPrice: number;
  currentPrice: number;
  quantity: number;
  pnl: number;           // % unrealised P&L
  openedAt: number;      // unix ms
}

export interface TradeRecord {
  id: string;
  symbol: string;
  side: 'BUY' | 'SELL';
  entryPrice: number;
  exitPrice: number;
  quantity: number;
  pnl: number;           // % realised P&L
  pnlUsd: number;
  openedAt: number;
  closedAt: number;
  exchange: string;
}

export interface TradeStats {
  total_trades: number;
  win_rate: number;       // 0–1
  total_pnl_usd: number;
  avg_pnl_pct: number;
  best_trade_pct: number;
  worst_trade_pct: number;
  most_traded_symbol: string;
}

export interface AutoTraderStatus {
  ok: boolean;
  enabled: boolean;
  active_trades: ActiveTrade[];
  log: string[];
}

export interface ExchangeAvailabilityFlag {
  enabled: boolean;
  reason?: string;
}




export interface BrainServiceCheck {
  ok: boolean;
  isBrain: boolean;
  reason?: string;
  storage?: { disk_path?: string };
}
export interface BrainSubscriptionFingerprintResponse {
  ok: boolean;
  hash?: string;
}

export interface BrainAnalysisSnapshotResponse {
  ok: boolean;
}
export interface PortfolioSnapshot {
  equity: number;
  balances: Array<{ asset: string; total: number; free: number; used: number }>;
  positions: Array<{
    symbol: string;
    side: string;
    contracts: number;
    entryPrice: number;
    markPrice: number;
    unrealizedPnl: number;
    leverage: number;
    marginMode: string;
  }>;
  usedMargin: number;
  maxLeverageBySymbol: Record<string, number>;
  ts: number;
}

const _BRAIN_SERVICE_CHECK_TTL_MS = 60_000;
let _brainServiceCheckCache: { at: number; value: BrainServiceCheck } | null = null;

export const brainAPI = {
  async checkHealth(): Promise<{ ok: boolean; ts?: number }> {
    const response = await brainGet('/health');
    return response;
  },

  async checkServiceType(): Promise<BrainServiceCheck> {
    if (_brainServiceCheckCache && Date.now() - _brainServiceCheckCache.at < _BRAIN_SERVICE_CHECK_TTL_MS) {
      return _brainServiceCheckCache.value;
    }

    const _cache = (value: BrainServiceCheck): BrainServiceCheck => {
      _brainServiceCheckCache = { at: Date.now(), value };
      return value;
    };

    const _looksLikeBrain = (paths: string[]) =>
      paths.some((p) => p.startsWith('/api/brain/') || p === '/api/brain/sync' || p === '/api/brain/predict');

    const _looksLikeMainBackend = (paths: string[]) =>
      paths.some((p) => p.startsWith('/api/sentinel/') || p.startsWith('/api/market/'));

    const _probeStorage = async (client: typeof brainApi) => {
      try {
        const status = await client.get<{ ok: boolean; disk_path?: string }>('/api/brain/storage/status');
        if (status.data?.ok) {
          return { ok: true, disk_path: status.data.disk_path } as const;
        }
      } catch {
        // best-effort probe
      }
      return { ok: false } as const;
    };

    try {
      // 1) Direct configured Brain host
      const directOpenapi = await brainApi.get<{ paths?: Record<string, unknown> }>('/openapi.json');
      const directPaths = Object.keys(directOpenapi.data?.paths ?? {});
      if (_looksLikeBrain(directPaths)) {
        return _cache({ ok: true, isBrain: true });
      }

      const directStorage = await _probeStorage(brainApi);
      if (directStorage.ok) {
        return _cache({ ok: true, isBrain: true, storage: { disk_path: directStorage.disk_path } });
      }

      // 2) Compatibility path: API gateway may proxy /api/brain/* even when dedicated BRAIN_URL is mispointed.
      if (CONFIG.BRAIN_URL !== CONFIG.API_URL) {
        try {
          const gatewayOpenapi = await brainFallbackApi.get<{ paths?: Record<string, unknown> }>('/openapi.json');
          const gatewayPaths = Object.keys(gatewayOpenapi.data?.paths ?? {});
          if (_looksLikeBrain(gatewayPaths)) {
            return _cache({ ok: true, isBrain: true });
          }

          const gatewayStorage = await _probeStorage(brainFallbackApi);
          if (gatewayStorage.ok) {
            return _cache({ ok: true, isBrain: true, storage: { disk_path: gatewayStorage.disk_path } });
          }
        } catch {
          // keep diagnostics from direct host below
        }
      }

      if (_looksLikeMainBackend(directPaths)) {
        return _cache({
          ok: false,
          isBrain: false,
          reason: 'Configured BRAIN_URL appears to be the main backend (/api/sentinel/*), not Sentinel Brain (/api/brain/*).',
        });
      }

      return _cache({ ok: false, isBrain: false, reason: 'Configured BRAIN_URL does not expose expected /api/brain routes.' });
    } catch (error) {
      const err = error as AxiosError;
      const code = err?.response?.status;
      if (code === 404) {
        return _cache({
          ok: false,
          isBrain: false,
          reason: 'Configured BRAIN_URL points to a service without Brain routes.',
        });
      }
      return _cache({
        ok: false,
        isBrain: false,
        reason: err?.message ?? 'Unable to validate Brain service type',
      });
    }
  },

  async predict(params: {
    user_id: string;
    rsi: number;
    atr_score: number;
    geo_score: number;
    calendar_score: number;
  }): Promise<BrainPrediction> {
    const response = await brainPost('/api/brain/predict', params);
    return response;
  },

  async syncTrades(params: {
    user_id: string;
    exchange: string;
    api_key: string;
    api_secret: string;
    symbol?: string;
    days?: number;
  }): Promise<{ ok: boolean; trained: boolean; trade_count?: number; model_accuracy?: number }> {
    const response = await brainPost('/api/brain/sync', params);
    return response;
  },

  async getStats(userId: string): Promise<{ ok: boolean } & TradeStats> {
    const response = await brainGet(`/api/brain/stats/${userId}`);
    return response;
  },

  async getHistory(userId: string, limit: number = 50): Promise<{ ok: boolean; trades: TradeRecord[] }> {
    const response = await brainGet(`/api/brain/history/${userId}`, { params: { limit } });
    return response;
  },

  async getAutoTraderStatus(userId: string): Promise<AutoTraderStatus> {
    const response = await brainGet(`/api/brain/autotrader/${userId}`);
    return response;
  },

  async enableAutoTrader(params: {
    user_id: string;
    exchange: string;
    api_key: string;
    api_secret: string;
    symbols: string[];
    stop_loss_pct: number;
    take_profit_pct: number;
    max_position_pct: number;
    max_open_trades: number;
    passphrase?: string;
    margin_mode: "isolated" | "cross";
    max_leverage: number;
    risk_per_trade_pct: number;
    max_total_exposure_pct: number;
  }): Promise<{ ok: boolean }> {
    const response = await brainPost('/api/brain/autotrader/enable', params);
    return response;
  },

  async disableAutoTrader(userId: string): Promise<{ ok: boolean }> {
    const response = await brainPost('/api/brain/autotrader/disable', { user_id: userId });
    return response;
  },

  async closeTrade(userId: string, tradeId: string): Promise<{ ok: boolean }> {
    const response = await brainPost('/api/brain/autotrader/close', { user_id: userId, trade_id: tradeId });
    return response;
  },

  async getExchangeAvailability(): Promise<{ ok: boolean; exchanges: Record<string, ExchangeAvailabilityFlag> }> {
    const response = await brainGet('/api/brain/exchange/availability');
    return response;
  },


  async registerSubscription(params: {
    user_id: string;
    tier: string;
    source?: string;
    wallet_address?: string;
  }): Promise<BrainSubscriptionFingerprintResponse> {
    const response = await brainPost('/api/brain/subscription/register', params);
    return response;
  },

  async saveAnalysisSnapshot(params: {
    user_id: string;
    kind: string;
    content: Record<string, unknown>;
    symbol?: string;
  }): Promise<BrainAnalysisSnapshotResponse> {
    const response = await brainPost('/api/brain/analysis/snapshot', params);
    return response;
  },

  async publishTelegramSignal(params: {
    user_id: string;
    tier: string;
    signal_type: string;
    symbol: string;
    message: string;
    timestamp: number;
  }): Promise<{ ok: boolean; detail?: string }> {
    const response = await brainPost('/api/brain/telegram/signal', params);
    return response;
  },

  async getExchangeSnapshot(params: {
    exchange: string;
    apiKey: string;
    apiSecret: string;
    passphrase?: string;
  }): Promise<{ ok: boolean; snapshot: PortfolioSnapshot | null; exchanges?: Record<string, ExchangeAvailabilityFlag>; error?: string; blocked?: boolean }> {
    const response = await brainPost('/api/brain/exchange/snapshot', {
      exchange: params.exchange,
      api_key: params.apiKey,
      api_secret: params.apiSecret,
      passphrase: params.passphrase,
    });
    return response;
  },

};

export default api;
