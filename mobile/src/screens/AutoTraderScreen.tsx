import React, { useState, useCallback, useEffect, useRef } from 'react';
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
import CONFIG from '../config';
import type { ActiveTrade } from '../services/api';

const SYMBOL_OPTIONS = ['BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'BNB/USDT', 'XRP/USDT'];
const EXCHANGE_OPTIONS = ['binance', 'bybit', 'okx', 'mexc'];
const SNAPSHOT_MAX_AGE_MS = 5 * 60 * 1000;

export default function AutoTraderScreen() {
  const { user, autoTrader, setAutoTrader } = useStore();
  const [toggling, setToggling] = useState(false);
  const [loadingStatus, setLoadingStatus] = useState(false);
  const [closingId, setClosingId] = useState<string | null>(null);
  const [syncingPortfolio, setSyncingPortfolio] = useState(false);
  const hasShownBrainMisconfigAlert = useRef(false);

  const isEnabled = autoTrader.enabled;
  const cfg = autoTrader.config;
  const portfolio = autoTrader.portfolio ?? {
    equity: 0,
    balances: [],
    positions: [],
    usedMargin: 0,
    maxLeverageBySymbol: {},
    lastSyncTs: null,
  };
  const exchangeAvailability = autoTrader.exchangeAvailability ?? {};

  const refreshStatus = useCallback(async () => {
    setLoadingStatus(true);
    try {
      const [status, availability] = await Promise.all([
        user?.id ? brainAPI.getAutoTraderStatus(user.id) : Promise.resolve({ ok: false, enabled: false, active_trades: [], log: [] }),
        brainAPI.getExchangeAvailability().catch(() => ({ ok: false, exchanges: {} })),
      ]);
      if (status.ok) {
        setAutoTrader({ enabled: status.enabled, activeTrades: status.active_trades ?? [] });
      }
      if (availability.ok) {
        setAutoTrader({ exchangeAvailability: availability.exchanges });
      }
    } catch {
      // keep UI functional when backend is unavailable
    } finally {
      setLoadingStatus(false);
    }
  }, [user?.id, setAutoTrader]);

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

  const isExchangeEnabled = (exchange: string) => exchangeAvailability[exchange]?.enabled !== false;

  const syncPortfolio = useCallback(async (): Promise<boolean> => {
    if (!cfg.apiKey || !cfg.apiSecret) {
      Alert.alert('Setup Required', 'Enter your exchange API key and secret first.');
      return false;
    }

    setSyncingPortfolio(true);
    try {
      const serviceCheck = await brainAPI.checkServiceType();
      if (!serviceCheck.isBrain) {
        const reason = serviceCheck.reason || 'unknown';
        if (!hasShownBrainMisconfigAlert.current) {
          hasShownBrainMisconfigAlert.current = true;
          console.warn(
            `Brain service-type validation warning (${CONFIG.BRAIN_URL}):`,
            reason,
          );
        }
        // Do not hard-block here: continue and let real API call/fallback decide.
      } else {
        hasShownBrainMisconfigAlert.current = false;
      }
      hasShownBrainMisconfigAlert.current = false;

      const res = await brainAPI.getExchangeSnapshot({
        exchange: cfg.exchange,
        apiKey: cfg.apiKey,
        apiSecret: cfg.apiSecret,
        passphrase: cfg.passphrase || undefined,
      });

      if (res.exchanges) {
        setAutoTrader({ exchangeAvailability: res.exchanges });
      }

      if (!res.ok || !res.snapshot) {
        const msg = res.error || 'Could not sync portfolio.';
        Alert.alert('Sync Failed', msg);
        return false;
      }

      setAutoTrader({
        portfolio: {
          equity: res.snapshot.equity,
          balances: res.snapshot.balances,
          positions: res.snapshot.positions,
          usedMargin: res.snapshot.usedMargin,
          maxLeverageBySymbol: res.snapshot.maxLeverageBySymbol,
          lastSyncTs: Date.now(),
        },
      });
      return true;
    } catch {
      Alert.alert('Sync Failed', 'Unable to connect to the brain service right now.');
      return false;
    } finally {
      setSyncingPortfolio(false);
    }
  }, [cfg.apiKey, cfg.apiSecret, cfg.exchange, cfg.passphrase, setAutoTrader]);

  const ensureFreshSnapshot = useCallback(async (): Promise<boolean> => {
    const isFresh = !!portfolio.lastSyncTs && Date.now() - portfolio.lastSyncTs < SNAPSHOT_MAX_AGE_MS;
    if (isFresh) return true;
    return syncPortfolio();
  }, [portfolio.lastSyncTs, syncPortfolio]);

  const handleToggle = () => {
    if (!user?.id) {
      Alert.alert('Not Connected', 'Connect your wallet first.');
      return;
    }
    if (!isEnabled && (!cfg.apiKey || !cfg.apiSecret)) {
      Alert.alert('Setup Required', 'Enter your exchange API key and secret before enabling AutoTrader.');
      return;
    }
    if (!isEnabled && !isExchangeEnabled(cfg.exchange)) {
      Alert.alert('Execution Unavailable', 'Execution not available from server region. Use OKX/MEXC or Local Executor.');
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
              const ok = await ensureFreshSnapshot();
              if (!ok) return;

              if ((portfolio.equity || 0) < 50) {
                Alert.alert(
                  'Minimum Balance Required',
                  'AutoTrader requires at least 50 USDT equity in your trading account before activation.',
                );
                return;
              }

              await brainAPI.enableAutoTrader({
                user_id: user.id,
                exchange: cfg.exchange,
                api_key: cfg.apiKey,
                api_secret: cfg.apiSecret,
                passphrase: cfg.passphrase,
                symbols: cfg.symbols,
                stop_loss_pct: cfg.stopLossPct,
                take_profit_pct: cfg.takeProfitPct,
                max_position_pct: cfg.maxPositionPct,
                max_open_trades: cfg.maxOpenTrades,
                margin_mode: cfg.marginMode,
                max_leverage: cfg.maxLeverage,
                risk_per_trade_pct: cfg.riskPerTradePct,
                max_total_exposure_pct: cfg.maxTotalExposurePct,
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

  const selectedExchangeBlocked = !isExchangeEnabled(cfg.exchange);

  return (
    <SafeAreaView style={styles.safe} edges={['bottom']}>
      <ScrollView contentContainerStyle={styles.scroll} showsVerticalScrollIndicator={false}>
        <View style={styles.statusRow}>
          <View style={[styles.statusDot, { backgroundColor: isEnabled ? COLORS.success : COLORS.textMuted }]} />
          <Text style={[styles.statusLabel, { color: isEnabled ? COLORS.success : COLORS.textMuted }]}>
            {loadingStatus ? 'CHECKING…' : isEnabled ? 'AUTOTRADER ACTIVE' : 'AUTOTRADER IDLE'}
          </Text>
          <TouchableOpacity onPress={refreshStatus} style={styles.refreshBtn}>
            <Ionicons name="refresh" size={16} color={COLORS.textMuted} />
          </TouchableOpacity>
        </View>

        <LinearGradient
          colors={isEnabled ? ['#065F46', '#047857'] as [string, string] : COLORS.gradientCard as [string, string]}
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
            {toggling ? <ActivityIndicator color={COLORS.text} /> : <Switch
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

        <Text style={styles.sectionTitle}>Exchange Connection</Text>
        <View style={styles.card}>
          <Text style={styles.fieldLabel}>Exchange</Text>
          <View style={styles.chips}>
            {EXCHANGE_OPTIONS.map((ex) => {
              const blocked = !isExchangeEnabled(ex);
              return (
                <TouchableOpacity
                  key={ex}
                  style={[
                    styles.chip,
                    cfg.exchange === ex && styles.chipActive,
                    blocked && styles.chipDisabled,
                  ]}
                  onPress={() => !isEnabled && !blocked && updateCfg({ exchange: ex })}
                  disabled={isEnabled || blocked}
                >
                  <Text style={[styles.chipText, cfg.exchange === ex && styles.chipTextActive]}>
                    {ex.toUpperCase()}
                  </Text>
                </TouchableOpacity>
              );
            })}
          </View>

          {selectedExchangeBlocked && (
            <View style={styles.warnBox}>
              <Ionicons name="warning-outline" size={16} color={COLORS.warning} />
              <Text style={styles.warnText}>Execution not available from server region. Use OKX/MEXC or Local Executor.</Text>
            </View>
          )}

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

          <Text style={[styles.fieldLabel, { marginTop: SPACING.sm }]}>Passphrase (optional)</Text>
          <TextInput
            style={styles.input}
            value={cfg.passphrase}
            onChangeText={(v) => updateCfg({ passphrase: v })}
            placeholder="Needed for some exchanges (e.g. OKX)"
            placeholderTextColor={COLORS.textMuted}
            secureTextEntry
            editable={!isEnabled}
          />

          <TouchableOpacity style={styles.syncBtn} onPress={syncPortfolio} disabled={syncingPortfolio || isEnabled}>
            {syncingPortfolio ? <ActivityIndicator size="small" color={COLORS.text} /> : <>
              <Ionicons name="sync-outline" size={16} color={COLORS.text} />
              <Text style={styles.syncBtnText}>Sync Portfolio</Text>
            </>}
          </TouchableOpacity>

          {portfolio.lastSyncTs && (
            <Text style={styles.syncMeta}>
              Synced {new Date(portfolio.lastSyncTs).toLocaleTimeString()} · Equity ${portfolio.equity.toFixed(2)} · Used Margin ${portfolio.usedMargin.toFixed(2)}
            </Text>
          )}

          <View style={styles.securityNote}>
            <Ionicons name="lock-closed" size={12} color={COLORS.success} />
            <Text style={styles.securityText}>Use read-only keys without withdrawal permissions.</Text>
          </View>
        </View>

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

          <Text style={[styles.fieldLabel, { marginTop: SPACING.md }]}>Margin Mode</Text>
          <View style={styles.chips}>
            {(['isolated', 'cross'] as const).map((mode) => (
              <TouchableOpacity
                key={mode}
                style={[styles.chip, cfg.marginMode === mode && styles.chipActive]}
                onPress={() => !isEnabled && updateCfg({ marginMode: mode })}
                disabled={isEnabled}
              >
                <Text style={[styles.chipText, cfg.marginMode === mode && styles.chipTextActive]}>{mode.toUpperCase()}</Text>
              </TouchableOpacity>
            ))}
          </View>

          <View style={styles.paramGrid}>
            {([
              { label: 'Stop Loss %', key: 'stopLossPct' },
              { label: 'Take Profit %', key: 'takeProfitPct' },
              { label: 'Max Position %', key: 'maxPositionPct' },
              { label: 'Max Open Trades', key: 'maxOpenTrades' },
              { label: 'Max Leverage', key: 'maxLeverage' },
              { label: 'Risk / Trade %', key: 'riskPerTradePct' },
              { label: 'Max Total Exposure %', key: 'maxTotalExposurePct' },
            ] as const).map(({ label, key }) => (
              <View key={key} style={styles.paramField}>
                <Text style={styles.fieldLabel}>{label}</Text>
                <TextInput
                  style={styles.paramInput}
                  value={String(cfg[key])}
                  onChangeText={(v) => {
                    const n = key === 'maxOpenTrades' ? parseInt(v, 10) : parseFloat(v);
                    if (!isNaN(n)) updateCfg({ [key]: n } as Partial<typeof cfg>);
                  }}
                  keyboardType="numeric"
                  editable={!isEnabled}
                />
              </View>
            ))}
          </View>
        </View>

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

        <View style={styles.infoBox}>
          <Ionicons name="information-circle-outline" size={16} color={COLORS.info} />
          <Text style={styles.infoText}>
            AutoTrader uses Sentinel AI&apos;s market analysis and executes via your exchange API. You can disable it or close individual positions at any time.
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
  statusRow: { flexDirection: 'row', alignItems: 'center', marginBottom: SPACING.md },
  statusDot: { width: 8, height: 8, borderRadius: 4, marginRight: 6 },
  statusLabel: { fontSize: FONT_SIZES.sm, fontWeight: '600', flex: 1, letterSpacing: 1 },
  refreshBtn: { padding: 4 },
  mainCard: { borderRadius: BORDER_RADIUS.xl, padding: SPACING.lg, marginBottom: SPACING.lg, ...SHADOWS.md },
  toggleRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  toggleLeft: { flexDirection: 'row', alignItems: 'center', flex: 1 },
  toggleText: { marginLeft: SPACING.md },
  toggleTitle: { color: COLORS.text, fontSize: FONT_SIZES.lg, fontWeight: '700' },
  toggleSub: { color: COLORS.textSecondary, fontSize: FONT_SIZES.sm, marginTop: 2 },
  activeStats: { flexDirection: 'row', justifyContent: 'space-around', marginTop: SPACING.lg, paddingTop: SPACING.md, borderTopWidth: 1, borderTopColor: 'rgba(255,255,255,0.15)' },
  activeStat: { alignItems: 'center' },
  activeStatValue: { color: COLORS.text, fontSize: FONT_SIZES.xl, fontWeight: '700' },
  activeStatLabel: { color: COLORS.textSecondary, fontSize: FONT_SIZES.xs, marginTop: 2 },
  sectionTitle: { color: COLORS.text, fontSize: FONT_SIZES.md, fontWeight: '700', marginBottom: SPACING.sm, marginTop: SPACING.sm },
  card: { backgroundColor: COLORS.backgroundCard, borderRadius: BORDER_RADIUS.lg, padding: SPACING.md, marginBottom: SPACING.md, ...SHADOWS.sm },
  fieldLabel: { color: COLORS.textSecondary, fontSize: FONT_SIZES.xs, fontWeight: '600', marginBottom: 6 },
  chips: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  chip: { paddingHorizontal: SPACING.sm, paddingVertical: 6, borderRadius: BORDER_RADIUS.full, borderWidth: 1, borderColor: COLORS.border },
  chipActive: { backgroundColor: COLORS.primary, borderColor: COLORS.primary },
  chipDisabled: { opacity: 0.4 },
  chipText: { color: COLORS.textMuted, fontSize: FONT_SIZES.xs, fontWeight: '600' },
  chipTextActive: { color: COLORS.text },
  input: { backgroundColor: COLORS.surface, borderRadius: BORDER_RADIUS.md, paddingHorizontal: SPACING.md, paddingVertical: SPACING.sm, color: COLORS.text, fontSize: FONT_SIZES.sm, borderWidth: 1, borderColor: COLORS.border },
  warnBox: { flexDirection: 'row', alignItems: 'center', gap: 8, marginTop: SPACING.sm, backgroundColor: COLORS.warning + '20', borderRadius: BORDER_RADIUS.md, padding: SPACING.sm },
  warnText: { color: COLORS.warning, fontSize: FONT_SIZES.xs, flex: 1 },
  syncBtn: { marginTop: SPACING.md, borderRadius: BORDER_RADIUS.md, backgroundColor: COLORS.primary, paddingVertical: 10, alignItems: 'center', justifyContent: 'center', flexDirection: 'row', gap: 8 },
  syncBtnText: { color: COLORS.text, fontWeight: '700', fontSize: FONT_SIZES.sm },
  syncMeta: { marginTop: SPACING.xs, color: COLORS.textMuted, fontSize: FONT_SIZES.xs },
  securityNote: { flexDirection: 'row', alignItems: 'center', gap: 4, marginTop: SPACING.sm },
  securityText: { color: COLORS.success, fontSize: FONT_SIZES.xs },
  paramGrid: { flexDirection: 'row', flexWrap: 'wrap', gap: SPACING.sm, marginTop: SPACING.md },
  paramField: { width: '47%' },
  paramInput: { backgroundColor: COLORS.surface, borderRadius: BORDER_RADIUS.md, paddingHorizontal: SPACING.md, paddingVertical: SPACING.sm, color: COLORS.text, fontSize: FONT_SIZES.md, fontWeight: '600', borderWidth: 1, borderColor: COLORS.border, textAlign: 'center' },
  tradeCard: { backgroundColor: COLORS.backgroundCard, borderRadius: BORDER_RADIUS.lg, padding: SPACING.md, marginBottom: SPACING.sm, ...SHADOWS.sm },
  tradeRow: { flexDirection: 'row', alignItems: 'center' },
  sideBadge: { padding: 6, borderRadius: BORDER_RADIUS.sm },
  sideText: { fontSize: FONT_SIZES.xs, fontWeight: '700' },
  tradeBody: { flex: 1, marginLeft: SPACING.sm },
  tradeSymbol: { color: COLORS.text, fontSize: FONT_SIZES.md, fontWeight: '600' },
  tradeDetail: { color: COLORS.textMuted, fontSize: FONT_SIZES.xs, marginTop: 2 },
  tradePnl: { fontSize: FONT_SIZES.md, fontWeight: '700' },
  closeBtn: { marginTop: SPACING.sm, borderWidth: 1, borderColor: COLORS.error, borderRadius: BORDER_RADIUS.md, paddingVertical: 8, alignItems: 'center' },
  closeBtnText: { color: COLORS.error, fontSize: FONT_SIZES.sm, fontWeight: '600' },
  infoBox: { flexDirection: 'row', gap: SPACING.sm, backgroundColor: COLORS.info + '15', borderRadius: BORDER_RADIUS.lg, padding: SPACING.md, marginTop: SPACING.sm, alignItems: 'flex-start' },
  infoText: { color: COLORS.textSecondary, fontSize: FONT_SIZES.xs, flex: 1, lineHeight: 18 },
});
