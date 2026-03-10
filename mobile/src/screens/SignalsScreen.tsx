import React, { useEffect, useState, useCallback, useRef, useMemo } from 'react';
import {
  View,
  Text,
  StyleSheet,
  FlatList,
  TouchableOpacity,
  RefreshControl,
  Switch,
  Modal,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import { COLORS, SPACING, FONT_SIZES, BORDER_RADIUS } from '../constants/theme';
import { useStore, Signal } from '../store/useStore';
import { marketAPI, brainAPI } from '../services/api';
import * as Haptics from 'expo-haptics';
import * as Notifications from 'expo-notifications';
import { CONFIG } from '../config';

type SignalType = 'all' | 'arbitrage' | 'alert' | 'opportunity';

type TierSignalPolicy = {
  directionalLimit: number;
  allowNewCoinSignals: boolean;
  refreshMs: number;
};

const SIGNAL_REFRESH_MS: Record<string, number> = {
  free: 20000,
  starter: 15000,
  pro: 12000,
  elite: 9000,
  whale: 7000,
};

const RISK_MIN_INTERVAL_MS = 30000;
export default function SignalsScreen() {
  const { signals, addSignal, clearSignals, watchlist, settings, subscription, marketData, user } = useStore();
  const [refreshing, setRefreshing] = useState(false);
  const [filter, setFilter] = useState<SignalType>('all');
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [selectedSignal, setSelectedSignal] = useState<Signal | null>(null);
  const [modalRefPrice, setModalRefPrice] = useState<number | null>(null);
  const notificationReadyRef = useRef(false);
  const nextFetchAllowedAtRef = useRef(0);
  const riskReportCacheRef = useRef<Record<string, { at: number; value: any }>>({});

  const tierDirectionalLimit = CONFIG.TIER_LIMITS[subscription] ?? CONFIG.TIER_LIMITS.free;
  const canUseAdvancedSignals = subscription !== 'free';
  const tierPolicy: TierSignalPolicy = {
    directionalLimit: Number.isFinite(tierDirectionalLimit) ? Math.max(1, tierDirectionalLimit) : watchlist.length || 1,
    allowNewCoinSignals: subscription !== 'free' && subscription !== 'starter',
    refreshMs: SIGNAL_REFRESH_MS[subscription] ?? SIGNAL_REFRESH_MS.free,
  };

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
    if (subscription === 'free' || !settings.notifications) return;
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
      // ignore notification failures to keep signals flow non-blocking
    }
  }, [settings.notifications, subscription]);

  const addSignalWithFeedback = useCallback((signal: Signal) => {
    addSignal(signal);
    if (settings.hapticFeedback) {
      Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
    }
    maybeNotify(signal);

    if (subscription !== 'free' && user?.id) {
      brainAPI.publishTelegramSignal({
        user_id: user.id,
        tier: subscription,
        signal_type: signal.type,
        symbol: signal.symbol,
        message: signal.message,
        timestamp: signal.timestamp,
      }).catch(() => {
        // best-effort relay; in-app signals should continue even if Telegram is down
      });
    }
  }, [addSignal, maybeNotify, settings.hapticFeedback, subscription, user?.id]);


  const getRiskReportWithMinInterval = useCallback(async (symbol: string) => {
    const cache = riskReportCacheRef.current[symbol];
    const now = Date.now();
    if (cache && now - cache.at < RISK_MIN_INTERVAL_MS) {
      return cache.value;
    }

    const risk = await marketAPI.getRiskReport(symbol);
    riskReportCacheRef.current[symbol] = { at: now, value: risk };
    return risk;
  }, []);

  const fetchSignals = useCallback(async () => {
    if (Date.now() < nextFetchAllowedAtRef.current) {
      return;
    }

    const allowedPairs = getAllowedPairs(watchlist, subscription);

    // ── Arbitrage signals (all pairs, all tiers) — separate try/catch ──
    const SCAN_PAIRS = [
      'BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'XRP/USDT', 'DOGE/USDT',
      'NEAR/USDT', 'AVAX/USDT', 'LINK/USDT', 'DOT/USDT', 'ADA/USDT',
      'MATIC/USDT', 'ARB/USDT', 'OP/USDT', 'SUI/USDT', 'APT/USDT',
      'PEPE/USDT', 'WIF/USDT', 'FET/USDT', 'INJ/USDT', 'TIA/USDT',
    ];
    const arbPairs = [...new Set([...watchlist, ...SCAN_PAIRS])];

    try {
      const results = await Promise.allSettled(
        arbPairs.map(async (symbol) => {
          const arb = await marketAPI.getArbitrage(symbol);
          const signal = marketAPI.detectArbitrageSignal(arb, 0.05);
          return { symbol, arb, signal };
        })
      );

      results.forEach((result) => {
        if (result.status !== 'fulfilled') return;
        const { signal, symbol, arb } = result.value;
        if (signal && !hasRecentDuplicate(`arb-${symbol}`)) {
          addSignalWithFeedback({ ...signal, id: `arb-${symbol}-${Date.now()}` });
        }

        // Micro-spread alerts for monitored pairs
        if (!signal && arb.best_bid && arb.best_ask && arb.spread !== null) {
          const spreadPct = Math.abs(arb.spread / arb.best_ask) * 100;
          const sym = symbol.replace('/USDT', '');
          if (spreadPct > 0.02 && !hasRecentDuplicate(`spread-${sym}`)) {
            addSignalWithFeedback({
              id: `spread-${sym}-${Date.now()}`, type: 'arbitrage', symbol: sym,
              message: `${sym} spread ${spreadPct.toFixed(3)}% — ${arb.best_ask_venue} $${arb.best_ask.toFixed(4)} / ${arb.best_bid_venue} $${arb.best_bid.toFixed(4)}. Monitor for widening.`,
              profit: spreadPct, timestamp: Date.now(),
              venues: [arb.best_ask_venue, arb.best_bid_venue],
            });
          }
        }

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
    } catch (error) {
      _handleFetchError(error);
    }

    // ── Free-tier baseline risk hint — separate try/catch ──
    try {
      if (!canUseAdvancedSignals && allowedPairs.length > 0) {
        const baseSymbol = allowedPairs[0];
        const prefix = `free-risk-${baseSymbol}`;
        if (!hasAnySignalPrefix(prefix)) {
          const risk = await getRiskReportWithMinInterval(baseSymbol);
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
      // non-blocking
    }

      // Pro+ directional signals from composite risk framework
      if (canUseAdvancedSignals) {
        const directionalSymbols = watchlist.slice(0, tierPolicy.directionalLimit);
        const riskSettled = await Promise.allSettled(
          directionalSymbols.map(async (symbol) => ({ symbol, risk: await getRiskReportWithMinInterval(symbol) }))
        );

        riskSettled.forEach((result) => {
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
      } catch (error) {
        _handleFetchError(error);
      }
    }

    function _handleFetchError(error: unknown) {
      const status = (error as any)?.response?.status;
      const isNetworkError = (error as any)?.message === 'Network Error';
      if (status === 429) {
        nextFetchAllowedAtRef.current = Date.now() + 60000;
      } else if (isNetworkError) {
        nextFetchAllowedAtRef.current = Date.now() + 15000;
      }
    }
  }, [watchlist, addSignalWithFeedback, canUseAdvancedSignals, hasRecentDuplicate, hasAnySignalPrefix, tierPolicy.allowNewCoinSignals, tierPolicy.directionalLimit, getRiskReportWithMinInterval]);


  useEffect(() => {
    let cancelled = false;

    const loadModalRefPrice = async () => {
      if (!selectedSignal) {
        setModalRefPrice(null);
        return;
      }

      // Signal symbol might be "PEPE" or "PEPE/USDT" — try both
      const rawSym = selectedSignal.symbol;
      const fullSym = rawSym.includes('/') ? rawSym : `${rawSym}/USDT`;
      const shortSym = rawSym.replace('/USDT', '');

      const cached = marketData[fullSym] || marketData[shortSym] || marketData[rawSym];
      const cachedRef = cached?.bestAsk || cached?.bestBid || cached?.prices?.[0]?.price || null;
      if (cachedRef) {
        setModalRefPrice(cachedRef);
        return;
      }

      // Also try extracting price from the signal message itself (e.g. "at $0.00001234")
      const priceMatch = selectedSignal.message.match(/\$([0-9]+\.?[0-9]*)/);
      const msgPrice = priceMatch ? parseFloat(priceMatch[1]) : null;

      try {
        const snapshot = await marketAPI.getSnapshot(fullSym);
        const fresh = snapshot.venues.find((v) => v.last !== null)?.last ?? null;
        if (!cancelled) setModalRefPrice(fresh || msgPrice);
      } catch {
        // Fallback: use price from the signal message
        if (!cancelled) setModalRefPrice(msgPrice);
      }
    };

    loadModalRefPrice();
    return () => {
      cancelled = true;
    };
  }, [selectedSignal, marketData]);

  const inferTradePlan = useCallback((signal: Signal) => {
    const isShort = /short|defensive|bear/i.test(signal.message);
    const isPositionAlert = signal.venues?.includes('sentinel-position') || signal.venues?.includes('sentinel-portfolio');
    const base = Math.abs(signal.profit ?? 1.2);
    const highVolatility = /critical|caution|warning|volatile|risk\s[7-9]|risk\s10/i.test(signal.message);
    const liquidityTight = /limited|early listing|low liquidity|thin/i.test(signal.message);
    const isHighLeverage = /\d{3}x|[1-9]\d{2}x/.test(signal.message); // 100x+
    const isExtremeLeerage = /[2-3]\d{2}x/.test(signal.message); // 200x-300x

    // Extract leverage from position alerts (e.g., "145x cross", "235x")
    const leverageMatch = signal.message.match(/(\d+)x\s*(cross|isolated)?/i);
    const detectedLeverage = leverageMatch ? parseInt(leverageMatch[1], 10) : 0;

    // Dynamic leverage suggestion based on signal context
    let leverage: string;
    if (isPositionAlert && detectedLeverage > 0) {
      // For position alerts, show the actual leverage and suggest adjustment
      leverage = `Current: ${detectedLeverage}x`;
    } else if (isExtremeLeerage) {
      leverage = '50x-150x (scale down from current extreme)';
    } else if (isHighLeverage) {
      leverage = '25x-75x (reduce from high leverage zone)';
    } else if (liquidityTight) {
      leverage = '5x-20x (thin liquidity — lower exposure)';
    } else if (highVolatility) {
      leverage = '10x-50x (volatility elevated — manage size)';
    } else {
      leverage = '20x-100x (adjust based on conviction & structure)';
    }

    const entry = highVolatility
      ? 'Scale-in around key S/R zones (avoid full-size market entry)'
      : 'Market / nearest support-resistance retest';
    const sl = (isShort ? base * 0.8 : base);
    const tp1 = (base * 1.1);
    const tp2 = (base * 1.8);
    const leverage = liquidityTight ? '1x-2x' : (highVolatility ? '2x-3x' : '3x-5x');
    const validationWindow = highVolatility ? '15-45 min' : '30-120 min';

    const symbolMarket = marketData[signal.symbol];
    const refPrice = modalRefPrice || symbolMarket?.bestAsk || symbolMarket?.bestBid || symbolMarket?.prices?.[0]?.price;
    const toAbsPrice = (pct: number, target: 'sl' | 'tp') => {
      if (!refPrice) return null;
      const factor = pct / 100;
      if (target === 'sl') {
        return isShort ? refPrice * (1 + factor) : refPrice * (1 - factor);
      }
      return isShort ? refPrice * (1 - factor) : refPrice * (1 + factor);
    };

    let note: string;
    if (isPositionAlert) {
      if (detectedLeverage >= 200) {
        note = `Extreme leverage (${detectedLeverage}x) — use incremental leverage adjustments to protect position. Trail stop at breakeven once in profit.`;
      } else if (detectedLeverage >= 100) {
        note = `High leverage (${detectedLeverage}x) — tighten stops progressively. Consider partial close at TP1 to reduce risk.`;
      } else {
        note = 'Position management signal — evaluate current exposure and adjust accordingly.';
      }
    } else if (liquidityTight) {
      note = 'Lower leverage due to thinner liquidity / higher slippage risk.';
    } else if (highVolatility) {
      note = 'Volatility elevated — scale in, use tighter stops, and consider splitting across leverage tiers.';
    } else {
      note = 'Standard risk profile. Adjust leverage based on conviction — increase on strong structure, reduce on uncertainty.';
    }

    return {
      side: isShort ? 'SHORT bias' : 'LONG bias',
      entry,
      entryPrice: refPrice ? (refPrice >= 1 ? `$${refPrice.toFixed(4)}` : `$${refPrice.toPrecision(4)}`) : 'N/A',
      sl: `${sl.toFixed(2)}%`,
      tp1: `${tp1.toFixed(2)}%`,
      tp2: `${tp2.toFixed(2)}%`,
      slPrice: toAbsPrice(sl, 'sl'),
      tp1Price: toAbsPrice(tp1, 'tp'),
      tp2Price: toAbsPrice(tp2, 'tp'),
      leverage,
      validationWindow,
      note,
    };
  }, [marketData, modalRefPrice]);

  useEffect(() => {
    fetchSignals();
    let interval: ReturnType<typeof setInterval>;

    if (autoRefresh) {
      interval = setInterval(fetchSignals, tierPolicy.refreshMs);
    }

    return () => {
      if (interval) clearInterval(interval);
    };
  }, [fetchSignals, autoRefresh, tierPolicy.refreshMs]);

  const onRefresh = useCallback(async () => {
    setRefreshing(true);
    await fetchSignals();
    setRefreshing(false);
  }, [fetchSignals]);

  const filteredSignals = useMemo(() => signals.filter((s) => {
    if (filter === 'all') return true;
    return s.type === filter;
  }), [signals, filter]);

  const getSignalIcon = (type: Signal['type']) => {
    switch (type) {
      case 'arbitrage':
        return { name: 'swap-horizontal', color: COLORS.success };
      case 'alert':
        return { name: 'warning', color: COLORS.warning };
      case 'opportunity':
        return { name: 'trending-up', color: COLORS.primary };
      default:
        return { name: 'information-circle', color: COLORS.info };
    }
  };

  const formatTime = (timestamp: number) => {
    const diff = Date.now() - timestamp;
    const minutes = Math.floor(diff / 60000);
    const hours = Math.floor(diff / 3600000);

    if (minutes < 1) return 'Just now';
    if (minutes < 60) return `${minutes}m ago`;
    if (hours < 24) return `${hours}h ago`;
    return new Date(timestamp).toLocaleDateString();
  };

  const renderSignal = useCallback(({ item }: { item: Signal }) => {
    const icon = getSignalIcon(item.type);

    return (
      <TouchableOpacity style={styles.signalCard} onPress={() => setSelectedSignal(item)}>
        <View style={[styles.signalIcon, { backgroundColor: icon.color + '20' }]}>
          <Ionicons name={icon.name as any} size={24} color={icon.color} />
        </View>
        <View style={styles.signalContent}>
          <View style={styles.signalHeader}>
            <Text style={styles.signalSymbol}>{item.symbol}</Text>
            <Text style={styles.signalTime}>{formatTime(item.timestamp)}</Text>
          </View>
          <Text style={styles.signalMessage}>{item.message}</Text>
          {item.profit && (
            <View style={styles.signalProfit}>
              <Ionicons name="trending-up" size={14} color={COLORS.success} />
              <Text style={styles.signalProfitText}>
                Potential: +{item.profit.toFixed(3)}%
              </Text>
            </View>
          )}
          <View style={styles.signalVenues}>
            {item.venues.map((venue, index) => (
              <View key={index} style={styles.venueBadge}>
                <Text style={styles.venueBadgeText}>{venue}</Text>
              </View>
            ))}
          </View>
        </View>
      </TouchableOpacity>
    );
  }, []);

  const FilterButton = ({ type, label }: { type: SignalType; label: string }) => (
    <TouchableOpacity
      style={[styles.filterButton, filter === type && styles.filterButtonActive]}
      onPress={() => setFilter(type)}
    >
      <Text
        style={[
          styles.filterButtonText,
          filter === type && styles.filterButtonTextActive,
        ]}
      >
        {label}
      </Text>
    </TouchableOpacity>
  );

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      {/* Header */}
      <View style={styles.header}>
        <Text style={styles.title}>Trading Signals</Text>
        <View style={styles.headerRight}>
          <Text style={styles.autoRefreshLabel}>Auto</Text>
          <Switch
            value={autoRefresh}
            onValueChange={setAutoRefresh}
            trackColor={{ false: COLORS.border, true: COLORS.primary }}
            thumbColor={COLORS.text}
          />
        </View>
      </View>

      {/* Subscription Notice */}
      {subscription === 'free' && (
        <View style={styles.subscriptionNotice}>
          <Ionicons name="information-circle-outline" size={16} color={COLORS.info} />
          <Text style={styles.subscriptionNoticeText}>
            Arbitrage scanning: all pairs. Directional signals: BTC only. Upgrade for more pairs & AI insights.
          </Text>
        </View>
      )}

      {subscription === 'starter' && (
        <View style={styles.subscriptionNotice}>
          <Ionicons name="information-circle-outline" size={16} color={COLORS.info} />
          <Text style={styles.subscriptionNoticeText}>
            Arbitrage scanning: all pairs. Directional signals: BTC + ETH. Upgrade to Pro+ for all pairs & new-coin alerts.
          </Text>
        </View>
      )}

      {/* Filters */}
      <View style={styles.filters}>
        <FilterButton type="all" label="All" />
        <FilterButton type="arbitrage" label="Arbitrage" />
        <FilterButton type="alert" label="Alerts" />
        <FilterButton type="opportunity" label="Opportunities" />
      </View>

      {/* Signals List */}
      <FlatList
        data={filteredSignals}
        renderItem={renderSignal}
        keyExtractor={signalKeyExtractor}
        contentContainerStyle={styles.listContent}
        removeClippedSubviews
        maxToRenderPerBatch={10}
        windowSize={7}
        initialNumToRender={8}
        updateCellsBatchingPeriod={50}
        getItemLayout={getSignalItemLayout}
        refreshControl={
          <RefreshControl
            refreshing={refreshing}
            onRefresh={onRefresh}
            tintColor={COLORS.primary}
          />
        }
        ListEmptyComponent={
          <View style={styles.emptyState}>
            <Ionicons name="pulse" size={64} color={COLORS.textMuted} />
            <Text style={styles.emptyStateTitle}>No Signals Yet</Text>
            <Text style={styles.emptyStateText}>
              We're scanning the markets for opportunities. New signals will appear here automatically.
            </Text>
          </View>
        }
        ListHeaderComponent={
          signals.length > 0 ? (
            <View style={styles.listHeader}>
              <Text style={styles.signalCount}>
                {filteredSignals.length} signals
              </Text>
              <TouchableOpacity onPress={clearSignals}>
                <Text style={styles.clearButton}>Clear All</Text>
              </TouchableOpacity>
            </View>
          ) : null
        }
      />

      <Modal visible={!!selectedSignal} transparent animationType="slide" onRequestClose={() => setSelectedSignal(null)}>
        <View style={styles.modalOverlay}>
          <View style={styles.modalCard}>
            {selectedSignal && (
              <>
                <View style={styles.modalHeader}>
                  <Text style={styles.modalTitle}>{selectedSignal.symbol}</Text>
                  <TouchableOpacity onPress={() => setSelectedSignal(null)}>
                    <Ionicons name="close" size={22} color={COLORS.textMuted} />
                  </TouchableOpacity>
                </View>
                <Text style={styles.modalMessage}>{selectedSignal.message}</Text>
                <View style={styles.planBox}>
                  {(() => {
                    const plan = inferTradePlan(selectedSignal);
                    return (
                      <>
                        <Text style={styles.planTitle}>{plan.side}</Text>
                        <Text style={styles.planLine}>Entry: {plan.entry}</Text>
                        <Text style={styles.planLine}>Entry ref price: {plan.entryPrice}</Text>
                        <Text style={styles.planLine}>SL: {plan.sl}{plan.slPrice ? ` (${fmtPrice(plan.slPrice)})` : ''}</Text>
                        <Text style={styles.planLine}>TP1: {plan.tp1}{plan.tp1Price ? ` (${fmtPrice(plan.tp1Price)})` : ''}</Text>
                        <Text style={styles.planLine}>TP2: {plan.tp2}{plan.tp2Price ? ` (${fmtPrice(plan.tp2Price)})` : ''}</Text>
                        <Text style={styles.planLine}>Leverage: {plan.leverage}</Text>
                        <Text style={styles.planLine}>Validation window: {plan.validationWindow}</Text>
                        <Text style={styles.planHint}>{plan.note}</Text>
                        <Text style={styles.planHint}>Generated: {new Date(selectedSignal.timestamp).toLocaleString()}</Text>
                      </>
                    );
                  })()}
                </View>
              </>
            )}
          </View>
        </View>
      </Modal>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: COLORS.background,
  },
  header: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    paddingHorizontal: SPACING.lg,
    paddingVertical: SPACING.md,
  },
  title: {
    fontSize: FONT_SIZES.xxl,
    fontWeight: '700',
    color: COLORS.text,
  },
  headerRight: {
    flexDirection: 'row',
    alignItems: 'center',
  },
  autoRefreshLabel: {
    fontSize: FONT_SIZES.sm,
    color: COLORS.textSecondary,
    marginRight: SPACING.sm,
  },
  subscriptionNotice: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: COLORS.warning + '20',
    marginHorizontal: SPACING.lg,
    padding: SPACING.md,
    borderRadius: BORDER_RADIUS.md,
    marginBottom: SPACING.md,
  },
  subscriptionNoticeText: {
    flex: 1,
    fontSize: FONT_SIZES.sm,
    color: COLORS.warning,
    marginLeft: SPACING.sm,
  },
  filters: {
    flexDirection: 'row',
    paddingHorizontal: SPACING.lg,
    marginBottom: SPACING.md,
    gap: SPACING.sm,
  },
  filterButton: {
    paddingHorizontal: SPACING.md,
    paddingVertical: SPACING.sm,
    borderRadius: BORDER_RADIUS.full,
    backgroundColor: COLORS.surface,
    borderWidth: 1,
    borderColor: COLORS.border,
  },
  filterButtonActive: {
    backgroundColor: COLORS.primary,
    borderColor: COLORS.primary,
  },
  filterButtonText: {
    fontSize: FONT_SIZES.sm,
    color: COLORS.textSecondary,
    fontWeight: '500',
  },
  filterButtonTextActive: {
    color: COLORS.text,
  },
  listContent: {
    paddingHorizontal: SPACING.lg,
    paddingBottom: SPACING.xxl,
  },
  listHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: SPACING.md,
  },
  signalCount: {
    fontSize: FONT_SIZES.sm,
    color: COLORS.textMuted,
  },
  clearButton: {
    fontSize: FONT_SIZES.sm,
    color: COLORS.error,
    fontWeight: '500',
  },
  signalCard: {
    flexDirection: 'row',
    backgroundColor: COLORS.surface,
    borderRadius: BORDER_RADIUS.lg,
    padding: SPACING.md,
    marginBottom: SPACING.md,
    borderWidth: 1,
    borderColor: COLORS.border,
  },
  signalIcon: {
    width: 48,
    height: 48,
    borderRadius: BORDER_RADIUS.md,
    justifyContent: 'center',
    alignItems: 'center',
    marginRight: SPACING.md,
  },
  signalContent: {
    flex: 1,
  },
  signalHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: SPACING.xs,
  },
  signalSymbol: {
    fontSize: FONT_SIZES.lg,
    fontWeight: '600',
    color: COLORS.text,
  },
  signalTime: {
    fontSize: FONT_SIZES.xs,
    color: COLORS.textMuted,
  },
  signalMessage: {
    fontSize: FONT_SIZES.md,
    color: COLORS.textSecondary,
    lineHeight: 20,
    marginBottom: SPACING.sm,
  },
  signalProfit: {
    flexDirection: 'row',
    alignItems: 'center',
    marginBottom: SPACING.sm,
  },
  signalProfitText: {
    fontSize: FONT_SIZES.sm,
    color: COLORS.success,
    fontWeight: '600',
    marginLeft: SPACING.xs,
  },
  signalVenues: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: SPACING.xs,
  },
  venueBadge: {
    backgroundColor: COLORS.backgroundCard,
    paddingHorizontal: SPACING.sm,
    paddingVertical: 2,
    borderRadius: BORDER_RADIUS.sm,
  },
  venueBadgeText: {
    fontSize: FONT_SIZES.xs,
    color: COLORS.textMuted,
    fontWeight: '500',
  },
  emptyState: {
    alignItems: 'center',
    justifyContent: 'center',
    paddingVertical: SPACING.xxl * 2,
    paddingHorizontal: SPACING.xl,
  },
  emptyStateTitle: {
    fontSize: FONT_SIZES.xl,
    fontWeight: '600',
    color: COLORS.text,
    marginTop: SPACING.lg,
    marginBottom: SPACING.sm,
  },
  emptyStateText: {
    fontSize: FONT_SIZES.md,
    color: COLORS.textMuted,
    textAlign: 'center',
    lineHeight: 22,
  },
  modalOverlay: {
    flex: 1,
    justifyContent: 'flex-end',
    backgroundColor: 'rgba(0,0,0,0.55)',
  },
  modalCard: {
    backgroundColor: COLORS.surface,
    borderTopLeftRadius: BORDER_RADIUS.xl,
    borderTopRightRadius: BORDER_RADIUS.xl,
    padding: SPACING.lg,
    borderWidth: 1,
    borderColor: COLORS.border,
  },
  modalHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: SPACING.sm,
  },
  modalTitle: {
    fontSize: FONT_SIZES.xl,
    fontWeight: '700',
    color: COLORS.text,
  },
  modalMessage: {
    color: COLORS.textSecondary,
    lineHeight: 22,
    fontSize: FONT_SIZES.md,
    marginBottom: SPACING.md,
  },
  planBox: {
    backgroundColor: COLORS.backgroundCard,
    borderRadius: BORDER_RADIUS.md,
    padding: SPACING.md,
  },
  planTitle: {
    color: COLORS.primary,
    fontWeight: '700',
    marginBottom: SPACING.xs,
  },
  planLine: {
    color: COLORS.textSecondary,
    fontSize: FONT_SIZES.sm,
    marginBottom: 2,
  },
  planHint: {
    color: COLORS.textMuted,
    fontSize: FONT_SIZES.xs,
    marginTop: SPACING.xs,
    lineHeight: 16,
  },
});
