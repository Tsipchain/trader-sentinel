import { create } from 'zustand';
import { persist, createJSONStorage } from 'zustand/middleware';
import AsyncStorage from '@react-native-async-storage/async-storage';
import type { ActiveTrade, TradeRecord, TradeStats } from '../services/api';

// Types
export interface User {
  id: string;
  walletAddress: string;
  email?: string;
  subscription: SubscriptionTier;
  thronosBalance: number;
  rewardsBalance: number;
  referralCode: string;
  createdAt: string;
}

export type SubscriptionTier = 'free' | 'starter' | 'pro' | 'elite' | 'whale';

export type WalletType = 'thronos' | 'evm' | 'phantom' | null;

export interface WalletState {
  isConnected: boolean;
  address: string | null;
  chainId: number | string | null;
  balance: string;
  walletType: WalletType;
  selectedChainKey: string | null;  // key into CONFIG.SUPPORTED_CHAINS (e.g. 'ETHEREUM', 'BSC')
  provider: string | null;          // wallet provider id (e.g. 'metamask', 'trust', 'thronos')
}

export interface Signal {
  id: string;
  type: 'arbitrage' | 'alert' | 'opportunity';
  symbol: string;
  message: string;
  profit?: number;
  timestamp: number;
  venues: string[];
}

export interface MarketData {
  symbol: string;
  prices: {
    venue: string;
    price: number;
    timestamp: number;
  }[];
  bestBid: number;
  bestBidVenue: string;
  bestAsk: number;
  bestAskVenue: string;
  spread: number;
}

export interface AutoTraderConfig {
  exchange: string;
  apiKey: string;
  apiSecret: string;
  passphrase: string;
  symbols: string[];
  stopLossPct: number;
  takeProfitPct: number;
  maxPositionPct: number;
  maxOpenTrades: number;
  marginMode: 'isolated' | 'cross';
  maxLeverage: number;
  riskPerTradePct: number;
  maxTotalExposurePct: number;
}

export interface PortfolioState {
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
  lastSyncTs: number | null;
}

const DEFAULT_AUTOTRADER_CONFIG: AutoTraderConfig = {
  exchange: 'binance',
  apiKey: '',
  apiSecret: '',
  passphrase: '',
  symbols: ['BTC/USDT', 'ETH/USDT'],
  stopLossPct: 2,
  takeProfitPct: 4,
  maxPositionPct: 10,
  maxOpenTrades: 3,
  marginMode: 'isolated',
  maxLeverage: 125,
  riskPerTradePct: 1,
  maxTotalExposurePct: 25,
};

const DEFAULT_PORTFOLIO: PortfolioState = {
  equity: 0,
  balances: [],
  positions: [],
  usedMargin: 0,
  maxLeverageBySymbol: {},
  lastSyncTs: null,
};

// Store Interface
interface AppStore {
  // Auth & User
  user: User | null;
  isAuthenticated: boolean;
  setUser: (user: User | null) => void;
  logout: () => void;

  // Wallet
  wallet: WalletState;
  setWallet: (wallet: Partial<WalletState>) => void;
  disconnectWallet: () => void;

  // Subscription
  subscription: SubscriptionTier;
  setSubscription: (tier: SubscriptionTier) => void;

  // Signals
  signals: Signal[];
  addSignal: (signal: Signal) => void;
  clearSignals: () => void;

  // Market Data
  marketData: Record<string, MarketData>;
  setMarketData: (symbol: string, data: MarketData) => void;

  // Rewards
  rewards: {
    total: number;
    pending: number;
    claimed: number;
    history: { amount: number; type: string; date: string }[];
  };
  addReward: (amount: number, type: string) => void;
  claimRewards: () => void;

  // Settings
  settings: {
    notifications: boolean;
    soundAlerts: boolean;
    hapticFeedback: boolean;
    theme: 'dark' | 'light';
    currency: string;
  };
  updateSettings: (settings: Partial<AppStore['settings']>) => void;

  // Watchlist
  watchlist: string[];
  addToWatchlist: (symbol: string) => void;
  removeFromWatchlist: (symbol: string) => void;

  // AutoTrader
  autoTrader: {
    enabled: boolean;
    config: AutoTraderConfig;
    activeTrades: ActiveTrade[];
    portfolio: PortfolioState;
    exchangeAvailability: Record<string, { enabled: boolean; reason?: string }>;
  };
  setAutoTrader: (partial: Partial<AppStore['autoTrader']>) => void;

  // Trade History
  tradeHistory: {
    trades: TradeRecord[];
    stats: TradeStats | null;
    lastSynced: string | null;
    aiAnalysis: string | null;
  };
  setTradeHistory: (partial: Partial<AppStore['tradeHistory']>) => void;
}

