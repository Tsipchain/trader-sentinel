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
const MARKET_TYPE_OPTIONS = [
  { key: 'auto' as const, label: 'AUTO' },
  { key: 'futures' as const, label: 'FUTURES' },
  { key: 'spot' as const, label: 'SPOT' },
];

type OpenPosition = {
  symbol: string;
  side: string;
  contracts: number;
  entryPrice: number;
  markPrice: number;
  unrealizedPnl: number;
  pnlPct: number;
  leverage: number;
  marginMode: string;
  liquidationPrice: number;
  notional: number;
};

type TraderProfile = {
  dominant_traits: Array<{ trait: string; frequency: number }>;
  avg_leverage: number;
  max_leverage_seen: number;
  learning_snapshots: number;
  model_trained: boolean;
  model_accuracy: number | null;
  trades_trained_on: number;
} | null;

/** Leverage risk classification */
function leverageRisk(lev: number): { level: string; color: string; icon: string } {
  if (lev >= 200) return { level: 'EXTREME', color: '#FF4444', icon: 'flame' };
  if (lev >= 100) return { level: 'HIGH', color: '#FF8800', icon: 'warning' };
  if (lev >= 50) return { level: 'ELEVATED', color: '#FFD700', icon: 'alert-circle' };
  if (lev >= 20) return { level: 'MODERATE', color: '#44AAFF', icon: 'shield-checkmark' };
  return { level: 'LOW', color: COLORS.success, icon: 'shield' };
}

/** Smart price formatting — handles micro-cap coins like PEPE ($0.000012) */
function formatPrice(price: number): string {
  if (price <= 0) return '$0';
  if (price >= 100) return `$${price.toFixed(2)}`;
  if (price >= 1) return `$${price.toFixed(4)}`;
  if (price >= 0.01) return `$${price.toFixed(5)}`;
  // Very small prices (PEPE, SHIB, etc.) — show significant digits
  return `$${price.toPrecision(4)}`;
}

/** Smart PnL USD formatting — handles tiny and large values */
function formatPnlUsd(pnl: number, entryPrice: number, markPrice: number, contracts: number, leverage: number): string {
  // If the API returns a meaningful unrealizedPnl, use it
  if (Math.abs(pnl) >= 0.005) {
    if (Math.abs(pnl) >= 1000) return `${pnl >= 0 ? '+' : ''}$${pnl.toFixed(2)}`;
    if (Math.abs(pnl) >= 1) return `${pnl >= 0 ? '+' : ''}$${pnl.toFixed(4)}`;
    return `${pnl >= 0 ? '+' : ''}$${pnl.toPrecision(3)}`;
  }
  // Fallback: calculate from price diff * contracts * leverage
  // This handles micro-cap coins where unrealizedPnl might round to 0
  if (entryPrice > 0 && markPrice > 0 && contracts > 0) {
    const priceDiff = markPrice - entryPrice;
    const calculated = priceDiff * contracts * leverage;
    if (Math.abs(calculated) >= 0.005) {
      if (Math.abs(calculated) >= 1) return `${calculated >= 0 ? '+' : ''}$${calculated.toFixed(4)}`;
      return `${calculated >= 0 ? '+' : ''}$${calculated.toPrecision(3)}`;
    }
    // Still too small — compute from PnL % if available
  }
  // Last resort: show the raw value with enough precision
  if (pnl === 0) return '$0.00';
  return `${pnl >= 0 ? '+' : ''}$${pnl.toPrecision(2)}`;
}

/** Calculate distance to liquidation as percentage */
function liqDistancePct(pos: OpenPosition): number {
  if (!pos.liquidationPrice || pos.liquidationPrice <= 0 || !pos.markPrice) return 100;
  const isLong = pos.side === 'long';
  return isLong
    ? ((pos.markPrice - pos.liquidationPrice) / pos.markPrice) * 100
    : ((pos.liquidationPrice - pos.markPrice) / pos.markPrice) * 100;
}

