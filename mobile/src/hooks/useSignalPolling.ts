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

// Extra pairs Sentinel scans beyond the user's watchlist
const SENTINEL_SCAN_PAIRS = [
  'BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'XRP/USDT', 'DOGE/USDT',
  'NEAR/USDT', 'AVAX/USDT', 'LINK/USDT', 'DOT/USDT', 'ADA/USDT',
  'MATIC/USDT', 'ARB/USDT', 'OP/USDT', 'SUI/USDT', 'APT/USDT',
  'PEPE/USDT', 'WIF/USDT', 'FET/USDT', 'INJ/USDT', 'TIA/USDT',
  'RENDER/USDT', 'FIL/USDT', 'ATOM/USDT', 'LTC/USDT',
];

/** Tier-based pair access for directional signals: free=BTC only, starter=BTC+ETH, pro+=all */
export function getAllowedPairs(watchlist: string[], tier: string): string[] {
  if (tier === 'free') {
    return watchlist.filter((s) => s.startsWith('BTC'));
  }
  if (tier === 'starter') {
    return watchlist.filter((s) => s.startsWith('BTC') || s.startsWith('ETH'));
  }
  return watchlist;
}

function leverageSuggestion(leverage: number, pnlPct: number, liqDistance: number): string {
  if (leverage >= 150 && pnlPct > 50) {
    return `Take partial profit — ${leverage}x with +${pnlPct.toFixed(1)}% unrealized. Lock in gains.`;
  }
  if (leverage >= 150 && liqDistance < 5) {
    return `Liquidation ${liqDistance.toFixed(1)}% away at ${leverage}x! Reduce leverage or add margin.`;
  }
  if (leverage >= 50 && pnlPct < -10) {
    return `Position underwater at ${leverage}x. Consider reducing size.`;
  }
  if (leverage >= 150) {
    return `Running ${leverage}x — trail stops aggressively.`;
  }
  if (leverage >= 100) {
    return `${leverage}x active. Monitor liq distance and scale out at key levels.`;
  }
  return '';
}

