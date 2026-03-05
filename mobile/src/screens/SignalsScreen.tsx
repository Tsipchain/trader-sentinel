import React, { useEffect, useState, useCallback, useRef } from 'react';
import {
  View,
  Text,
  StyleSheet,
  FlatList,
  TouchableOpacity,
  RefreshControl,
  Switch,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import { COLORS, SPACING, FONT_SIZES, BORDER_RADIUS } from '../constants/theme';
import { useStore, Signal } from '../store/useStore';
import { marketAPI, analystAPI, type AnalystBriefing } from '../services/api';
import * as Haptics from 'expo-haptics';

type SignalType = 'all' | 'arbitrage' | 'alert' | 'opportunity';

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

export default function SignalsScreen() {
  const { signals, addSignal, clearSignals, watchlist, settings, subscription } = useStore();
  const [refreshing, setRefreshing] = useState(false);
  const [filter, setFilter] = useState<SignalType>('all');
  const [autoRefresh, setAutoRefresh] = useState(true);
  const nextFetchAllowedAtRef = useRef(0);
  const nextAnalystSignalAtRef = useRef(0);

  const tierPolicy = SIGNAL_POLICY[subscription] ?? SIGNAL_POLICY.free;
  const canUseAdvancedSignals = subscription !== 'free';

  const hasRecentDuplicate = useCallback((idPrefix: string) => {
    const now = Date.now();
    const currentSignals = useStore.getState().signals;
    return currentSignals.some((s) => s.id.startsWith(idPrefix) && now - s.timestamp < 10 * 60 * 1000);
  }, []);

  const fetchSignals = useCallback(async () => {
    if (Date.now() < nextFetchAllowedAtRef.current) {
      return;
    }

    try {
      // Fetch arbitrage data for watchlist (per-symbol fault tolerance)
      const results = await Promise.allSettled(
        watchlist.map(async (symbol) => {
          const arb = await marketAPI.getArbitrage(symbol);
          const signal = marketAPI.detectArbitrageSignal(arb, 0.1);
          return { symbol, arb, signal };
        })
      );

      // Add arbitrage signals
      results.forEach((result) => {
        if (result.status !== 'fulfilled') return;
        const { signal, symbol, arb } = result.value;
        if (signal && !hasRecentDuplicate(`arb-${symbol}`)) {
          addSignal({ ...signal, id: `arb-${symbol}-${Date.now()}` });
          if (settings.hapticFeedback) {
            Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
          }
        }

        // Pro+ signal: πιθανή cross-exchange "listing / availability" ευκαιρία
        if (tierPolicy.allowNewCoinSignals && !hasRecentDuplicate(`newcoin-${symbol}`)) {
          const listedVenue = arb.best_bid_venue || arb.best_ask_venue;
          const noVenue = !arb.best_bid_venue || !arb.best_ask_venue;
          if (listedVenue && noVenue) {
            addSignal({
              id: `newcoin-${symbol}-${Date.now()}`,
              type: 'opportunity',
              symbol,
              message: `${symbol} εμφανίζει περιορισμένη διαθεσιμότητα σε venues — πιθανό early listing edge.`,
              timestamp: Date.now(),
              venues: [listedVenue],
            });
          }
        }
      });


      // Free-tier baseline directional hint so users don't wait "all day" for a signal.
      if (!canUseAdvancedSignals && watchlist.length > 0) {
        const baseSymbol = watchlist[0];
        const prefix = `free-risk-${baseSymbol}`;
        if (!hasRecentDuplicate(prefix)) {
          const risk = await marketAPI.getRiskReport(baseSymbol);
          const lowRisk = risk.composite_score <= 5.5;
          addSignal({
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

      // Analyst pattern signal (throttled) to surface potential market patterns.
      if (Date.now() >= nextAnalystSignalAtRef.current) {
        const analyst = await analystAPI.getBriefing();
        if ((analyst as AnalystBriefing).briefing) {
          const text = (analyst as AnalystBriefing).briefing;
          const lower = text.toLowerCase();
          const trend = lower.includes('accum') || lower.includes('bull') || lower.includes('long')
            ? 'LONG/ACCUMULATE'
            : (lower.includes('distribution') || lower.includes('bear') || lower.includes('short') ? 'SHORT/DEFENSIVE' : 'NEUTRAL');
          const prefix = `analyst-pattern-${trend}`;
          if (!hasRecentDuplicate(prefix)) {
            addSignal({
              id: `${prefix}-${Date.now()}`,
              type: trend === 'SHORT/DEFENSIVE' ? 'alert' : 'opportunity',
              symbol: watchlist[0] ?? 'BTC/USDT',
              message: `Analyst pattern: ${trend} bias — ${text.slice(0, 160)}${text.length > 160 ? '…' : ''}`,
              timestamp: Date.now(),
              venues: ['sentinel-analyst'],
            });
          }
          nextAnalystSignalAtRef.current = Date.now() + 10 * 60 * 1000;
        }
      }
      // Pro+ directional signals from composite risk framework
      if (canUseAdvancedSignals) {
        const directionalSymbols = watchlist.slice(0, tierPolicy.directionalLimit);
        const riskSignals = await Promise.all(
          directionalSymbols.map(async (symbol) => {
            const risk = await marketAPI.getRiskReport(symbol);
            return { symbol, risk };
          })
        );

        riskSignals.forEach(({ symbol, risk }) => {
          const level = risk.recommendation?.level || 'UNKNOWN';
          const action = (risk.recommendation?.action || '').toLowerCase();
          const prefix = `dir-${symbol}-${level}`;

          if (hasRecentDuplicate(prefix)) return;

          if (action.includes('reduce') || action.includes('hedge') || level === 'CAUTION' || level === 'CRITICAL') {
            addSignal({
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
            addSignal({
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
        // Client-side backoff to avoid hammering upstream when rate-limited
        nextFetchAllowedAtRef.current = Date.now() + 60_000;
      } else if (isNetworkError) {
        // transient network issue: short cooldown to avoid retry storms
        nextFetchAllowedAtRef.current = Date.now() + 15_000;
      }

      if (status === 429 || isNetworkError) {
        console.warn('Signals fetch backoff:', status ?? 'network');
      } else {
        console.warn('Signals fetch failed:', (error as any)?.message ?? 'unknown error');
      }
    }
  }, [watchlist, addSignal, settings.hapticFeedback, canUseAdvancedSignals, hasRecentDuplicate, tierPolicy.allowNewCoinSignals, tierPolicy.directionalLimit]);

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

  const filteredSignals = signals.filter((s) => {
    if (filter === 'all') return true;
    return s.type === filter;
  });

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

  const renderSignal = ({ item }: { item: Signal }) => {
    const icon = getSignalIcon(item.type);

    return (
      <TouchableOpacity style={styles.signalCard}>
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
  };

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
          <Ionicons name="lock-closed" size={16} color={COLORS.warning} />
          <Text style={styles.subscriptionNoticeText}>
            Free tier: 1 directional signal + arbitrage. Upgrade for more pairs/faster updates.
          </Text>
        </View>
      )}

      {subscription === 'starter' && (
        <View style={styles.subscriptionNotice}>
          <Ionicons name="information-circle-outline" size={16} color={COLORS.info} />
          <Text style={styles.subscriptionNoticeText}>
            Starter tier: up to 2 directional pairs. Upgrade to Pro+ for new-coin opportunities.
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
        keyExtractor={(item) => item.id}
        contentContainerStyle={styles.listContent}
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
});