/** Generate Sentinel observation for a position */
function sentinelNote(pos: OpenPosition): string | null {
  const lev = pos.leverage || 1;
  const pnl = pos.pnlPct || 0;
  const liqDist = liqDistancePct(pos);

  if (liqDist < 2) return `CRITICAL: ${liqDist.toFixed(1)}% from liquidation! Add margin or close immediately.`;
  if (liqDist < 5) return `WARNING: Liquidation ${liqDist.toFixed(1)}% away. Tighten risk management.`;
  if (lev >= 200 && pnl > 50) return `Extreme leverage with strong profit. Lock in gains — partial close recommended.`;
  if (lev >= 200 && pnl < -10) return `Extreme leverage underwater. Consider reducing to protect capital.`;
  if (lev >= 100 && pnl > 100) return `+${pnl.toFixed(0)}% at ${lev}x — trail stop aggressively. Don't give it back.`;
  if (lev >= 100 && pnl > 30) return `Solid move at ${lev}x. Consider moving stop to breakeven.`;
  if (pnl > 200) return `Massive gain — take partial profit to secure the win.`;
  return null;
}

export default function HistoryScreen() {
  const { user, tradeHistory, setTradeHistory, autoTrader, setAutoTrader, subscription } = useStore();

  const [syncLoading, setSyncLoading] = useState(false);
  const [analysisLoading, setAnalysisLoading] = useState(false);
  const hasShownBrainMisconfigAlert = useRef(false);

  const [showSetup, setShowSetup] = useState(false);
  const [marketType, setMarketType] = useState<'auto' | 'futures' | 'spot'>('auto');
  const [openPositions, setOpenPositions] = useState<OpenPosition[]>([]);
  const [totalUnrealizedPnl, setTotalUnrealizedPnl] = useState(0);
  const [positionsLastChecked, setPositionsLastChecked] = useState<string | null>(null);
  const positionPollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const [traderProfile, setTraderProfile] = useState<TraderProfile>(null);
  const [sentinelLearning, setSentinelLearning] = useState(false);
  const cfg = autoTrader.config;
  const exchange = cfg.exchange;
  const apiKey = cfg.apiKey;
  const apiSecret = cfg.apiSecret;
  const updateSharedCfg = (patch: Partial<typeof cfg>) => setAutoTrader({ config: { ...cfg, ...patch } });

  // Position monitoring — poll every 15 minutes
  const fetchPositions = useCallback(async () => {
    const apiKeyTrimmed = apiKey.trim();
    const apiSecretTrimmed = apiSecret.trim();
    if (!user?.id || !apiKeyTrimmed || !apiSecretTrimmed || !exchange) return;
    setPositionsLoading(true);
    try {
      const result = await brainAPI.getOpenPositions({
        user_id: user.id,
        exchange,
        api_key: apiKeyTrimmed,
        api_secret: apiSecretTrimmed,
      });
      if (result.ok) {
        setOpenPositions(result.positions ?? []);
        setTotalUnrealizedPnl(result.total_unrealized_pnl ?? 0);
        setPositionsLastChecked(new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }));

        // Trigger Sentinel learning from positions (best-effort)
        if (result.positions?.length) {
          setSentinelLearning(true);
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
            total_unrealized_pnl: result.total_unrealized_pnl ?? 0,
          }).then(() => setSentinelLearning(false)).catch(() => setSentinelLearning(false));

          // Fetch trader profile
          brainAPI.getTraderProfile(user.id).then((profileResult) => {
            if (profileResult.ok && profileResult.profile) {
              setTraderProfile(profileResult.profile);
            }
          }).catch(() => {});
        }
      }
    } catch {
      // Silent fail for background polling
    } finally {
      setPositionsLoading(false);
    }
  }, [user?.id, exchange, apiKey, apiSecret]);

  useEffect(() => {
    // Auto-fetch positions on mount if credentials exist
    if (apiKey && apiSecret && exchange) {
      fetchPositions();
    }

    // Set up 15-minute polling interval
    positionPollRef.current = setInterval(() => {
      if (apiKey && apiSecret && exchange) {
        fetchPositions();
      }
    }, 15 * 60 * 1000); // 15 minutes

    return () => {
      if (positionPollRef.current) {
        clearInterval(positionPollRef.current);
      }
    };
  }, [fetchPositions, apiKey, apiSecret, exchange]);

  useEffect(() => {
    // Keep History screen credentials in sync with shared AutoTrader config.
    setExchange(autoTrader.config.exchange);
    setApiKey(autoTrader.config.apiKey);
    setApiSecret(autoTrader.config.apiSecret);
  }, [autoTrader.config.exchange, autoTrader.config.apiKey, autoTrader.config.apiSecret]);

  useEffect(() => {
    // Keep History screen credentials in sync with shared AutoTrader config.
    setExchange(autoTrader.config.exchange);
    setApiKey(autoTrader.config.apiKey);
    setApiSecret(autoTrader.config.apiSecret);
  }, [autoTrader.config.exchange, autoTrader.config.apiKey, autoTrader.config.apiSecret]);

  useEffect(() => {
    // Keep History screen credentials in sync with shared AutoTrader config.
    setExchange(autoTrader.config.exchange);
    setApiKey(autoTrader.config.apiKey);
    setApiSecret(autoTrader.config.apiSecret);
  }, [autoTrader.config.exchange, autoTrader.config.apiKey, autoTrader.config.apiSecret]);

  useEffect(() => {
    // Keep History screen credentials in sync with shared AutoTrader config.
    setExchange(autoTrader.config.exchange);
    setApiKey(autoTrader.config.apiKey);
    setApiSecret(autoTrader.config.apiSecret);
  }, [autoTrader.config.exchange, autoTrader.config.apiKey, autoTrader.config.apiSecret]);

  useEffect(() => {
    // Keep History screen credentials in sync with shared AutoTrader config.
    setExchange(autoTrader.config.exchange);
    setApiKey(autoTrader.config.apiKey);
    setApiSecret(autoTrader.config.apiSecret);
  }, [autoTrader.config.exchange, autoTrader.config.apiKey, autoTrader.config.apiSecret]);

  useEffect(() => {
    // Keep History screen credentials in sync with shared AutoTrader config.
    setExchange(autoTrader.config.exchange);
    setApiKey(autoTrader.config.apiKey);
    setApiSecret(autoTrader.config.apiSecret);
  }, [autoTrader.config.exchange, autoTrader.config.apiKey, autoTrader.config.apiSecret]);

  useEffect(() => {
    // Keep History screen credentials in sync with shared AutoTrader config.
    setExchange(autoTrader.config.exchange);
    setApiKey(autoTrader.config.apiKey);
    setApiSecret(autoTrader.config.apiSecret);
  }, [autoTrader.config.exchange, autoTrader.config.apiKey, autoTrader.config.apiSecret]);

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
    const apiKeyTrimmed = apiKey.trim();
    const apiSecretTrimmed = apiSecret.trim();

    if (!apiKeyTrimmed || !apiSecretTrimmed) {
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

      // Sync & train model (auto = try futures first, then spot)
      const syncResult = await brainAPI.syncTrades({
        user_id: user.id,
        exchange,
        api_key: apiKeyTrimmed,
        api_secret: apiSecretTrimmed,
        days: 90,
        market_type: marketType,
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

      // Also refresh open positions after sync
      await fetchPositions();

      Alert.alert(
        'Sync Complete',
        syncResult.trade_count
          ? `${syncResult.trade_count} trades synced (${syncResult.market_type ?? marketType}). Model accuracy: ${((syncResult.model_accuracy ?? 0) * 100).toFixed(1)}%`
          : (syncResult.message || 'Trade history updated.'),
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

          {/* Market type selector */}
          <Text style={[styles.fieldLabel, { marginTop: SPACING.sm }]}>Market Type</Text>
          <View style={styles.chips}>
            {MARKET_TYPE_OPTIONS.map((mt) => (
              <TouchableOpacity
                key={mt.key}
                style={[styles.chip, marketType === mt.key && styles.chipActive]}
                onPress={() => setMarketType(mt.key)}
              >
                <Text style={[styles.chipText, marketType === mt.key && styles.chipTextActive]}>
                  {mt.label}
                </Text>
              </TouchableOpacity>
            ))}
          </View>

          <TouchableOpacity style={styles.syncBtn} onPress={handleSync} disabled={syncLoading}>
            {syncLoading
              ? <ActivityIndicator size="small" color={COLORS.text} />
              : <>
                  <Ionicons name="sync-outline" size={18} color={COLORS.text} />
                  <Text style={styles.syncBtnText}>Sync Last 90 Days</Text>
                </>}
          </TouchableOpacity>
        </View>

        {/* ── Open Positions (live) ───────────────────────────────── */}
        {(openPositions.length > 0 || positionsLoading) && (
          <>
            <View style={styles.sectionRow}>
              <Text style={styles.sectionTitle}>Open Positions</Text>
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
                {sentinelLearning && (
                  <View style={{ flexDirection: 'row', alignItems: 'center', gap: 4 }}>
                    <ActivityIndicator size="small" color={COLORS.thronosGold} />
                    <Text style={{ color: COLORS.thronosGold, fontSize: FONT_SIZES.xs }}>Learning</Text>
                  </View>
                )}
                <TouchableOpacity onPress={fetchPositions} disabled={positionsLoading}>
                  {positionsLoading
                    ? <ActivityIndicator size="small" color={COLORS.primary} />
                    : <Ionicons name="refresh-outline" size={18} color={COLORS.primary} />}
                </TouchableOpacity>
              </View>
            </View>
            {positionsLastChecked && (
              <Text style={styles.lastSynced}>
                Last checked {positionsLastChecked} · Auto-refresh every 15min
              </Text>
            )}
            {totalUnrealizedPnl !== 0 && (
              <View style={[styles.card, { marginBottom: SPACING.sm }]}>
                <Text style={styles.fieldLabel}>Total Unrealized P&L</Text>
                <Text style={[styles.statValue, {
                  color: totalUnrealizedPnl >= 0 ? COLORS.success : COLORS.error,
                  fontSize: FONT_SIZES.lg,
                }]}>
                  {totalUnrealizedPnl >= 0 ? '+' : ''}${totalUnrealizedPnl.toFixed(4)}
                </Text>
              </View>
            )}
            {openPositions.map((pos, i) => (
              <PositionRow key={`${pos.symbol}-${pos.side}-${i}`} position={pos} />
            ))}

            {/* ── Sentinel Observations ──────────────────────────── */}
            {openPositions.some((p) => sentinelNote(p) !== null) && (
              <View style={[styles.card, { marginTop: SPACING.sm, borderLeftWidth: 3, borderLeftColor: COLORS.thronosGold }]}>
                <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6, marginBottom: SPACING.sm }}>
                  <Ionicons name="eye" size={16} color={COLORS.thronosGold} />
                  <Text style={[styles.analysisTitle, { color: COLORS.thronosGold }]}>Sentinel Observations</Text>
                </View>
                {openPositions.map((pos, i) => {
                  const note = sentinelNote(pos);
                  if (!note) return null;
                  const sym = pos.symbol.replace(':USDT', '').replace('/USDT', '');
                  const risk = leverageRisk(pos.leverage);
                  return (
                    <View key={`obs-${pos.symbol}-${pos.side}-${i}`} style={{ flexDirection: 'row', alignItems: 'flex-start', marginBottom: 8, gap: 6 }}>
                      <Ionicons name={risk.icon as any} size={14} color={risk.color} style={{ marginTop: 2 }} />
                      <Text style={{ color: COLORS.textSecondary, fontSize: FONT_SIZES.xs, flex: 1, lineHeight: 18 }}>
                        <Text style={{ fontWeight: '700', color: risk.color }}>{sym} </Text>
                        {note}
                      </Text>
                    </View>
                  );
                })}
              </View>
            )}
          </>
        )}

        {/* ── Trader Profile (learned by Sentinel) ────────────────── */}
        {traderProfile && (
          <>
            <Text style={styles.sectionTitle}>Trader Profile</Text>
            <View style={[styles.card, { borderLeftWidth: 3, borderLeftColor: COLORS.primary }]}>
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6, marginBottom: SPACING.sm }}>
                <Ionicons name="person-circle" size={18} color={COLORS.primary} />
                <Text style={styles.analysisTitle}>Sentinel AI Profile</Text>
              </View>
              <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 6, marginBottom: SPACING.sm }}>
                {traderProfile.dominant_traits.map((t) => (
                  <View key={t.trait} style={{
                    backgroundColor: COLORS.primary + '22', paddingHorizontal: 8, paddingVertical: 3,
                    borderRadius: BORDER_RADIUS.full,
                  }}>
                    <Text style={{ color: COLORS.primary, fontSize: FONT_SIZES.xs, fontWeight: '600' }}>
                      {t.trait.replace(/_/g, ' ')}
                    </Text>
                  </View>
                ))}
              </View>
              <View style={{ flexDirection: 'row', justifyContent: 'space-between' }}>
                <View style={{ alignItems: 'center' }}>
                  <Text style={{ color: COLORS.text, fontWeight: '700', fontSize: FONT_SIZES.md }}>
                    {traderProfile.avg_leverage.toFixed(0)}x
                  </Text>
                  <Text style={{ color: COLORS.textMuted, fontSize: FONT_SIZES.xs }}>Avg Leverage</Text>
                </View>
                <View style={{ alignItems: 'center' }}>
                  <Text style={{ color: COLORS.text, fontWeight: '700', fontSize: FONT_SIZES.md }}>
                    {traderProfile.max_leverage_seen}x
                  </Text>
                  <Text style={{ color: COLORS.textMuted, fontSize: FONT_SIZES.xs }}>Max Seen</Text>
                </View>
                <View style={{ alignItems: 'center' }}>
                  <Text style={{ color: COLORS.text, fontWeight: '700', fontSize: FONT_SIZES.md }}>
                    {traderProfile.learning_snapshots}
                  </Text>
                  <Text style={{ color: COLORS.textMuted, fontSize: FONT_SIZES.xs }}>Snapshots</Text>
                </View>
                <View style={{ alignItems: 'center' }}>
                  <Text style={{ color: traderProfile.model_trained ? COLORS.success : COLORS.textMuted, fontWeight: '700', fontSize: FONT_SIZES.md }}>
                    {traderProfile.model_trained ? (traderProfile.model_accuracy ? `${(traderProfile.model_accuracy * 100).toFixed(0)}%` : 'Active') : 'Training'}
                  </Text>
                  <Text style={{ color: COLORS.textMuted, fontSize: FONT_SIZES.xs }}>Brain</Text>
                </View>
              </View>
            </View>
          </>
        )}

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

