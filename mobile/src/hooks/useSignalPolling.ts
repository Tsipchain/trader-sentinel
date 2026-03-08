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

/** Tier-based pair access for directional signals: free=BTC only, starter=BTC+ETH, pro+=all */
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

/** Leverage risk classification for position-aware alerts */
function leverageRiskLevel(leverage: number): 'low' | 'medium' | 'high' | 'extreme' {
  if (leverage <= 10) return 'low';
  if (leverage <= 50) return 'medium';
  if (leverage <= 150) return 'high';
  return 'extreme'; // 150x-300x
}

function leverageSuggestion(leverage: number, pnlPct: number, liqDistance: number): string {
  const risk = leverageRiskLevel(leverage);
  if (risk === 'extreme' && pnlPct > 50) {
    return `Take partial profit — ${leverage}x leverage with +${pnlPct.toFixed(1)}% unrealized. Lock in gains before volatility spike.`;
  }
  if (risk === 'extreme' && liqDistance < 5) {
    return `Liquidation ${liqDistance.toFixed(1)}% away at ${leverage}x! Reduce leverage or add margin immediately.`;
  }
  if (risk === 'high' && pnlPct < -10) {
    return `Position underwater at ${leverage}x. Consider reducing size to avoid forced liquidation.`;
  }
  if (risk === 'extreme') {
    return `Running ${leverage}x — tight management required. Trail stops aggressively.`;
  }
  if (risk === 'high') {
    return `${leverage}x leverage active. Monitor liquidation distance and consider scaling out at key levels.`;
  }
  return '';
}

