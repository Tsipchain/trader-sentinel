import React, { useState, useCallback, useEffect } from 'react';
import {
  View, Text, ScrollView, StyleSheet, Switch, TouchableOpacity,
  Alert, TextInput, ActivityIndicator,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import { LinearGradient } from 'expo-linear-gradient';
import { COLORS, SPACING, FONT_SIZES, BORDER_RADIUS, SHADOWS } from '../constants/theme';
import { useStore } from '../store/useStore';
import { brainAPI } from '../services/api';
import type { ActiveTrade } from '../services/api';

const SYMBOL_OPTIONS = ['BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'BNB/USDT', 'XRP/USDT'];
const EXCHANGE_OPTIONS = ['binance', 'bybit', 'okx', 'mexc'];

export default function AutoTraderScreen() {
  const { user, autoTrader, setAutoTrader } = useStore();
  const [toggling, setToggling] = useState(false);
  const [loadingStatus, setLoadingStatus] = useState(false);
  const [closingId, setClosingId] = useState<string | null>(null);

  const isEnabled = autoTrader.enabled;
  const cfg = autoTrader.config;

  const refreshStatus = useCallback(async () => {
    if (!user?.id) return;
    setLoadingStatus(true);
    try {
      const status = await brainAPI.getAutoTraderStatus(user.id);
      if (status.ok) {
        setAutoTrader({ enabled: status.enabled, activeTrades: status.active_trades ?? [] });
      }
    } catch {
      // Backend endpoint not yet live — UI still functional
    } finally {
      setLoadingStatus(false);
    }
  }, [user?.id]);

  useEffect(() => { refreshStatus(); }, [refreshStatus]);

  const updateCfg = (patch: Partial<typeof cfg>) =>
    setAutoTrader({ config: { ...cfg, ...patch } });

  const toggleSymbol = (sym: string) => {
    if (isEnabled) return;
    const next = cfg.symbols.includes(sym)
      ? cfg.symbols.filter((s) => s !== sym)
      : [...cfg.symbols, sym];
    if (next.length > 0) updateCfg({ symbols: next });
  };

  const handleToggle = () => {
    if (!user?.id) {
      Alert.alert('Not Connected', 'Connect your wallet first.');
      return;
    }
    if (!isEnabled && (!cfg.apiKey || !cfg.apiSecret)) {
      Alert.alert('Setup Required', 'Enter your exchange API key and secret before enabling AutoTrader.');
      return;
    }

    const title = isEnabled ? 'Disable AutoTrader?' : 'Enable AutoTrader?';
    const message = isEnabled
      ? 'The AI will stop trading. Open positions remain unchanged on your exchange.'
      : `Sentinel AI will monitor markets and execute trades on ${cfg.exchange.toUpperCase()} using your parameters. Continue?`;

    Alert.alert(title, message, [
      { text: 'Cancel', style: 'cancel' },
      {
        text: isEnabled ? 'Disable' : 'Enable',
        style: isEnabled ? 'destructive' : 'default',
        onPress: async () => {
          setToggling(true);
          try {
            if (isEnabled) {
              await brainAPI.disableAutoTrader(user.id).catch(() => {});
              setAutoTrader({ enabled: false, activeTrades: [] });
            } else {
              await brainAPI.enableAutoTrader({
                user_id: user.id,
                exchange: cfg.exchange,
                api_key: cfg.apiKey,
                api_secret: cfg.apiSecret,
                symbols: cfg.symbols,
                stop_loss_pct: cfg.stopLossPct,
                take_profit_pct: cfg.takeProfitPct,
                max_position_pct: cfg.maxPositionPct,
                max_open_trades: cfg.maxOpenTrades,
              }).catch(() => {});
              setAutoTrader({ enabled: true });
            }
          } finally {
            setToggling(false);
          }
        },
      },
    ]);
  };

  const handleCloseTrade = (trade: ActiveTrade) => {
    Alert.alert(
      'Close Position?',
      `Market-close ${trade.side} ${trade.symbol} (${trade.pnl >= 0 ? '+' : ''}${trade.pnl.toFixed(2)}%)`,
      [
        { text: 'Cancel', style: 'cancel' },
        {
          text: 'Close',
          style: 'destructive',
          onPress: async () => {
            if (!user?.id) return;
            setClosingId(trade.id);
            try {
              await brainAPI.closeTrade(user.id, trade.id).catch(() => {});
              setAutoTrader({
                activeTrades: autoTrader.activeTrades.filter((t) => t.id !== trade.id),
              });
            } finally {
              setClosingId(null);
            }
          },
        },
      ],
    );
  };

  return (
    <SafeAreaView style={styles.safe} edges={['bottom']}>
      <ScrollView contentContainerStyle={styles.scroll} showsVerticalScrollIndicator={false}>

        {/* ── Status banner ─────────────────────────────────────── */}
        <View style={styles.statusRow}>
          <View style={[styles.statusDot, { backgroundColor: isEnabled ? COLORS.success : COLORS.textMuted }]} />
          <Text style={[styles.statusLabel, { color: isEnabled ? COLORS.success : COLORS.textMuted }]}>
            {loadingStatus ? 'CHECKING…' : isEnabled ? 'AUTOTRADER ACTIVE' : 'AUTOTRADER IDLE'}
          </Text>
          <TouchableOpacity onPress={refreshStatus} style={styles.refreshBtn}>
            <Ionicons name="refresh" size={16} color={COLORS.textMuted} />
          </TouchableOpacity>
        </View>

        {/* ── Sleep-mode toggle card ─────────────────────────────── */}
        <LinearGradient
          colors={isEnabled
            ? ['#065F46', '#047857'] as [string, string]
            : COLORS.gradientCard as [string, string]}
          style={styles.mainCard}
        >
          <View style={styles.toggleRow}>
            <View style={styles.toggleLeft}>
              <Ionicons name="hardware-chip" size={30} color={isEnabled ? COLORS.chartGreen : COLORS.primary} />
              <View style={styles.toggleText}>
                <Text style={styles.toggleTitle}>Sleep Mode</Text>
                <Text style={styles.toggleSub}>
                  {isEnabled ? 'AI is trading on your behalf' : 'Hand over trades to Sentinel AI'}
                </Text>
              </View>
            </View>
            {toggling
              ? <ActivityIndicator color={COLORS.text} />
              : <Switch
                  value={isEnabled}
                  onValueChange={handleToggle}
                  trackColor={{ false: COLORS.surface, true: COLORS.successDark }}
                  thumbColor={isEnabled ? '#fff' : COLORS.textMuted}
                />}
          </View>

          {isEnabled && (
            <View style={styles.activeStats}>
              {[
                { label: 'Open', value: autoTrader.activeTrades.length },
                { label: 'Symbols', value: cfg.symbols.length },
                { label: 'Max Trades', value: cfg.maxOpenTrades },
              ].map(({ label, value }) => (
                <View key={label} style={styles.activeStat}>
                  <Text style={styles.activeStatValue}>{value}</Text>
                  <Text style={styles.activeStatLabel}>{label}</Text>
                </View>
              ))}
            </View>
          )}
        </LinearGradient>

        {/* ── Exchange API ───────────────────────────────────────── */}
        <Text style={styles.sectionTitle}>Exchange Connection</Text>
        <View style={styles.card}>
          <Text style={styles.fieldLabel}>Exchange</Text>
          <View style={styles.chips}>
            {EXCHANGE_OPTIONS.map((ex) => (
              <TouchableOpacity
                key={ex}
                style={[styles.chip, cfg.exchange === ex && styles.chipActive]}
                onPress={() => !isEnabled && updateCfg({ exchange: ex })}
                disabled={isEnabled}
              >
                <Text style={[styles.chipText, cfg.exchange === ex && styles.chipTextActive]}>
                  {ex.toUpperCase()}
                </Text>
              </TouchableOpacity>
            ))}
          </View>

          <Text style={[styles.fieldLabel, { marginTop: SPACING.md }]}>API Key</Text>
          <TextInput
            style={styles.input}
            value={cfg.apiKey}
            onChangeText={(v) => updateCfg({ apiKey: v })}
            placeholder="Read-only API key"
            placeholderTextColor={COLORS.textMuted}
            secureTextEntry
            editable={!isEnabled}
          />

          <Text style={[styles.fieldLabel, { marginTop: SPACING.sm }]}>API Secret</Text>
          <TextInput
            style={styles.input}
            value={cfg.apiSecret}
            onChangeText={(v) => updateCfg({ apiSecret: v })}
            placeholder="API secret"
            placeholderTextColor={COLORS.textMuted}
            secureTextEntry
            editable={!isEnabled}
          />
          <View style={styles.securityNote}>
            <Ionicons name="lock-closed" size={12} color={COLORS.success} />
            <Text style={styles.securityText}>
              Use read-only keys without withdrawal permissions.
            </Text>
          </View>
        </View>

        {/* ── Strategy parameters ────────────────────────────────── */}
        <Text style={styles.sectionTitle}>Strategy Parameters</Text>
        <View style={styles.card}>
          <Text style={styles.fieldLabel}>Symbols to Trade</Text>
          <View style={styles.chips}>
            {SYMBOL_OPTIONS.map((sym) => (
              <TouchableOpacity
                key={sym}
                style={[styles.chip, cfg.symbols.includes(sym) && styles.chipActive]}
                onPress={() => toggleSymbol(sym)}
                disabled={isEnabled}
              >
                <Text style={[styles.chipText, cfg.symbols.includes(sym) && styles.chipTextActive]}>
                  {sym.replace('/USDT', '')}
                </Text>
              </TouchableOpacity>
            ))}
          </View>

          <View style={styles.paramGrid}>
            {([
              { label: 'Stop Loss %', key: 'stopLossPct' },
              { label: 'Take Profit %', key: 'takeProfitPct' },
              { label: 'Max Position %', key: 'maxPositionPct' },
              { label: 'Max Open Trades', key: 'maxOpenTrades' },
            ] as const).map(({ label, key }) => (
              <View key={key} style={styles.paramField}>
                <Text style={styles.fieldLabel}>{label}</Text>
                <TextInput
                  style={styles.paramInput}
                  value={String(cfg[key])}
                  onChangeText={(v) => {
                    const n = key === 'maxOpenTrades' ? parseInt(v, 10) : parseFloat(v);
                    if (!isNaN(n)) updateCfg({ [key]: n });
                  }}
                  keyboardType="numeric"
                  editable={!isEnabled}
                />
              </View>
            ))}
          </View>
        </View>

        {/* ── Active trades ──────────────────────────────────────── */}
        {autoTrader.activeTrades.length > 0 && (
          <>
            <Text style={styles.sectionTitle}>Active Trades</Text>
            {autoTrader.activeTrades.map((trade) => {
              const pnlColor = trade.pnl >= 0 ? COLORS.success : COLORS.error;
              return (
                <View key={trade.id} style={styles.tradeCard}>
                  <View style={styles.tradeRow}>
                    <View style={[styles.sideBadge, {
                      backgroundColor: trade.side === 'BUY' ? COLORS.success + '22' : COLORS.error + '22',
                    }]}>
                      <Text style={[styles.sideText, { color: pnlColor }]}>{trade.side}</Text>
                    </View>
                    <View style={styles.tradeBody}>
                      <Text style={styles.tradeSymbol}>{trade.symbol}</Text>
                      <Text style={styles.tradeDetail}>Entry ${trade.entryPrice.toFixed(2)}</Text>
                    </View>
                    <View style={{ alignItems: 'flex-end' }}>
                      <Text style={[styles.tradePnl, { color: pnlColor }]}>
                        {trade.pnl >= 0 ? '+' : ''}{trade.pnl.toFixed(2)}%
                      </Text>
                      <Text style={styles.tradeDetail}>${trade.currentPrice.toFixed(2)}</Text>
                    </View>
                  </View>
                  <TouchableOpacity
                    style={styles.closeBtn}
                    onPress={() => handleCloseTrade(trade)}
                    disabled={closingId === trade.id}
                  >
                    {closingId === trade.id
                      ? <ActivityIndicator size="small" color={COLORS.error} />
                      : <Text style={styles.closeBtnText}>Close Position</Text>}
                  </TouchableOpacity>
                </View>
              );
            })}
          </>
        )}

        {/* ── Info ──────────────────────────────────────────────── */}
        <View style={styles.infoBox}>
          <Ionicons name="information-circle-outline" size={16} color={COLORS.info} />
          <Text style={styles.infoText}>
            AutoTrader uses Sentinel AI's market analysis and executes via your exchange API.
            You can disable it or close individual positions at any time.
          </Text>
        </View>

        <View style={{ height: 32 }} />
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: COLORS.background },
  scroll: { padding: SPACING.md },

  statusRow: {
    flexDirection: 'row', alignItems: 'center', marginBottom: SPACING.md,
  },
  statusDot: { width: 8, height: 8, borderRadius: 4, marginRight: 6 },
  statusLabel: { fontSize: FONT_SIZES.sm, fontWeight: '600', flex: 1, letterSpacing: 1 },
  refreshBtn: { padding: 4 },

  mainCard: {
    borderRadius: BORDER_RADIUS.xl, padding: SPACING.lg, marginBottom: SPACING.lg,
    ...SHADOWS.md,
  },
  toggleRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  toggleLeft: { flexDirection: 'row', alignItems: 'center', flex: 1 },
  toggleText: { marginLeft: SPACING.md },
  toggleTitle: { color: COLORS.text, fontSize: FONT_SIZES.lg, fontWeight: '700' },
  toggleSub: { color: COLORS.textSecondary, fontSize: FONT_SIZES.sm, marginTop: 2 },
  activeStats: {
    flexDirection: 'row', justifyContent: 'space-around',
    marginTop: SPACING.lg, paddingTop: SPACING.md,
    borderTopWidth: 1, borderTopColor: 'rgba(255,255,255,0.15)',
  },
  activeStat: { alignItems: 'center' },
  activeStatValue: { color: COLORS.text, fontSize: FONT_SIZES.xl, fontWeight: '700' },
  activeStatLabel: { color: COLORS.textSecondary, fontSize: FONT_SIZES.xs, marginTop: 2 },

  sectionTitle: {
    color: COLORS.text, fontSize: FONT_SIZES.md, fontWeight: '700',
    marginBottom: SPACING.sm, marginTop: SPACING.sm,
  },
  card: {
    backgroundColor: COLORS.backgroundCard, borderRadius: BORDER_RADIUS.lg,
    padding: SPACING.md, marginBottom: SPACING.md, ...SHADOWS.sm,
  },
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
  securityNote: {
    flexDirection: 'row', alignItems: 'center', gap: 4, marginTop: SPACING.sm,
  },
  securityText: { color: COLORS.success, fontSize: FONT_SIZES.xs },

  paramGrid: { flexDirection: 'row', flexWrap: 'wrap', gap: SPACING.sm, marginTop: SPACING.md },
  paramField: { width: '47%' },
  paramInput: {
    backgroundColor: COLORS.surface, borderRadius: BORDER_RADIUS.md,
    paddingHorizontal: SPACING.md, paddingVertical: SPACING.sm,
    color: COLORS.text, fontSize: FONT_SIZES.md, fontWeight: '600',
    borderWidth: 1, borderColor: COLORS.border, textAlign: 'center',
  },

  tradeCard: {
    backgroundColor: COLORS.backgroundCard, borderRadius: BORDER_RADIUS.lg,
    padding: SPACING.md, marginBottom: SPACING.sm, ...SHADOWS.sm,
  },
  tradeRow: { flexDirection: 'row', alignItems: 'center' },
  sideBadge: { padding: 6, borderRadius: BORDER_RADIUS.sm },
  sideText: { fontSize: FONT_SIZES.xs, fontWeight: '700' },
  tradeBody: { flex: 1, marginLeft: SPACING.sm },
  tradeSymbol: { color: COLORS.text, fontSize: FONT_SIZES.md, fontWeight: '600' },
  tradeDetail: { color: COLORS.textMuted, fontSize: FONT_SIZES.xs, marginTop: 2 },
  tradePnl: { fontSize: FONT_SIZES.md, fontWeight: '700' },
  closeBtn: {
    marginTop: SPACING.sm, borderWidth: 1, borderColor: COLORS.error,
    borderRadius: BORDER_RADIUS.md, paddingVertical: 8, alignItems: 'center',
  },
  closeBtnText: { color: COLORS.error, fontSize: FONT_SIZES.sm, fontWeight: '600' },

  infoBox: {
    flexDirection: 'row', gap: SPACING.sm, backgroundColor: COLORS.info + '15',
    borderRadius: BORDER_RADIUS.lg, padding: SPACING.md, marginTop: SPACING.sm,
    alignItems: 'flex-start',
  },
  infoText: { color: COLORS.textSecondary, fontSize: FONT_SIZES.xs, flex: 1, lineHeight: 18 },
});