function PositionRow({ position }: { position: OpenPosition }) {
  const pnlColor = position.unrealizedPnl >= 0 ? COLORS.success : COLORS.error;
  const isLong = position.side === 'long';
  const displaySymbol = position.symbol.replace(':USDT', '').replace('/USDT', '');
  const risk = leverageRisk(position.leverage);
  const liqDist = liqDistancePct(position);

  return (
    <View style={rowStyles.row}>
      <View style={[rowStyles.side, { backgroundColor: isLong ? COLORS.success + '22' : COLORS.error + '22' }]}>
        <Text style={[rowStyles.sideText, { color: isLong ? COLORS.success : COLORS.error }]}>
          {isLong ? 'L' : 'S'}
        </Text>
      </View>
      <View style={rowStyles.body}>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
          <Text style={rowStyles.symbol}>{displaySymbol}</Text>
          <View style={[rowStyles.leverageBadge, { backgroundColor: risk.color + '22', borderColor: risk.color + '44' }]}>
            <Ionicons name={risk.icon as any} size={9} color={risk.color} />
            <Text style={[rowStyles.leverageBadgeText, { color: risk.color }]}>{risk.level}</Text>
          </View>
        </View>
        <Text style={rowStyles.date}>
          {position.leverage}x {position.marginMode} · Entry {formatPrice(position.entryPrice)}
        </Text>
        <Text style={[rowStyles.date, { color: COLORS.textMuted }]}>
          Mark {formatPrice(position.markPrice)} · Liq {position.liquidationPrice > 0 ? `${formatPrice(position.liquidationPrice)} (${liqDist.toFixed(1)}%)` : '–'}
        </Text>
      </View>
      <View style={{ alignItems: 'flex-end' }}>
        <Text style={[rowStyles.pnl, { color: pnlColor }]}>
          {position.pnlPct >= 0 ? '+' : ''}{position.pnlPct.toFixed(2)}%
        </Text>
        <Text style={[rowStyles.pnlUsd, { color: pnlColor }]}>
          {formatPnlUsd(position.unrealizedPnl, position.entryPrice, position.markPrice, position.contracts, position.leverage)}
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
  leverageBadge: {
    flexDirection: 'row', alignItems: 'center', gap: 3,
    paddingHorizontal: 5, paddingVertical: 1,
    borderRadius: BORDER_RADIUS.sm, borderWidth: 1,
  },
  leverageBadgeText: { fontSize: 8, fontWeight: '800', letterSpacing: 0.5 },
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
  sectionRow: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    marginTop: SPACING.sm, marginBottom: SPACING.sm,
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