export function useSignalPolling() {
  const nextFetchAllowedAtRef = useRef(0);
  const notificationReadyRef = useRef(false);
  const positionCheckRef = useRef(0); // throttle position checks

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

  // ── Position-Aware Sentinel Alerts ──────────────────────────────────────────
  const checkPositionAlerts = useCallback(async () => {
    const now = Date.now();
    // Throttle position checks to every 60s (they're heavier than market data)
    if (now - positionCheckRef.current < 60_000) return;
    positionCheckRef.current = now;

    const state = useStore.getState();
    const { autoTrader, user } = state;
    const config = autoTrader?.config;

    // Need exchange credentials to check positions
    if (!config?.apiKey || !config?.apiSecret || !config?.exchange) return;

    try {
      const result = await brainAPI.getOpenPositions({
        user_id: user?.id || 'anonymous',
        exchange: config.exchange,
        api_key: config.apiKey,
        api_secret: config.apiSecret,
        passphrase: config.passphrase || undefined,
      });

      if (!result.ok || !result.positions?.length) return;

      for (const pos of result.positions) {
        const sym = pos.symbol?.replace(':USDT', '')?.replace('/', '') || 'UNKNOWN';
        const leverage = pos.leverage || 1;
        const pnlPct = pos.pnlPct || 0;
        const entry = pos.entryPrice || 0;
        const mark = pos.markPrice || 0;
        const liq = pos.liquidationPrice || 0;
        const side = (pos.side || '').toLowerCase();

        // Calculate distance to liquidation (%)
        let liqDistance = 100;
        if (liq > 0 && mark > 0) {
          liqDistance = side === 'long'
            ? ((mark - liq) / mark) * 100
            : ((liq - mark) / mark) * 100;
        }

        // ── Liquidation proximity alert ──
        if (liqDistance < 3 && !hasRecentDuplicate(`liq-warn-${sym}`)) {
          addSignalWithFeedback({
            id: `liq-warn-${sym}-${now}`,
            type: 'alert',
            symbol: sym,
            message: `LIQUIDATION WARNING: ${sym} ${side.toUpperCase()} ${leverage}x — only ${liqDistance.toFixed(1)}% from liquidation at $${liq.toFixed(4)}. Add margin or reduce position NOW.`,
            timestamp: now,
            venues: ['sentinel-position'],
          });
        }

        // ── Extreme leverage alert (>100x) ──
        if (leverage >= 100 && !hasRecentDuplicate(`lev-${sym}`)) {
          const suggestion = leverageSuggestion(leverage, pnlPct, liqDistance);
          if (suggestion) {
            addSignalWithFeedback({
              id: `lev-${sym}-${now}`,
              type: pnlPct > 30 ? 'opportunity' : 'alert',
              symbol: sym,
              message: `${sym} ${side.toUpperCase()} ${leverage}x cross — ${suggestion}`,
              timestamp: now,
              venues: ['sentinel-position'],
            });
          }
        }

        // ── Take profit suggestion when PnL is high ──
        if (pnlPct > 50 && !hasRecentDuplicate(`tp-${sym}`)) {
          addSignalWithFeedback({
            id: `tp-${sym}-${now}`,
            type: 'opportunity',
            symbol: sym,
            message: `${sym} +${pnlPct.toFixed(1)}% unrealized at ${leverage}x. Consider taking partial profit (25-50%) to lock in gains. Entry $${entry.toFixed(4)} → Mark $${mark.toFixed(4)}.`,
            timestamp: now,
            venues: ['sentinel-position'],
          });
        }

        // ── Underwater position with high leverage ──
        if (pnlPct < -15 && leverage >= 50 && !hasRecentDuplicate(`uw-${sym}`)) {
          addSignalWithFeedback({
            id: `uw-${sym}-${now}`,
            type: 'alert',
            symbol: sym,
            message: `${sym} ${side.toUpperCase()} ${leverage}x — ${pnlPct.toFixed(1)}% drawdown. Risk of cascade liquidation. Evaluate reducing leverage or hedging.`,
            timestamp: now,
            venues: ['sentinel-position'],
          });
        }
      }

      // ── Portfolio-level alerts ──
      if (result.positions.length >= 3 && !hasRecentDuplicate('portfolio-exposure')) {
        const totalNotional = result.total_notional || 0;
        const avgLeverage = result.positions.reduce((sum, p) => sum + (p.leverage || 1), 0) / result.positions.length;
        if (avgLeverage > 100) {
          addSignalWithFeedback({
            id: `portfolio-exposure-${now}`,
            type: 'alert',
            symbol: 'PORTFOLIO',
            message: `${result.positions.length} open positions with avg ${avgLeverage.toFixed(0)}x leverage. Total notional $${totalNotional.toFixed(0)}. High concentration risk — diversify or reduce.`,
            timestamp: now,
            venues: ['sentinel-portfolio'],
          });
        }
      }

      // ── Feed position data to Brain learning loop ──
      // Sentinel learns the trader's style: leverage preferences, win patterns, risk tolerance
      if (user?.id && result.positions.length > 0) {
        brainAPI.learnFromPositions({
          user_id: user.id,
          positions: result.positions.map((p) => ({
            symbol: p.symbol,
            side: p.side,
            leverage: p.leverage,
            pnlPct: p.pnlPct,
            entryPrice: p.entryPrice,
            markPrice: p.markPrice,
          })),
          total_unrealized_pnl: result.total_unrealized_pnl,
        }).catch(() => {});
      }
    } catch {
      // Position check is best-effort; don't block signal flow
    }
  }, [addSignalWithFeedback, hasRecentDuplicate]);

  const fetchSignals = useCallback(async () => {
    if (Date.now() < nextFetchAllowedAtRef.current) return;

    const state = useStore.getState();
    const { watchlist, subscription } = state;
    const tierPolicy = SIGNAL_POLICY[subscription] ?? SIGNAL_POLICY.free;
    const canUseAdvancedSignals = subscription !== 'free';
    const allowedPairs = getAllowedPairs(watchlist, subscription);

    // ── Check position-based alerts (runs every 60s regardless of tier) ──
    checkPositionAlerts();

    // ── Arbitrage signals scan ALL watchlist pairs (available to every tier) ──
    try {
      const results = await Promise.allSettled(
        watchlist.map(async (symbol) => {
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
    } catch {
      // Arb fetch failed — continue with other signal types
    }

    // ── Risk-based directional signals (separate try/catch for resilience) ──
    try {
      if (!canUseAdvancedSignals && allowedPairs.length > 0) {
        // Free-tier baseline directional hint
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
    } catch {
      // Risk report failed for free tier — non-blocking
    }

    // ── Pro+ directional signals from composite risk framework ──
    if (canUseAdvancedSignals) {
      try {
        const directionalSymbols = allowedPairs.slice(0, tierPolicy.directionalLimit);
        const riskSignals = await Promise.allSettled(
          directionalSymbols.map(async (symbol) => {
            const risk = await marketAPI.getRiskReport(symbol);
            return { symbol, risk };
          }),
        );

        riskSignals.forEach((result) => {
          if (result.status !== 'fulfilled') return;
          const { symbol, risk } = result.value;
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
      } catch {
        // Directional signals failed — non-blocking
      }
    }
  }, [addSignalWithFeedback, hasRecentDuplicate, hasAnySignalPrefix, checkPositionAlerts]);

  useEffect(() => {
    const state = useStore.getState();
    const tierPolicy = SIGNAL_POLICY[state.subscription] ?? SIGNAL_POLICY.free;

    // Initial fetch
    fetchSignals();

    const interval = setInterval(fetchSignals, tierPolicy.refreshMs);
    return () => clearInterval(interval);
  }, [fetchSignals]);
}
