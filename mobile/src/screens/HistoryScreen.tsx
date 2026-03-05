import React, { useState, useCallback, useRef, useEffect } from 'react';
import {
  View, Text, ScrollView, StyleSheet, TouchableOpacity,
  ActivityIndicator, Alert, TextInput,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import { LinearGradient } from 'expo-linear-gradient';
import { COLORS, SPACING, FONT_SIZES, BORDER_RADIUS, SHADOWS } from '../constants/theme';
import { useStore } from '../store/useStore';
import { brainAPI, analystAPI } from '../services/api';
import CONFIG from '../config';
import type { TradeRecord } from '../services/api';

const EXCHANGE_OPTIONS = ['binance', 'bybit', 'okx', 'mexc'];

export default function HistoryScreen() {
  const { user, tradeHistory, setTradeHistory, autoTrader, setAutoTrader, subscription } = useStore();

  const [syncLoading, setSyncLoading] = useState(false);
  const [analysisLoading, setAnalysisLoading] = useState(false);
  const hasShownBrainMisconfigAlert = useRef(false);

  // Exchange credentials for sync (re-use autoTrader config if available)
  const [exchange, setExchange] = useState(autoTrader.config.exchange);
  const [apiKey, setApiKey] = useState(autoTrader.config.apiKey);
  const [apiSecret, setApiSecret] = useState(autoTrader.config.apiSecret);
  const [showSetup, setShowSetup] = useState(false);

  useEffect(() => {
    // Keep History screen credentials in sync with shared AutoTrader config.
    setExchange(autoTrader.config.exchange);
    setApiKey(autoTrader.config.apiKey);
    setApiSecret(autoTrader.config.apiSecret);
  }, [autoTrader.config.exchange, autoTrader.config.apiKey, autoTrader.config.apiSecret]);

  const { trades, stats, lastSynced, aiAnalysis } = tradeHistory;

  const handleSync = useCallback(async () => {
    if (!user?.id) {
      Alert.alert('Not Connected', 'Connect your wallet first.');
      return;
    }
    if (!apiKey || !apiSecret) {
      setShowSetup(true);
      return;
    }
    setSyncLoading(true);
    try {
      // Preflight checks to detect wrong Brain deployment early.
      await brainAPI.checkHealth();
      const serviceCheck = await brainAPI.checkServiceType();
      if (!serviceCheck.isBrain) {
        const reason = serviceCheck.reason || 'unknown';
        if (!hasShownBrainMisconfigAlert.current) {
          hasShownBrainMisconfigAlert.current = true;
          if (__DEV__) {
            console.info(
              `Brain service-type validation warning (${CONFIG.BRAIN_URL}):`,
              reason,
            );
          }
        }
        // Do not hard-block here: continue and let the sync endpoint result decide.
      } else {
        hasShownBrainMisconfigAlert.current = false;
      }
      hasShownBrainMisconfigAlert.current = false;

      // Sync & train model
      const syncResult = await brainAPI.syncTrades({
        user_id: user.id,
        exchange,
        api_key: apiKey,
        api_secret: apiSecret,
        days: 90,
      });

      // Fetch history
      const histResult = await brainAPI.getHistory(user.id, 100);

      // Fetch stats
      const statsResult = await brainAPI.getStats(user.id);

      setTradeHistory({
        trades: histResult.trades ?? [],
        stats: statsResult.ok ? {
          total_trades: statsResult.total_trades,
          win_rate: statsResult.win_rate,
          total_pnl_usd: statsResult.total_pnl_usd,
          avg_pnl_pct: statsResult.avg_pnl_pct,
          best_trade_pct: statsResult.best_trade_pct,
          worst_trade_pct: statsResult.worst_trade_pct,
          most_traded_symbol: statsResult.most_traded_symbol,
        } : null,
        lastSynced: new Date().toISOString(),
      });

      // Persist subscription fingerprint for lifecycle analytics (best effort).
      await brainAPI.registerSubscription({
        user_id: user.id,
        tier: subscription,
        source: 'mobile-history-sync',
        wallet_address: user.walletAddress ?? '',
      }).catch(() => {});

      Alert.alert(
        'Sync Complete',
        syncResult.trade_count
          ? `${syncResult.trade_count} trades synced. Model accuracy: ${((syncResult.model_accuracy ?? 0) * 100).toFixed(1)}%`
          : 'Trade history updated.',
      );
    } catch (err: any) {
      const status = err?.response?.status;
      const detail = err?.response?.data?.detail || err?.response?.data?.message || err?.message;
      if (status === 404) {
        Alert.alert(
          'Sync Failed',
          `Brain route returned 404. Deploy latest sentinel-brain code and verify /api/brain/* routes exist.\nCurrent BRAIN_URL: ${CONFIG.BRAIN_URL}\nAlso verify DISK_PATH points to a writable mounted volume (e.g. /disk).`,
        );
      } else {
        Alert.alert('Sync Failed', detail ? `Could not sync with Brain. ${detail}` : 'Could not reach the Brain service. Check your API keys and try again.');
      }
    } finally {
      setSyncLoading(false);
    }
  }, [user?.id, user?.walletAddress, exchange, apiKey, apiSecret, subscription]);

  const handleAnalyze = useCallback(async () => {
    if (!stats) {
      Alert.alert('No Data', 'Sync your trade history first so the AI has data to analyze.');
      return;
    }
    setAnalysisLoading(true);
    try {
      const prompt =
        `Analyze this trader's performance: Win rate ${(stats.win_rate * 100).toFixed(1)}%, ` +
        `${stats.total_trades} trades, avg P&L ${stats.avg_pnl_pct.toFixed(2)}%, ` +
        `best trade +${stats.best_trade_pct.toFixed(2)}%, worst trade ${stats.worst_trade_pct.toFixed(2)}%, ` +
        `most traded symbol ${stats.most_traded_symbol}. ` +
        `Identify 3 specific weaknesses in this strategy and give concrete improvement suggestions in 5 sentences max.`;

      const result = await analystAPI.ask(prompt);
      const advice = result.answer ?? '';
      setTradeHistory({ aiAnalysis: advice });

      // Persist strategy analysis snapshot in Brain for historical comparisons (best effort).
      await brainAPI.saveAnalysisSnapshot({
        user_id: user?.id ?? 'anonymous',
        kind: 'strategy_advice',
        symbol: stats.most_traded_symbol || 'BTC/USDT',
        content: {
          prompt,
          advice,
          stats,
          generated_at: new Date().toISOString(),
        },
      }).catch(() => {});
    } catch {
      Alert.alert('Analysis Failed', 'Could not reach the AI analyst. Try again.');
    } finally {
      setAnalysisLoading(false);
    }
  }, [stats, user?.id]);

  const winRate = stats ? (stats.win_rate * 100).toFixed(1) : '–';
  const totalPnl = stats?.total_pnl_usd ?? null;
  const pnlColor = totalPnl === null ? COLORS.textMuted : totalPnl >= 0 ? COLORS.success : COLORS.error;

  return (
    <SafeAreaView style={styles.safe} edges={['bottom']}>
      <ScrollView contentContainerStyle={styles.scroll} showsVerticalScrollIndicator={false}>

        {/* ── Stats summary ──────────────────────────────────────── */}
        <View style={styles.statsGrid}>
          <LinearGradient colors={COLORS.gradientCard as [string, string]} style={styles.statCard}>
            <Text style={[styles.statValue, { color: pnlColor }]}>
              {totalPnl !== null ? `${totalPnl >= 0 ? '+' : ''}$${totalPnl.toFixed(0)}` : '–'}
            </Text>
            <Text style={styles.statLabel}>Total P&L</Text>
          </LinearGradient>

          <LinearGradient colors={COLORS.gradientCard as [string, string]} style={styles.statCard}>
            <Text style={[styles.statValue, {
              color: stats ? (stats.win_rate >= 0.5 ? COLORS.success : COLORS.error) : COLORS.textMuted,
            }]}>
              {stats ? `${winRate}%` : '–'}
            </Text>
            <Text style={styles.statLabel}>Win Rate</Text>
          </LinearGradient>

          <LinearGradient colors={COLORS.gradientCard as [string, string]} style={styles.statCard}>
            <Text style={styles.statValue}>{stats?.total_trades ?? '–'}</Text>
            <Text style={styles.statLabel}>Trades</Text>
          </LinearGradient>

          <LinearGradient colors={COLORS.gradientCard as [string, string]} style={styles.statCard}>
            <Text style={[styles.statValue, {
              color: stats ? (stats.avg_pnl_pct >= 0 ? COLORS.success : COLORS.error) : COLORS.textMuted,
            }]}>
              {stats ? `${stats.avg_pnl_pct >= 0 ? '+' : ''}${stats.avg_pnl_pct.toFixed(2)}%` : '–'}
            </Text>
            <Text style={styles.statLabel}>Avg Trade</Text>
          </LinearGradient>
        </View>

        {/* ── Sync section ───────────────────────────────────────── */}
        <Text style={styles.sectionTitle}>Trade History Sync</Text>
        <View style={styles.card}>
          {lastSynced && (
            <Text style={styles.lastSynced}>
              Last synced {new Date(lastSynced).toLocaleDateString()} at{' '}
              {new Date(lastSynced).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
            </Text>
          )}

          {/* Collapsible exchange setup */}
          <TouchableOpacity style={styles.setupHeader} onPress={() => setShowSetup((v) => !v)}>
            <Ionicons name="key-outline" size={16} color={COLORS.primary} />
            <Text style={styles.setupHeaderText}>Exchange API Setup</Text>
            <Ionicons name={showSetup ? 'chevron-up' : 'chevron-down'} size={16} color={COLORS.textMuted} />
          </TouchableOpacity>

          {showSetup && (
            <View style={styles.setupBody}>
              <Text style={styles.fieldLabel}>Exchange</Text>
              <View style={styles.chips}>
                {EXCHANGE_OPTIONS.map((ex) => (
                  <TouchableOpacity
                    key={ex}
                    style={[styles.chip, exchange === ex && styles.chipActive]}
                    onPress={() => {
                      setExchange(ex);
                      setAutoTrader({ config: { ...autoTrader.config, exchange: ex } });
                    }}
                  >
                    <Text style={[styles.chipText, exchange === ex && styles.chipTextActive]}>
                      {ex.toUpperCase()}
                    </Text>
                  </TouchableOpacity>
                ))}
              </View>
              <Text style={[styles.fieldLabel, { marginTop: SPACING.sm }]}>API Key</Text>
              <TextInput
                style={styles.input}
                value={apiKey}
                onChangeText={(value) => {
                  setApiKey(value);
                  setAutoTrader({ config: { ...autoTrader.config, apiKey: value } });
                }}
                placeholder="Read-only key"
                placeholderTextColor={COLORS.textMuted}
                secureTextEntry
              />
              <Text style={[styles.fieldLabel, { marginTop: SPACING.sm }]}>API Secret</Text>
              <TextInput
                style={styles.input}
                value={apiSecret}
                onChangeText={(value) => {
                  setApiSecret(value);
                  setAutoTrader({ config: { ...autoTrader.config, apiSecret: value } });
                }}
                placeholder="API secret"
                placeholderTextColor={COLORS.textMuted}
                secureTextEntry
              />
            </View>
          )}

          <TouchableOpacity style={styles.syncBtn} onPress={handleSync} disabled={syncLoading}>
            {syncLoading
              ? <ActivityIndicator size="small" color={COLORS.text} />
              : <>
                  <Ionicons name="sync-outline" size={18} color={COLORS.text} />
                  <Text style={styles.syncBtnText}>Sync Last 90 Days</Text>
                </>}
          </TouchableOpacity>
        </View>

        {/* ── AI Strategy Advisor ────────────────────────────────── */}
        <Text style={styles.sectionTitle}>AI Strategy Advisor</Text>
        <View style={styles.card}>
          {aiAnalysis ? (
            <>
              <View style={styles.analysisHeader}>
                <Ionicons name="bulb" size={18} color={COLORS.thronosGold} />
                <Text style={styles.analysisTitle}>Strategy Analysis</Text>
              </View>
              <Text style={styles.analysisText}>{aiAnalysis}</Text>
              <TouchableOpacity style={styles.reanalyzeBtn} onPress={handleAnalyze} disabled={analysisLoading}>
                {analysisLoading
                  ? <ActivityIndicator size="small" color={COLORS.primary} />
                  : <Text style={styles.reanalyzeBtnText}>Re-analyze</Text>}
              </TouchableOpacity>
            </>
          ) : (
            <View style={styles.analyzeEmpty}>
              <Ionicons name="analytics-outline" size={40} color={COLORS.textMuted} />
              <Text style={styles.analyzeEmptyTitle}>No Analysis Yet</Text>
              <Text style={styles.analyzeEmptyText}>
                Sync your trade history, then let Sentinel AI identify weaknesses in your strategy and
                suggest improvements.
              </Text>
              <TouchableOpacity style={styles.analyzeBtn} onPress={handleAnalyze} disabled={analysisLoading || !stats}>
                {analysisLoading
                  ? <ActivityIndicator size="small" color={COLORS.text} />
                  : <>
                      <Ionicons name="sparkles" size={16} color={COLORS.text} />
                      <Text style={styles.analyzeBtnText}>Analyze My Strategy</Text>
                    </>}
              </TouchableOpacity>
            </View>
          )}
        </View>

        {/* ── Best / Worst trade highlight ───────────────────────── */}
        {stats && (
          <>
            <Text style={styles.sectionTitle}>Performance Highlights</Text>
            <View style={styles.highlightRow}>
              <View style={[styles.highlightCard, { borderColor: COLORS.success + '44' }]}>
                <Ionicons name="trending-up" size={20} color={COLORS.success} />
                <Text style={[styles.highlightValue, { color: COLORS.success }]}>
                  +{stats.best_trade_pct.toFixed(2)}%
                </Text>
                <Text style={styles.highlightLabel}>Best Trade</Text>
              </View>
              <View style={[styles.highlightCard, { borderColor: COLORS.error + '44' }]}>
                <Ionicons name="trending-down" size={20} color={COLORS.error} />
                <Text style={[styles.highlightValue, { color: COLORS.error }]}>
                  {stats.worst_trade_pct.toFixed(2)}%
                </Text>
                <Text style={styles.highlightLabel}>Worst Trade</Text>
              </View>
              <View style={[styles.highlightCard, { borderColor: COLORS.primary + '44' }]}>
                <Ionicons name="star-outline" size={20} color={COLORS.primary} />
                <Text style={[styles.highlightValue, { color: COLORS.primary }]}>
                  {stats.most_traded_symbol.replace('/USDT', '')}
                </Text>
                <Text style={styles.highlightLabel}>Most Traded</Text>
              </View>
            </View>
          </>
        )}

        {/* ── Trade list ─────────────────────────────────────────── */}
        {trades.length > 0 ? (
          <>
            <Text style={styles.sectionTitle}>Trade Log ({trades.length})</Text>
            {trades.map((trade) => <TradeRow key={trade.id} trade={trade} />)}
          </>
        ) : (
          <View style={styles.emptyList}>
            <Ionicons name="receipt-outline" size={36} color={COLORS.textMuted} />
            <Text style={styles.emptyText}>Sync your exchange to see trade history</Text>
          </View>
        )}

        <View style={{ height: 32 }} />
      </ScrollView>
    </SafeAreaView>
  );
}

function TradeRow({ trade }: { trade: TradeRecord }) {
  const pnl = trade.pnl;
  const pnlColor = pnl >= 0 ? COLORS.success : COLORS.error;
  const date = new Date(trade.closedAt).toLocaleDateString([], { month: 'short', day: 'numeric' });

  return (
    <View style={rowStyles.row}>
      <View style={[rowStyles.side, { backgroundColor: trade.side === 'BUY' ? COLORS.success + '22' : COLORS.error + '22' }]}>
        <Text style={[rowStyles.sideText, { color: trade.side === 'BUY' ? COLORS.success : COLORS.error }]}>
          {trade.side === 'BUY' ? 'L' : 'S'}
        </Text>
      </View>
      <View style={rowStyles.body}>
        <Text style={rowStyles.symbol}>{trade.symbol}</Text>
        <Text style={rowStyles.date}>{date} · {trade.exchange}</Text>
      </View>
      <View style={{ alignItems: 'flex-end' }}>
        <Text style={[rowStyles.pnl, { color: pnlColor }]}>
          {pnl >= 0 ? '+' : ''}{pnl.toFixed(2)}%
        </Text>
        <Text style={rowStyles.pnlUsd}>
          {trade.pnlUsd >= 0 ? '+' : ''}${trade.pnlUsd.toFixed(2)}
        </Text>
      </View>
    </View>
  );
}

const rowStyles = StyleSheet.create({
  row: {
    flexDirection: 'row', alignItems: 'center',
    backgroundColor: COLORS.backgroundCard, borderRadius: BORDER_RADIUS.md,
    padding: SPACING.sm, marginBottom: 6,
  },
  side: {
    width: 28, height: 28, borderRadius: 14,
    alignItems: 'center', justifyContent: 'center',
  },
  sideText: { fontSize: FONT_SIZES.xs, fontWeight: '700' },
  body: { flex: 1, marginLeft: SPACING.sm },
  symbol: { color: COLORS.text, fontSize: FONT_SIZES.sm, fontWeight: '600' },
  date: { color: COLORS.textMuted, fontSize: FONT_SIZES.xs, marginTop: 2 },
  pnl: { fontSize: FONT_SIZES.sm, fontWeight: '700' },
  pnlUsd: { color: COLORS.textMuted, fontSize: FONT_SIZES.xs, marginTop: 1 },
});

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: COLORS.background },
  scroll: { padding: SPACING.md },

  statsGrid: { flexDirection: 'row', flexWrap: 'wrap', gap: SPACING.sm, marginBottom: SPACING.md },
  statCard: {
    width: '47%', borderRadius: BORDER_RADIUS.lg,
    padding: SPACING.md, alignItems: 'center', ...SHADOWS.sm,
  },
  statValue: { color: COLORS.text, fontSize: FONT_SIZES.xl, fontWeight: '700' },
  statLabel: { color: COLORS.textMuted, fontSize: FONT_SIZES.xs, marginTop: 4 },

  sectionTitle: {
    color: COLORS.text, fontSize: FONT_SIZES.md, fontWeight: '700',
    marginBottom: SPACING.sm, marginTop: SPACING.sm,
  },
  card: {
    backgroundColor: COLORS.backgroundCard, borderRadius: BORDER_RADIUS.lg,
    padding: SPACING.md, marginBottom: SPACING.md, ...SHADOWS.sm,
  },
  lastSynced: { color: COLORS.textMuted, fontSize: FONT_SIZES.xs, marginBottom: SPACING.sm },

  setupHeader: {
    flexDirection: 'row', alignItems: 'center', gap: 8,
    paddingVertical: SPACING.sm,
  },
  setupHeaderText: { color: COLORS.text, fontSize: FONT_SIZES.sm, fontWeight: '600', flex: 1 },
  setupBody: { paddingTop: SPACING.sm },
  fieldLabel: { color: COLORS.textSecondary, fontSize: FONT_SIZES.xs, fontWeight: '600', marginBottom: 6 },
  chips: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  chip: {
    paddingHorizontal: SPACING.sm, paddingVertical: 6,
    borderRadius: BORDER_RADIUS.full, borderWidth: 1, borderColor: COLORS.border,
  },
  chipActive: { backgroundColor: COLORS.primary, borderColor: COLORS.primary },
  chipText: { color: COLORS.textMuted, fontSize: FONT_SIZES.xs, fontWeight: '600' },
  chipTextActive: { color: COLORS.text },
  input: {
    backgroundColor: COLORS.surface, borderRadius: BORDER_RADIUS.md,
    paddingHorizontal: SPACING.md, paddingVertical: SPACING.sm,
    color: COLORS.text, fontSize: FONT_SIZES.sm, borderWidth: 1, borderColor: COLORS.border,
  },
  syncBtn: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 8,
    backgroundColor: COLORS.primary, borderRadius: BORDER_RADIUS.lg,
    paddingVertical: SPACING.sm, marginTop: SPACING.md,
  },
  syncBtnText: { color: COLORS.text, fontSize: FONT_SIZES.sm, fontWeight: '700' },

  analysisHeader: { flexDirection: 'row', alignItems: 'center', gap: 8, marginBottom: SPACING.sm },
  analysisTitle: { color: COLORS.text, fontSize: FONT_SIZES.md, fontWeight: '700' },
  analysisText: { color: COLORS.textSecondary, fontSize: FONT_SIZES.sm, lineHeight: 22 },
  reanalyzeBtn: { marginTop: SPACING.md, alignSelf: 'flex-start' },
  reanalyzeBtnText: { color: COLORS.primary, fontSize: FONT_SIZES.sm, fontWeight: '600' },

  analyzeEmpty: { alignItems: 'center', paddingVertical: SPACING.lg },
  analyzeEmptyTitle: { color: COLORS.text, fontSize: FONT_SIZES.md, fontWeight: '700', marginTop: SPACING.sm },
  analyzeEmptyText: {
    color: COLORS.textMuted, fontSize: FONT_SIZES.sm, textAlign: 'center',
    marginTop: SPACING.sm, lineHeight: 20, paddingHorizontal: SPACING.md,
  },
  analyzeBtn: {
    flexDirection: 'row', alignItems: 'center', gap: 8,
    backgroundColor: COLORS.primary, borderRadius: BORDER_RADIUS.lg,
    paddingHorizontal: SPACING.lg, paddingVertical: SPACING.sm, marginTop: SPACING.lg,
  },
  analyzeBtnText: { color: COLORS.text, fontSize: FONT_SIZES.sm, fontWeight: '700' },

  highlightRow: { flexDirection: 'row', gap: SPACING.sm, marginBottom: SPACING.md },
  highlightCard: {
    flex: 1, backgroundColor: COLORS.backgroundCard, borderRadius: BORDER_RADIUS.lg,
    padding: SPACING.md, alignItems: 'center', borderWidth: 1, ...SHADOWS.sm,
  },
  highlightValue: { fontSize: FONT_SIZES.md, fontWeight: '700', marginTop: 4 },
  highlightLabel: { color: COLORS.textMuted, fontSize: FONT_SIZES.xs, marginTop: 2 },

  emptyList: { alignItems: 'center', paddingVertical: SPACING.xl },
  emptyText: { color: COLORS.textMuted, fontSize: FONT_SIZES.sm, marginTop: SPACING.sm },
});