export const useStore = create<AppStore>()(
  persist(
    (set, get) => ({
      // Auth & User
      user: null,
      isAuthenticated: false,
      setUser: (user) => set({ user, isAuthenticated: !!user }),
      logout: () => set({
        user: null,
        isAuthenticated: false,
        wallet: { isConnected: false, address: null, chainId: null, balance: '0', walletType: null, selectedChainKey: null, provider: null },
      }),

      // Wallet
      wallet: {
        isConnected: false,
        address: null,
        chainId: null,
        balance: '0',
        walletType: null,
        selectedChainKey: null,
        provider: null,
      },
      setWallet: (wallet) => set((state) => ({
        wallet: { ...state.wallet, ...wallet },
      })),
      disconnectWallet: () => set({
        wallet: { isConnected: false, address: null, chainId: null, balance: '0', walletType: null, selectedChainKey: null, provider: null },
      }),

      // Subscription
      subscription: 'free',
      setSubscription: (tier) => set({ subscription: tier }),

      // Signals
      signals: [],
      addSignal: (signal) => set((state) => ({
        signals: [signal, ...state.signals].slice(0, 100), // Keep last 100
      })),
      clearSignals: () => set({ signals: [] }),

      // Market Data
      marketData: {},
      setMarketData: (symbol, data) => set((state) => ({
        marketData: { ...state.marketData, [symbol]: data },
      })),

      // Rewards
      rewards: {
        total: 0,
        pending: 0,
        claimed: 0,
        history: [],
      },
      addReward: (amount, type) => set((state) => ({
        rewards: {
          ...state.rewards,
          total: state.rewards.total + amount,
          pending: state.rewards.pending + amount,
          history: [
            { amount, type, date: new Date().toISOString() },
            ...state.rewards.history,
          ],
        },
      })),
      claimRewards: () => set((state) => ({
        rewards: {
          ...state.rewards,
          claimed: state.rewards.claimed + state.rewards.pending,
          pending: 0,
        },
      })),

      // Settings
      settings: {
        notifications: true,
        soundAlerts: true,
        hapticFeedback: true,
        theme: 'dark',
        currency: 'USD',
      },
      updateSettings: (settings) => set((state) => ({
        settings: { ...state.settings, ...settings },
      })),

      // Watchlist
      watchlist: ['BTC/USDT', 'ETH/USDT', 'SOL/USDT'],
      addToWatchlist: (symbol) => set((state) => ({
        watchlist: state.watchlist.includes(symbol)
          ? state.watchlist
          : [...state.watchlist, symbol],
      })),
      removeFromWatchlist: (symbol) => set((state) => ({
        watchlist: state.watchlist.filter((s) => s !== symbol),
      })),

      // AutoTrader
      autoTrader: {
        enabled: false,
        config: DEFAULT_AUTOTRADER_CONFIG,
        activeTrades: [],
        portfolio: DEFAULT_PORTFOLIO,
        exchangeAvailability: {},
      },
      setAutoTrader: (partial) => set((state) => ({
        autoTrader: {
          ...state.autoTrader,
          ...partial,
          config: {
            ...state.autoTrader.config,
            ...(partial.config ?? {}),
          },
          portfolio: {
            ...state.autoTrader.portfolio,
            ...(partial.portfolio ?? {}),
          },
          exchangeAvailability: {
            ...state.autoTrader.exchangeAvailability,
            ...(partial.exchangeAvailability ?? {}),
          },
        },
      })),

      // Trade History
      tradeHistory: {
        trades: [],
        stats: null,
        lastSynced: null,
        aiAnalysis: null,
      },
      setTradeHistory: (partial) => set((state) => ({
        tradeHistory: { ...state.tradeHistory, ...partial },
      })),
    }),
    {
      name: 'trader-sentinel-storage',
      storage: createJSONStorage(() => AsyncStorage),
      partialize: (state) => ({
        user: state.user,
        isAuthenticated: state.isAuthenticated,
        subscription: state.subscription,
        settings: state.settings,
        watchlist: state.watchlist,
        rewards: state.rewards,
        autoTrader: state.autoTrader,
        tradeHistory: state.tradeHistory,
      }),
      merge: (persisted, current) => {
        const persistedState = (persisted as Partial<AppStore>) ?? {};

        // Protect subscription: never downgrade a persisted paid tier to 'free'
        // This prevents reset/rehydration bugs from losing paid subscriptions
        const persistedTier = persistedState.subscription || 'free';
        const persistedUserTier = persistedState.user?.subscription || 'free';
        const currentTier = (current as AppStore).subscription || 'free';
        const currentUserTier = (current as AppStore).user?.subscription || 'free';
        const safeTier = [persistedTier, persistedUserTier, currentTier, currentUserTier].find((tier) => tier && tier !== 'free') || 'free';

        return {
          ...current,
          ...persistedState,
          subscription: safeTier,
          autoTrader: {
            ...current.autoTrader,
            ...(persistedState.autoTrader ?? {}),
            config: {
              ...DEFAULT_AUTOTRADER_CONFIG,
              ...(persistedState.autoTrader?.config ?? {}),
            },
            portfolio: {
              ...DEFAULT_PORTFOLIO,
              ...(persistedState.autoTrader?.portfolio ?? {}),
            },
            exchangeAvailability: {
              ...(persistedState.autoTrader?.exchangeAvailability ?? {}),
            },
          },
        };
      },
    }
  )
);

export default useStore;
