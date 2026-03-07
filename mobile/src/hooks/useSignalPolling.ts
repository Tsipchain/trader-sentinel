// Global signal polling hook — runs at app level so signals populate
// regardless of which screen is active.

import { useEffect, useRef, useCallback } from 'react';
import { useStore } from '../store/useStore';
import { marketAPI, brainAPI } from '../services/api';
import type { Signal } from '../store/useStore';
import * as Haptics from 'expo-haptics';
import * as Notifications from 'expo-notifications';

type TierSignalPolicy = {
  directionalLimit: number;
  allowNewCoinSignals: boolean;
  refreshMs: number;
};

const SIGNAL_POLICY: Record<string, TierSignalPolicy> = {
  free: { directionalLimit: 1, allowNewCoinSignals: false, refreshMs: 20000 },
  starter: { directionalLimit: 2, allowNewCoinSignals: false, refreshMs: 15000 },
  pro: { directionalLimit: 5, allowNewCoinSignals: true, refreshMs: 12000 },
  elite: { directionalLimit: 10, allowNewCoinSignals: true, refreshMs: 9000 },
  whale: { directionalLimit: 99, allowNewCoinSignals: true, refreshMs: 7000 },
};

/** Tier-based pair access: free=BTC only, starter=BTC+ETH, pro+=all */
export function getAllowedPairs(watchlist: string[], tier: string): string[] {
  if (tier === 'free') {
    return watchlist.filter((s) => s.startsWith('BTC'));
  }
  if (tier === 'starter') {
    return watchlist.filter((s) => s.startsWith('BTC') || s.startsWith('ETH'));
  }
  // pro, elite, whale → all pairs
  return watchlist;
}

