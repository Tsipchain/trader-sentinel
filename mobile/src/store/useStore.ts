import { create } from 'zustand';
import { persist, createJSONStorage } from 'zustand/middleware';
import AsyncStorage from '@react-native-async-storage/async-storage';

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

export interface WalletState {
  isConnected: boolean;
  address: string | null;
  chainId: number | null;
  balance: string;
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
        wallet: { isConnected: false, address: null, chainId: null, balance: '0' },
      }),

      // Wallet
      wallet: {
        isConnected: false,
        address: null,
        chainId: null,
        balance: '0',
      },
      setWallet: (wallet) => set((state) => ({
        wallet: { ...state.wallet, ...wallet },
      })),
      disconnectWallet: () => set({
        wallet: { isConnected: false, address: null, chainId: null, balance: '0' },
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
      }),
    }
  )
);

export default useStore;