export function useSignalPolling() {
  const nextFetchAllowedAtRef = useRef(0);
  const notificationReadyRef = useRef(false);
  const positionCheckRef = useRef(0);
  const techScanRef = useRef(0); // throttle technical scans

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
      // ignore
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

  // ── Position-Aware Alerts ──────────────────────────────────────────────────
  const checkPositionAlerts = useCallback(async () => {
    const now = Date.now();
    if (now - positionCheckRef.current < 60000) return;
    positionCheckRef.current = now;

    const state = useStore.getState();
    const { autoTrader, user } = state;
    const config = autoTrader?.config;
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

        let liqDistance = 100;
        if (liq > 0 && mark > 0) {
          liqDistance = side === 'long'
            ? ((mark - liq) / mark) * 100
            : ((liq - mark) / mark) * 100;
        }

        if (liqDistance < 3 && !hasRecentDuplicate(`liq-warn-${sym}`)) {
          addSignalWithFeedback({
            id: `liq-warn-${sym}-${now}`, type: 'alert', symbol: sym,
            message: `LIQUIDATION WARNING: ${sym} ${side.toUpperCase()} ${leverage}x — ${liqDistance.toFixed(1)}% from liq at $${liq.toFixed(4)}!`,
            timestamp: now, venues: ['sentinel-position'],
          });
        }

        if (leverage >= 100 && !hasRecentDuplicate(`lev-${sym}`)) {
          const suggestion = leverageSuggestion(leverage, pnlPct, liqDistance);
          if (suggestion) {
            addSignalWithFeedback({
              id: `lev-${sym}-${now}`, type: pnlPct > 30 ? 'opportunity' : 'alert', symbol: sym,
              message: `${sym} ${side.toUpperCase()} ${leverage}x cross — ${suggestion}`,
              timestamp: now, venues: ['sentinel-position'],
            });
          }
        }

        if (pnlPct > 50 && !hasRecentDuplicate(`tp-${sym}`)) {
          addSignalWithFeedback({
            id: `tp-${sym}-${now}`, type: 'opportunity', symbol: sym,
            message: `${sym} +${pnlPct.toFixed(1)}% unrealized at ${leverage}x. Take partial profit. Entry $${entry.toFixed(4)} → Mark $${mark.toFixed(4)}.`,
            timestamp: now, venues: ['sentinel-position'],
          });
        }

        if (pnlPct < -15 && leverage >= 50 && !hasRecentDuplicate(`uw-${sym}`)) {
          addSignalWithFeedback({
            id: `uw-${sym}-${now}`, type: 'alert', symbol: sym,
            message: `${sym} ${side.toUpperCase()} ${leverage}x — ${pnlPct.toFixed(1)}% drawdown. Evaluate reducing or hedging.`,
            timestamp: now, venues: ['sentinel-position'],
          });
        }
      }

      if (result.positions.length >= 3 && !hasRecentDuplicate('portfolio-exposure')) {
        const avgLev = result.positions.reduce((s, p) => s + (p.leverage || 1), 0) / result.positions.length;
        if (avgLev > 100) {
          addSignalWithFeedback({
            id: `portfolio-exposure-${now}`, type: 'alert', symbol: 'PORTFOLIO',
            message: `${result.positions.length} positions, avg ${avgLev.toFixed(0)}x leverage. High concentration — diversify or reduce.`,
            timestamp: now, venues: ['sentinel-portfolio'],
          });
        }
      }

      // Feed to Brain learning loop
      if (user?.id && result.positions.length > 0) {
        brainAPI.learnFromPositions({
          user_id: user.id,
          positions: result.positions.map((p) => ({
            symbol: p.symbol, side: p.side, leverage: p.leverage,
            pnlPct: p.pnlPct, entryPrice: p.entryPrice, markPrice: p.markPrice,
          })),
          total_unrealized_pnl: result.total_unrealized_pnl,
        }).catch(() => {});
      }
    } catch {
      // best-effort
    }
  }, [addSignalWithFeedback, hasRecentDuplicate]);

  // ── Sentinel Technical Scanner — its own signals from TA ──────────────────
  const scanTechnicals = useCallback(async () => {
    const now = Date.now();
    // Technical scans every 30s (heavier than arb, lighter than positions)
    if (now - techScanRef.current < 30000) return;
    techScanRef.current = now;

    const state = useStore.getState();
    const { subscription } = state;
    if (subscription === 'free') return; // Only paid tiers get Sentinel TA signals

    // Merge watchlist + sentinel scan pairs (unique)
    const allPairs = [...new Set([...state.watchlist, ...SENTINEL_SCAN_PAIRS])];
    // Pick a batch to scan each cycle (rotate through)
    const batchSize = subscription === 'whale' ? 8 : (subscription === 'elite' ? 5 : 3);
    const offset = Math.floor((now / 30000) % Math.ceil(allPairs.length / batchSize)) * batchSize;
    const batch = allPairs.slice(offset, offset + batchSize);

    const results = await Promise.allSettled(
      batch.map(async (symbol) => {
        const tech = await marketAPI.getTechnicals(symbol);
        return { symbol, tech };
      }),
    );

    for (const result of results) {
      if (result.status !== 'fulfilled') continue;
      const { symbol, tech } = result.value;
      if (!tech.ok || tech.error) continue;

      const sym = symbol.replace('/USDT', '');
      const price = tech.current_price;
      const rsi = tech.rsi_14;

      // ── RSI Extreme signals ──
      if (rsi <= 25 && !hasRecentDuplicate(`rsi-os-${sym}`)) {
        addSignalWithFeedback({
          id: `rsi-os-${sym}-${now}`, type: 'opportunity', symbol: sym,
          message: `${sym} RSI ${rsi.toFixed(1)} — OVERSOLD. Potential bounce opportunity at $${price.toFixed(4)}. ${tech.bollinger_bands?.signal === 'oversold' ? 'BB confirms oversold.' : ''}`,
          timestamp: now, venues: ['sentinel-ta'],
        });
      }
      if (rsi >= 78 && !hasRecentDuplicate(`rsi-ob-${sym}`)) {
        addSignalWithFeedback({
          id: `rsi-ob-${sym}-${now}`, type: 'alert', symbol: sym,
          message: `${sym} RSI ${rsi.toFixed(1)} — OVERBOUGHT at $${price.toFixed(4)}. Watch for reversal or short entry. ${tech.macd?.trend === 'bearish' ? 'MACD bearish confirms.' : ''}`,
          timestamp: now, venues: ['sentinel-ta'],
        });
      }

      // ── MACD Crossover signals ──
      if (tech.macd) {
        const hist = tech.macd.histogram;
        if (tech.macd.trend === 'bullish' && hist > 0 && hist < 0.5 && !hasRecentDuplicate(`macd-bull-${sym}`)) {
          addSignalWithFeedback({
            id: `macd-bull-${sym}-${now}`, type: 'opportunity', symbol: sym,
            message: `${sym} MACD bullish crossover detected at $${price.toFixed(4)}. Histogram turning positive — early momentum shift.`,
            timestamp: now, venues: ['sentinel-ta'],
          });
        }
        if (tech.macd.trend === 'bearish' && hist < 0 && hist > -0.5 && !hasRecentDuplicate(`macd-bear-${sym}`)) {
          addSignalWithFeedback({
            id: `macd-bear-${sym}-${now}`, type: 'alert', symbol: sym,
            message: `${sym} MACD bearish crossover at $${price.toFixed(4)}. Momentum fading — protect longs or look for shorts.`,
            timestamp: now, venues: ['sentinel-ta'],
          });
        }
      }

      // ── Bollinger Band squeeze / breakout ──
      if (tech.bollinger_bands) {
        const bb = tech.bollinger_bands;
        const bandwidth = ((bb.upper - bb.lower) / bb.middle) * 100;
        if (bandwidth < 3 && !hasRecentDuplicate(`bb-squeeze-${sym}`)) {
          addSignalWithFeedback({
            id: `bb-squeeze-${sym}-${now}`, type: 'opportunity', symbol: sym,
            message: `${sym} Bollinger Band SQUEEZE — bandwidth ${bandwidth.toFixed(1)}% at $${price.toFixed(4)}. Breakout imminent. Watch direction for entry.`,
            timestamp: now, venues: ['sentinel-ta'],
          });
        }
        if (bb.pct_b > 1.05 && !hasRecentDuplicate(`bb-break-up-${sym}`)) {
          addSignalWithFeedback({
            id: `bb-break-up-${sym}-${now}`, type: 'opportunity', symbol: sym,
            message: `${sym} breaking ABOVE upper BB at $${price.toFixed(4)} (%B: ${bb.pct_b.toFixed(2)}). Strong momentum — trend continuation or exhaustion?`,
            timestamp: now, venues: ['sentinel-ta'],
          });
        }
        if (bb.pct_b < -0.05 && !hasRecentDuplicate(`bb-break-dn-${sym}`)) {
          addSignalWithFeedback({
            id: `bb-break-dn-${sym}-${now}`, type: 'alert', symbol: sym,
            message: `${sym} breaking BELOW lower BB at $${price.toFixed(4)} (%B: ${bb.pct_b.toFixed(2)}). Panic sell or bounce zone?`,
            timestamp: now, venues: ['sentinel-ta'],
          });
        }
      }

      // ── EMA crossover ──
      if (tech.ema) {
        if (tech.ema.cross === 'golden_cross' && !hasRecentDuplicate(`ema-golden-${sym}`)) {
          addSignalWithFeedback({
            id: `ema-golden-${sym}-${now}`, type: 'opportunity', symbol: sym,
            message: `${sym} GOLDEN CROSS — EMA20 crossed above EMA50 at $${price.toFixed(4)}. Bullish trend confirmation.`,
            timestamp: now, venues: ['sentinel-ta'],
          });
        }
        if (tech.ema.cross === 'death_cross' && !hasRecentDuplicate(`ema-death-${sym}`)) {
          addSignalWithFeedback({
            id: `ema-death-${sym}-${now}`, type: 'alert', symbol: sym,
            message: `${sym} DEATH CROSS — EMA20 below EMA50 at $${price.toFixed(4)}. Bearish structure — reduce longs.`,
            timestamp: now, venues: ['sentinel-ta'],
          });
        }
      }

      // ── Williams %R extreme ──
      if (tech.williams_r) {
        const wr = tech.williams_r.value;
        if (wr < -90 && !hasRecentDuplicate(`wr-os-${sym}`)) {
          addSignalWithFeedback({
            id: `wr-os-${sym}-${now}`, type: 'opportunity', symbol: sym,
            message: `${sym} Williams %R at ${wr.toFixed(0)} — deeply oversold. Reversal watch at $${price.toFixed(4)}.`,
            timestamp: now, venues: ['sentinel-ta'],
          });
        }
        if (wr > -10 && !hasRecentDuplicate(`wr-ob-${sym}`)) {
          addSignalWithFeedback({
            id: `wr-ob-${sym}-${now}`, type: 'alert', symbol: sym,
            message: `${sym} Williams %R at ${wr.toFixed(0)} — extremely overbought. Distribution risk at $${price.toFixed(4)}.`,
            timestamp: now, venues: ['sentinel-ta'],
          });
        }
      }

      // ── Fibonacci proximity ──
      if (tech.nearest_fib && Math.abs(tech.nearest_fib.distance_pct) < 1 && !hasRecentDuplicate(`fib-${sym}`)) {
        addSignalWithFeedback({
          id: `fib-${sym}-${now}`, type: 'opportunity', symbol: sym,
          message: `${sym} at Fibonacci ${tech.nearest_fib.level} ($${tech.nearest_fib.price.toFixed(4)}) — ${Math.abs(tech.nearest_fib.distance_pct).toFixed(1)}% away. Key S/R level for entry/exit.`,
          timestamp: now, venues: ['sentinel-ta'],
        });
      }
    }
  }, [addSignalWithFeedback, hasRecentDuplicate]);

  const fetchSignals = useCallback(async () => {
    if (Date.now() < nextFetchAllowedAtRef.current) return;

    const state = useStore.getState();
    const { watchlist, subscription } = state;
    const tierPolicy = SIGNAL_POLICY[subscription] ?? SIGNAL_POLICY.free;
    const canUseAdvancedSignals = subscription !== 'free';
    const allowedPairs = getAllowedPairs(watchlist, subscription);

    // ── Position-based alerts (every 60s) ──
    checkPositionAlerts();

    // ── Sentinel Technical Scanner (every 30s, paid tiers) ──
    try {
      await scanTechnicals();
    } catch {
      // non-blocking
    }

    // ── Arbitrage signals (all pairs, all tiers) ──
    try {
      // Merge watchlist + extra scan pairs for broader arb detection
      const arbPairs = [...new Set([...watchlist, ...SENTINEL_SCAN_PAIRS])];
      const results = await Promise.allSettled(
        arbPairs.map(async (symbol) => {
          const arb = await marketAPI.getArbitrage(symbol);
          const signal = marketAPI.detectArbitrageSignal(arb, 0.05); // Lower threshold to catch more
          return { symbol, arb, signal };
        }),
      );

      results.forEach((result) => {
        if (result.status !== 'fulfilled') return;
        const { signal, symbol, arb } = result.value;

        if (signal && !hasRecentDuplicate(`arb-${symbol}`)) {
          addSignalWithFeedback({ ...signal, id: `arb-${symbol}-${Date.now()}` });
        }

        // Show spread info even without arb signal for monitored pairs
        if (!signal && arb.best_bid && arb.best_ask && arb.spread !== null) {
          const spreadPct = Math.abs(arb.spread / arb.best_ask) * 100;
          if (spreadPct > 0.02 && !hasRecentDuplicate(`spread-${symbol}`)) {
            // Micro-arb alert: spread exists but below trigger — informational
            const sym = symbol.replace('/USDT', '');
            addSignalWithFeedback({
              id: `spread-${sym}-${Date.now()}`, type: 'arbitrage', symbol: sym,
              message: `${sym} spread ${spreadPct.toFixed(3)}% between ${arb.best_ask_venue} ($${arb.best_ask.toFixed(4)}) and ${arb.best_bid_venue} ($${arb.best_bid.toFixed(4)}). Monitor for widening.`,
              profit: spreadPct,
              timestamp: Date.now(),
              venues: [arb.best_ask_venue, arb.best_bid_venue],
            });
          }
        }

        // Pro+ new-coin signals
        if (tierPolicy.allowNewCoinSignals && !hasRecentDuplicate(`newcoin-${symbol}`)) {
          const listedVenue = arb.best_bid_venue || arb.best_ask_venue;
          const noVenue = !arb.best_bid_venue || !arb.best_ask_venue;
          if (listedVenue && noVenue) {
            addSignalWithFeedback({
              id: `newcoin-${symbol}-${Date.now()}`, type: 'opportunity', symbol,
              message: `${symbol} limited venue availability — possible early listing edge.`,
              timestamp: Date.now(), venues: [listedVenue],
            });
          }
        }
      });
    } catch {
      // Arb failed — continue
    }

    // ── Free-tier baseline risk hint ──
    try {
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
            timestamp: Date.now(), venues: ['sentinel-risk'],
          });
        }
      }
    } catch {
      // non-blocking
    }

    // ── Pro+ directional risk signals ──
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
              id: `${prefix}-${Date.now()}`, type: 'alert', symbol,
              message: `SHORT/DEFENSIVE bias: Risk ${risk.composite_score.toFixed(1)}/10 (${level}) — ${risk.recommendation.description}`,
              timestamp: Date.now(), venues: ['sentinel-risk'],
            });
            return;
          }

          if (level === 'NEUTRAL' || action.includes('accumulate') || action.includes('long')) {
            addSignalWithFeedback({
              id: `${prefix}-${Date.now()}`, type: 'opportunity', symbol,
              message: `LONG/ACCUMULATE bias: Risk ${risk.composite_score.toFixed(1)}/10 (${level}) — ${risk.recommendation.description}`,
              timestamp: Date.now(), venues: ['sentinel-risk'],
            });
          }
        });
      } catch {
        // non-blocking
      }
    }
  }, [addSignalWithFeedback, hasRecentDuplicate, hasAnySignalPrefix, checkPositionAlerts, scanTechnicals]);

  useEffect(() => {
    const state = useStore.getState();
    const tierPolicy = SIGNAL_POLICY[state.subscription] ?? SIGNAL_POLICY.free;

    fetchSignals();

    const interval = setInterval(fetchSignals, tierPolicy.refreshMs);
    return () => clearInterval(interval);
  }, [fetchSignals]);
}