export function useSignalPolling() {
  const nextFetchAllowedAtRef = useRef(0);
  const notificationReadyRef = useRef(false);

  const hasRecentDuplicate = useCallback((idPrefix: string) => {
    const now = Date.now();
    const currentSignals = useStore.getState().signals;
    return currentSignals.some((s) => s.id.startsWith(idPrefix) && now - s.timestamp < 10 * 60 * 1000);
  }, []);

  const hasAnySignalPrefix = useCallback((idPrefix: string) => {
    const currentSignals = useStore.getState().signals;
    return currentSignals.some((s) => s.id.startsWith(idPrefix));
  }, []);

  const maybeNotify = useCallback(async (signal: Signal) => {
    const state = useStore.getState();
    if (state.subscription === 'free' || !state.settings.notifications) return;
    if (signal.type !== 'alert') return;

    try {
      if (!notificationReadyRef.current) {
        const perm = await Notifications.requestPermissionsAsync();
        if (!perm.granted) return;
        notificationReadyRef.current = true;
      }

      await Notifications.scheduleNotificationAsync({
        content: {
          title: `${signal.symbol} Alert`,
          body: signal.message,
          data: { signalId: signal.id },
        },
        trigger: null,
      });
    } catch {
      // ignore notification failures
    }
  }, []);

  const addSignalWithFeedback = useCallback((signal: Signal) => {
    const state = useStore.getState();
    state.addSignal(signal);

    if (state.settings.hapticFeedback) {
      Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
    }
    maybeNotify(signal);

    if (state.subscription !== 'free' && state.user?.id) {
      brainAPI.publishTelegramSignal({
        user_id: state.user.id,
        tier: state.subscription,
        signal_type: signal.type,
        symbol: signal.symbol,
        message: signal.message,
        timestamp: signal.timestamp,
      }).catch(() => {});
    }
  }, [maybeNotify]);

  const fetchSignals = useCallback(async () => {
    if (Date.now() < nextFetchAllowedAtRef.current) return;

    const state = useStore.getState();
    const { watchlist, subscription } = state;
    const tierPolicy = SIGNAL_POLICY[subscription] ?? SIGNAL_POLICY.free;
    const canUseAdvancedSignals = subscription !== 'free';
    const allowedPairs = getAllowedPairs(watchlist, subscription);

    try {
      // Arbitrage signals for tier-allowed pairs
      const results = await Promise.allSettled(
        allowedPairs.map(async (symbol) => {
          const arb = await marketAPI.getArbitrage(symbol);
          const signal = marketAPI.detectArbitrageSignal(arb, 0.1);
          return { symbol, arb, signal };
        }),
      );

      results.forEach((result) => {
        if (result.status !== 'fulfilled') return;
        const { signal, symbol, arb } = result.value;

        if (signal && !hasRecentDuplicate(`arb-${symbol}`)) {
          addSignalWithFeedback({ ...signal, id: `arb-${symbol}-${Date.now()}` });
        }

        // Pro+ new-coin / limited-availability signals
        if (tierPolicy.allowNewCoinSignals && !hasRecentDuplicate(`newcoin-${symbol}`)) {
          const listedVenue = arb.best_bid_venue || arb.best_ask_venue;
          const noVenue = !arb.best_bid_venue || !arb.best_ask_venue;
          if (listedVenue && noVenue) {
            addSignalWithFeedback({
              id: `newcoin-${symbol}-${Date.now()}`,
              type: 'opportunity',
              symbol,
              message: `${symbol} shows limited venue availability — possible early listing edge.`,
              timestamp: Date.now(),
              venues: [listedVenue],
            });
          }
        }
      });

      // Free-tier baseline directional hint
      if (!canUseAdvancedSignals && allowedPairs.length > 0) {
        const baseSymbol = allowedPairs[0];
        const prefix = `free-risk-${baseSymbol}`;
        if (!hasAnySignalPrefix(prefix)) {
          const risk = await marketAPI.getRiskReport(baseSymbol);
          const lowRisk = risk.composite_score <= 5.5;
          addSignalWithFeedback({
            id: `${prefix}-${Date.now()}`,
            type: lowRisk ? 'opportunity' : 'alert',
            symbol: baseSymbol,
            message: lowRisk
              ? `Baseline LONG watch: risk ${risk.composite_score.toFixed(1)}/10 (${risk.recommendation.level}).`
              : `Baseline DEFENSIVE watch: risk ${risk.composite_score.toFixed(1)}/10 (${risk.recommendation.level}).`,
            timestamp: Date.now(),
            venues: ['sentinel-risk'],
          });
        }
      }

      // Pro+ directional signals from composite risk framework
      if (canUseAdvancedSignals) {
        const directionalSymbols = allowedPairs.slice(0, tierPolicy.directionalLimit);
        const riskSignals = await Promise.all(
          directionalSymbols.map(async (symbol) => {
            const risk = await marketAPI.getRiskReport(symbol);
            return { symbol, risk };
          }),
        );

        riskSignals.forEach(({ symbol, risk }) => {
          const level = risk.recommendation?.level || 'UNKNOWN';
          const action = (risk.recommendation?.action || '').toLowerCase();
          const prefix = `dir-${symbol}-${level}`;

          if (hasRecentDuplicate(prefix)) return;

          if (action.includes('reduce') || action.includes('hedge') || level === 'CAUTION' || level === 'CRITICAL') {
            addSignalWithFeedback({
              id: `${prefix}-${Date.now()}`,
              type: 'alert',
              symbol,
              message: `SHORT/DEFENSIVE bias: Risk ${risk.composite_score.toFixed(1)}/10 (${level}) — ${risk.recommendation.description}`,
              timestamp: Date.now(),
              venues: ['sentinel-risk'],
            });
            return;
          }

          if (level === 'NEUTRAL' || action.includes('accumulate') || action.includes('long')) {
            addSignalWithFeedback({
              id: `${prefix}-${Date.now()}`,
              type: 'opportunity',
              symbol,
              message: `LONG/ACCUMULATE bias: Risk ${risk.composite_score.toFixed(1)}/10 (${level}) — ${risk.recommendation.description}`,
              timestamp: Date.now(),
              venues: ['sentinel-risk'],
            });
          }
        });
      }
    } catch (error) {
      const status = (error as any)?.response?.status;
      const isNetworkError = (error as any)?.message === 'Network Error';
      if (status === 429) {
        nextFetchAllowedAtRef.current = Date.now() + 60_000;
      } else if (isNetworkError) {
        nextFetchAllowedAtRef.current = Date.now() + 15_000;
      }
    }
  }, [addSignalWithFeedback, hasRecentDuplicate, hasAnySignalPrefix]);

  useEffect(() => {
    const state = useStore.getState();
    const tierPolicy = SIGNAL_POLICY[state.subscription] ?? SIGNAL_POLICY.free;

    // Initial fetch
    fetchSignals();

    const interval = setInterval(fetchSignals, tierPolicy.refreshMs);
    return () => clearInterval(interval);
  }, [fetchSignals]);
}
