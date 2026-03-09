import React, { useState, useCallback, useEffect, useRef } from 'react';
import {
  View, Text, ScrollView, StyleSheet, Switch, TouchableOpacity,
  Alert, TextInput, ActivityIndicator, Modal,
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
const SLEEP_POLL_MS = 15000; // poll sleep status every 15s
const PROTECTION_POLL_MS = 30000; // check protection status every 30s

type SleepTrade = {
  id?: string;
  symbol: string;
  side: string;
  amount: number;
  entry_price: number;
  leverage: number;
  sl_price: number;
  tp_price: number;
  confidence: number;
  opened_at: number;
  status: string;
  pnl_pct?: number;
  closed_at?: number;
};

type ProtectionAction = {
  id: string;
  type: 'hedge' | 'safe_order' | 'sl_adjust' | 'tp_adjust' | 'reduce';
  symbol: string;
  description: string;
  timestamp: number;
  status: 'pending' | 'executed' | 'failed';
};

type SleepStatus = {
  active: boolean;
  started_at?: number;
  ends_at?: number;
  elapsed_s?: number;
  remaining_s?: number;
  trade_count?: number;
  realized_pnl?: number;
  status?: string;
  trades?: SleepTrade[];
  log?: string[];
};

function formatDuration(seconds: number): string {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

export default function AutoTraderScreen() {
  const { user, autoTrader, setAutoTrader } = useStore();
  const [toggling, setToggling] = useState(false);
  const [loadingStatus, setLoadingStatus] = useState(false);
  const [closingId, setClosingId] = useState<string | null>(null);
  const [syncingPortfolio, setSyncingPortfolio] = useState(false);
  const [sleepStatus, setSleepStatus] = useState<SleepStatus>({ active: false });
  const [startingSleep, setStartingSleep] = useState(false);
  const [stoppingSleep, setStoppingSleep] = useState(false);
  const hasShownBrainMisconfigAlert = useRef(false);
  const sleepPollRef = useRef<NodeJS.Timeout | null>(null);
  const protectionPollRef = useRef<NodeJS.Timeout | null>(null);

  // SL/TP edit modal state
  const [editingTrade, setEditingTrade] = useState<ActiveTrade | null>(null);
  const [editSL, setEditSL] = useState('');
  const [editTP, setEditTP] = useState('');
  const [savingSlTp, setSavingSlTp] = useState(false);

  // Trade protection state
  const [protectionEnabled, setProtectionEnabled] = useState(true);
  const [protectionActions, setProtectionActions] = useState<ProtectionAction[]>([]);
  const [protectionChecking, setProtectionChecking] = useState(false);

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

  const pollSleepStatus = useCallback(async () => {
    if (!user?.id) return;
    try {
      const res = await brainAPI.getSleepStatus(user.id);
      if (res.ok) {
        setSleepStatus({
          active: res.active,
          started_at: res.started_at,
          ends_at: res.ends_at,
          elapsed_s: res.elapsed_s,
          remaining_s: res.remaining_s,
          trade_count: res.trade_count,
          realized_pnl: res.realized_pnl,
          status: res.status,
          trades: res.trades,
          log: res.log,
        });
      }
    } catch {
      // silent
    }
  }, [user?.id]);

  // Poll sleep status when active
  useEffect(() => {
    if (sleepStatus.active || isEnabled) {
      pollSleepStatus();
      sleepPollRef.current = setInterval(pollSleepStatus, SLEEP_POLL_MS);
    }
    return () => {
      if (sleepPollRef.current) clearInterval(sleepPollRef.current);
    };
  }, [sleepStatus.active, isEnabled, pollSleepStatus]);

  // Trade protection: monitor and protect user positions
  const checkProtection = useCallback(async () => {
    if (!user?.id || !isEnabled || !protectionEnabled) return;
    if (!cfg.apiKey || !cfg.apiSecret) return;
    setProtectionChecking(true);
    try {
      const res = await brainAPI.checkTradeProtection({
        user_id: user.id,
        exchange: cfg.exchange,
        api_key: cfg.apiKey,
        api_secret: cfg.apiSecret,
        passphrase: cfg.passphrase || undefined,
        mode: sleepStatus.active ? 'sleep' : 'active',
        config: {
          stop_loss_pct: cfg.stopLossPct,
          take_profit_pct: cfg.takeProfitPct,
          max_leverage: cfg.maxLeverage,
          max_total_exposure_pct: cfg.maxTotalExposurePct,
        },
      }).catch(() => ({ ok: false, actions: [] }));
      if (res.ok && res.actions) {
        setProtectionActions((prev) => {
          const existing = new Set(prev.map((a) => a.id));
          const newActions = res.actions.filter((a: ProtectionAction) => !existing.has(a.id));
          return [...newActions, ...prev].slice(0, 20);
        });
      }
    } catch {
      // silent
    } finally {
      setProtectionChecking(false);
    }
  }, [user?.id, isEnabled, protectionEnabled, cfg, sleepStatus.active]);

  useEffect(() => {
    if (isEnabled && protectionEnabled) {
      checkProtection();
      protectionPollRef.current = setInterval(checkProtection, PROTECTION_POLL_MS);
    }
    return () => {
      if (protectionPollRef.current) clearInterval(protectionPollRef.current);
    };
  }, [isEnabled, protectionEnabled, checkProtection]);

  // SL/TP editing
  const openEditSlTp = (trade: ActiveTrade) => {
    setEditingTrade(trade);
    setEditSL(String(trade.stopLoss ?? cfg.stopLossPct));
    setEditTP(String(trade.takeProfit ?? cfg.takeProfitPct));
  };

  const handleSaveSlTp = async () => {
    if (!editingTrade || !user?.id) return;
    const sl = parseFloat(editSL);
    const tp = parseFloat(editTP);
    if (isNaN(sl) || isNaN(tp) || sl <= 0 || tp <= 0) {
      Alert.alert('Invalid', 'Enter valid SL and TP values.');
      return;
    }
    setSavingSlTp(true);
    try {
      await brainAPI.updateTradeSlTp(user.id, editingTrade.id, sl, tp).catch(() => {});
      // Update local state
      setAutoTrader({
        activeTrades: autoTrader.activeTrades.map((t) =>
          t.id === editingTrade.id ? { ...t, stopLoss: sl, takeProfit: tp } : t
        ),
      });
      setEditingTrade(null);
    } finally {
      setSavingSlTp(false);
    }
  };

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
      await pollSleepStatus();
    } catch {
      // keep UI functional when backend is unavailable
    } finally {
      setLoadingStatus(false);
    }
  }, [user?.id, setAutoTrader, pollSleepStatus]);

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
          if (__DEV__) {
            console.info(
              `Brain service-type validation warning (${CONFIG.BRAIN_URL}):`,
              reason,
            );
          }
        }
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

  const handleStartSleep = () => {
    if (!user?.id || !isEnabled) return;
    Alert.alert(
      'Activate Sleep Mode?',
      `Sentinel will autonomously trade ${cfg.symbols.join(', ')} on ${(cfg.exchange || '').toUpperCase()} for up to 8 hours while you rest.\n\nTarget: 25-30% portfolio profit.\nMax leverage: ${cfg.maxLeverage}x\nPortfolio: $${(portfolio.equity ?? 0).toFixed(2)}`,
      [
        { text: 'Cancel', style: 'cancel' },
        {
          text: 'Start Sleep Mode',
          onPress: async () => {
            setStartingSleep(true);
            try {
              const res = await brainAPI.startSleepMode(user.id);
              if (res.ok) {
                setSleepStatus({ active: true, status: 'running', trade_count: 0, realized_pnl: 0 });
              } else {
                Alert.alert('Sleep Mode', res.error || 'Could not start sleep mode.');
              }
            } catch {
              Alert.alert('Error', 'Failed to start sleep mode.');
            } finally {
              setStartingSleep(false);
            }
          },
        },
      ],
    );
  };

  const handleStopSleep = () => {
    if (!user?.id) return;
    Alert.alert('Stop Sleep Mode?', 'Open positions will remain on your exchange. Sleep Mode trading will stop.', [
      { text: 'Keep Running', style: 'cancel' },
      {
        text: 'Stop',
        style: 'destructive',
        onPress: async () => {
          setStoppingSleep(true);
          try {
            await brainAPI.stopSleepMode(user.id);
            setSleepStatus((prev) => ({ ...prev, active: false, status: 'stopped_by_user' }));
          } catch {
            // silent
          } finally {
            setStoppingSleep(false);
          }
        },
      },
    ]);
  };

  const handleCloseTrade = (trade: ActiveTrade) => {
    Alert.alert(
      'Close Position?',
      `Market-close ${trade.side} ${trade.symbol} (${(trade.pnl ?? 0) >= 0 ? '+' : ''}${(trade.pnl ?? 0).toFixed(2)}%)`,
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

  // Sleep session derived data
  const sleepTrades = sleepStatus.trades ?? [];
  const openSleepTrades = sleepTrades.filter((t) => t.status === 'open');
  const closedSleepTrades = sleepTrades.filter((t) => t.status === 'closed');
  const sleepLog = sleepStatus.log ?? [];

  return (
    <SafeAreaView style={styles.safe} edges={['bottom']}>
      <ScrollView contentContainerStyle={styles.scroll} showsVerticalScrollIndicator={false}>
        <View style={styles.statusRow}>
          <View style={[styles.statusDot, { backgroundColor: sleepStatus.active ? '#8B5CF6' : isEnabled ? COLORS.success : COLORS.textMuted }]} />
          <Text style={[styles.statusLabel, { color: sleepStatus.active ? '#8B5CF6' : isEnabled ? COLORS.success : COLORS.textMuted }]}>
            {loadingStatus ? 'CHECKING\u2026' : sleepStatus.active ? 'SLEEP MODE ACTIVE' : isEnabled ? 'AUTOTRADER ACTIVE' : 'AUTOTRADER IDLE'}
          </Text>
          <TouchableOpacity onPress={refreshStatus} style={styles.refreshBtn}>
            <Ionicons name="refresh" size={16} color={COLORS.textMuted} />
          </TouchableOpacity>
        </View>

        {/* Main toggle card */}
        <LinearGradient
          colors={sleepStatus.active ? ['#4C1D95', '#6D28D9'] as [string, string] : isEnabled ? ['#065F46', '#047857'] as [string, string] : COLORS.gradientCard as [string, string]}
          style={styles.mainCard}
        >
          <View style={styles.toggleRow}>
            <View style={styles.toggleLeft}>
              <Ionicons
                name={sleepStatus.active ? 'moon' : 'hardware-chip'}
                size={30}
                color={sleepStatus.active ? '#C4B5FD' : isEnabled ? COLORS.chartGreen : COLORS.primary}
              />
              <View style={styles.toggleText}>
                <Text style={styles.toggleTitle}>
                  {sleepStatus.active ? 'Sleep Mode' : 'AutoTrader'}
                </Text>
                <Text style={styles.toggleSub}>
                  {sleepStatus.active
                    ? 'Sentinel is trading while you rest'
                    : isEnabled ? 'AI is trading on your behalf' : 'Hand over trades to Sentinel AI'}
                </Text>
              </View>
            </View>
            {toggling ? <ActivityIndicator color={COLORS.text} /> : <Switch
              value={isEnabled}
              onValueChange={handleToggle}
              trackColor={{ false: COLORS.surface, true: COLORS.successDark }}
              thumbColor={isEnabled ? '#fff' : COLORS.textMuted}
              disabled={sleepStatus.active}
            />}
          </View>

          {isEnabled && !sleepStatus.active && (
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

          {sleepStatus.active && (
            <View style={styles.activeStats}>
              {[
                { label: 'Trades', value: sleepStatus.trade_count ?? 0 },
                { label: 'PnL', value: `$${(sleepStatus.realized_pnl ?? 0).toFixed(2)}` },
                { label: 'Remaining', value: formatDuration(sleepStatus.remaining_s ?? 0) },
              ].map(({ label, value }) => (
                <View key={label} style={styles.activeStat}>
                  <Text style={styles.activeStatValue}>{String(value)}</Text>
                  <Text style={styles.activeStatLabel}>{label}</Text>
                </View>
              ))}
            </View>
          )}
        </LinearGradient>

        {/* Sleep Mode Controls */}
        {isEnabled && (
          <View style={styles.card}>
            <View style={{ flexDirection: 'row', alignItems: 'center', marginBottom: SPACING.sm }}>
              <Ionicons name="moon-outline" size={18} color="#8B5CF6" />
              <Text style={[styles.sectionTitle, { marginLeft: 8, marginBottom: 0, marginTop: 0 }]}>
                Sleep Mode
              </Text>
            </View>
            <Text style={{ color: COLORS.textSecondary, fontSize: FONT_SIZES.xs, marginBottom: SPACING.md, lineHeight: 16 }}>
              Activate when you go to sleep. Sentinel will autonomously trade for 8 hours targeting 25-30% portfolio profit using TA-driven entries with SL/TP protection.
            </Text>

            {!sleepStatus.active ? (
              <TouchableOpacity
                style={[styles.sleepBtn, startingSleep && { opacity: 0.6 }]}
                onPress={handleStartSleep}
                disabled={startingSleep}
              >
                {startingSleep ? (
                  <ActivityIndicator color="#fff" />
                ) : (
                  <>
                    <Ionicons name="moon" size={18} color="#fff" />
                    <Text style={styles.sleepBtnText}>Start Sleep Mode (8h)</Text>
                  </>
                )}
              </TouchableOpacity>
            ) : (
              <TouchableOpacity
                style={[styles.sleepStopBtn, stoppingSleep && { opacity: 0.6 }]}
                onPress={handleStopSleep}
                disabled={stoppingSleep}
              >
                {stoppingSleep ? (
                  <ActivityIndicator color={COLORS.error} />
                ) : (
                  <>
                    <Ionicons name="stop-circle" size={18} color={COLORS.error} />
                    <Text style={styles.sleepStopBtnText}>Stop Sleep Mode</Text>
                  </>
                )}
              </TouchableOpacity>
            )}

            {/* Progress bar */}
            {sleepStatus.active && sleepStatus.elapsed_s != null && sleepStatus.ends_at != null && sleepStatus.started_at != null && (
              <View style={styles.progressContainer}>
                <View style={styles.progressBar}>
                  <View
                    style={[styles.progressFill, {
                      width: `${Math.min(100, (sleepStatus.elapsed_s / (sleepStatus.ends_at - sleepStatus.started_at)) * 100)}%`,
                    }]}
                  />
                </View>
                <Text style={styles.progressText}>
                  {formatDuration(sleepStatus.elapsed_s)} elapsed
                </Text>
              </View>
            )}
          </View>
        )}

        {/* Sleep Trades */}
        {sleepStatus.active && openSleepTrades.length > 0 && (
          <>
            <Text style={styles.sectionTitle}>Sleep Mode Open Trades</Text>
            {openSleepTrades.map((trade) => {
              const isLong = trade.side === 'buy';
              const tradeKey = trade.id || `sleep-${trade.symbol}-${trade.opened_at}`;
              return (
                <View key={tradeKey} style={[styles.tradeCard, { borderLeftWidth: 3, borderLeftColor: isLong ? COLORS.success : COLORS.error }]}>
                  <View style={styles.tradeRow}>
                    <View style={[styles.sideBadge, {
                      backgroundColor: isLong ? COLORS.success + '22' : COLORS.error + '22',
                    }]}>
                      <Text style={[styles.sideText, { color: isLong ? COLORS.success : COLORS.error }]}>
                        {isLong ? 'LONG' : 'SHORT'}
                      </Text>
                    </View>
                    <View style={styles.tradeBody}>
                      <Text style={styles.tradeSymbol}>{trade.symbol}</Text>
                      <Text style={styles.tradeDetail}>
                        Entry ${(trade.entry_price ?? 0).toFixed(2)} | {trade.leverage ?? 1}x
                      </Text>
                    </View>
                    <View style={{ alignItems: 'flex-end' }}>
                      <Text style={[styles.tradePnl, { color: '#8B5CF6' }]}>
                        {((trade.confidence ?? 0) * 100).toFixed(0)}% conf
                      </Text>
                    </View>
                  </View>
                  {/* SL/TP for sleep trades */}
                  <View style={styles.slTpRow}>
                    <View style={styles.slTpItem}>
                      <Text style={[styles.slTpLabel, { color: COLORS.error }]}>SL</Text>
                      <Text style={[styles.slTpValue, { color: COLORS.error }]}>${(trade.sl_price ?? 0).toFixed(2)}</Text>
                    </View>
                    <View style={styles.slTpItem}>
                      <Text style={[styles.slTpLabel, { color: COLORS.success }]}>TP</Text>
                      <Text style={[styles.slTpValue, { color: COLORS.success }]}>${(trade.tp_price ?? 0).toFixed(2)}</Text>
                    </View>
                  </View>
                </View>
              );
            })}
          </>
        )}

        {/* Closed Sleep Trades */}
        {closedSleepTrades.length > 0 && (
          <>
            <Text style={styles.sectionTitle}>Sleep Mode Closed Trades</Text>
            {closedSleepTrades.slice(-5).map((trade) => {
              const pnl = trade.pnl_pct ?? 0;
              const pnlColor = pnl >= 0 ? COLORS.success : COLORS.error;
              const tradeKey = trade.id || `closed-${trade.symbol}-${trade.closed_at ?? trade.opened_at}`;
              return (
                <View key={tradeKey} style={[styles.tradeCard, { opacity: 0.8 }]}>
                  <View style={styles.tradeRow}>
                    <View style={[styles.sideBadge, { backgroundColor: pnlColor + '22' }]}>
                      <Text style={[styles.sideText, { color: pnlColor }]}>
                        {trade.side === 'buy' ? 'LONG' : 'SHORT'}
                      </Text>
                    </View>
                    <View style={styles.tradeBody}>
                      <Text style={styles.tradeSymbol}>{trade.symbol}</Text>
                      <Text style={styles.tradeDetail}>
                        ${(trade.entry_price ?? 0).toFixed(2)} | {trade.leverage ?? 1}x
                      </Text>
                    </View>
                    <Text style={[styles.tradePnl, { color: pnlColor }]}>
                      {pnl >= 0 ? '+' : ''}{pnl.toFixed(1)}%
                    </Text>
                  </View>
                  {/* SL/TP for closed sleep trades */}
                  <View style={styles.slTpRow}>
                    <View style={styles.slTpItem}>
                      <Text style={[styles.slTpLabel, { color: COLORS.textMuted }]}>SL</Text>
                      <Text style={[styles.slTpValue, { color: COLORS.textMuted }]}>${(trade.sl_price ?? 0).toFixed(2)}</Text>
                    </View>
                    <View style={styles.slTpItem}>
                      <Text style={[styles.slTpLabel, { color: COLORS.textMuted }]}>TP</Text>
                      <Text style={[styles.slTpValue, { color: COLORS.textMuted }]}>${(trade.tp_price ?? 0).toFixed(2)}</Text>
                    </View>
                  </View>
                </View>
              );
            })}
          </>
        )}

        {/* Sleep Log */}
        {sleepStatus.active && sleepLog.length > 0 && (
          <>
            <Text style={styles.sectionTitle}>Sleep Mode Log</Text>
            <View style={styles.logCard}>
              {sleepLog.slice(-8).map((entry, idx) => (
                <Text key={`log-${sleepLog.length - 8 + idx}-${entry.slice(0, 20)}`} style={styles.logEntry}>{entry}</Text>
              ))}
            </View>
          </>
        )}

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
              Synced {new Date(portfolio.lastSyncTs).toLocaleTimeString()} · Equity ${(portfolio.equity ?? 0).toFixed(2)} · Used Margin ${(portfolio.usedMargin ?? 0).toFixed(2)}
            </Text>
          )}

          <View style={styles.securityNote}>
            <Ionicons name="lock-closed" size={12} color={COLORS.success} />
            <Text style={styles.securityText}>Use trade-enabled keys without withdrawal permissions.</Text>
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

        {/* Trade Protection Controls */}
        {isEnabled && (
          <View style={styles.card}>
            <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: SPACING.sm }}>
              <View style={{ flexDirection: 'row', alignItems: 'center' }}>
                <Ionicons name="shield-checkmark" size={18} color={COLORS.success} />
                <Text style={[styles.sectionTitle, { marginLeft: 8, marginBottom: 0, marginTop: 0 }]}>
                  Trade Protection
                </Text>
              </View>
              <Switch
                value={protectionEnabled}
                onValueChange={setProtectionEnabled}
                trackColor={{ false: COLORS.surface, true: COLORS.success + '60' }}
                thumbColor={protectionEnabled ? COLORS.success : COLORS.textMuted}
              />
            </View>
            <Text style={{ color: COLORS.textSecondary, fontSize: FONT_SIZES.xs, marginBottom: SPACING.sm, lineHeight: 16 }}>
              {sleepStatus.active
                ? 'Sleep Guard: Sentinel monitors your positions and applies hedge/safe orders if markets move against you.'
                : 'Active Guard: Sentinel watches for anomalies and protects existing positions with SL adjustments and hedging.'}
            </Text>
            {protectionChecking && (
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
                <ActivityIndicator size="small" color={COLORS.info} />
                <Text style={{ color: COLORS.textMuted, fontSize: FONT_SIZES.xs }}>Scanning positions...</Text>
              </View>
            )}
            {protectionActions.length > 0 && (
              <View style={{ marginTop: SPACING.sm }}>
                <Text style={{ color: COLORS.textSecondary, fontSize: FONT_SIZES.xs, fontWeight: '600', marginBottom: 4 }}>Recent Actions:</Text>
                {protectionActions.slice(0, 5).map((action) => (
                  <View key={action.id} style={{ flexDirection: 'row', alignItems: 'center', paddingVertical: 3, gap: 6 }}>
                    <Ionicons
                      name={action.type === 'hedge' ? 'swap-horizontal' : action.type === 'safe_order' ? 'layers' : 'trending-down'}
                      size={12}
                      color={action.status === 'executed' ? COLORS.success : action.status === 'failed' ? COLORS.error : COLORS.warning}
                    />
                    <Text style={{ color: COLORS.textMuted, fontSize: 10, flex: 1, fontFamily: 'monospace' as any }}>
                      [{action.type.toUpperCase()}] {action.symbol}: {action.description}
                    </Text>
                  </View>
                ))}
              </View>
            )}
          </View>
        )}

        {autoTrader.activeTrades.length > 0 && (
          <>
            <Text style={styles.sectionTitle}>Active Trades</Text>
            {autoTrader.activeTrades.map((trade, tradeIdx) => {
              const pnl = trade.pnl ?? 0;
              const entryPrice = trade.entryPrice ?? 0;
              const currentPrice = trade.currentPrice ?? 0;
              const pnlColor = pnl >= 0 ? COLORS.success : COLORS.error;
              const sl = trade.stopLoss ?? cfg.stopLossPct;
              const tp = trade.takeProfit ?? cfg.takeProfitPct;
              return (
                <View key={trade.id || `${trade.symbol}-${trade.openedAt || tradeIdx}`} style={styles.tradeCard}>
                  <View style={styles.tradeRow}>
                    <View style={[styles.sideBadge, {
                      backgroundColor: trade.side === 'BUY' ? COLORS.success + '22' : COLORS.error + '22',
                    }]}>
                      <Text style={[styles.sideText, { color: trade.side === 'BUY' ? COLORS.success : COLORS.error }]}>
                        {trade.side === 'BUY' ? 'LONG' : 'SHORT'}
                      </Text>
                    </View>
                    <View style={styles.tradeBody}>
                      <Text style={styles.tradeSymbol}>{trade.symbol}</Text>
                      <Text style={styles.tradeDetail}>Entry ${entryPrice.toFixed(2)}</Text>
                    </View>
                    <View style={{ alignItems: 'flex-end' }}>
                      <Text style={[styles.tradePnl, { color: pnlColor }]}>
                        {pnl >= 0 ? '+' : ''}{pnl.toFixed(2)}%
                      </Text>
                      <Text style={styles.tradeDetail}>${currentPrice.toFixed(2)}</Text>
                    </View>
                  </View>
                  {/* SL/TP Display */}
                  <View style={styles.slTpRow}>
                    <View style={styles.slTpItem}>
                      <Text style={[styles.slTpLabel, { color: COLORS.error }]}>SL</Text>
                      <Text style={[styles.slTpValue, { color: COLORS.error }]}>{sl}%</Text>
                    </View>
                    <View style={styles.slTpItem}>
                      <Text style={[styles.slTpLabel, { color: COLORS.success }]}>TP</Text>
                      <Text style={[styles.slTpValue, { color: COLORS.success }]}>{tp}%</Text>
                    </View>
                    <TouchableOpacity
                      style={styles.editSlTpBtn}
                      onPress={() => openEditSlTp(trade)}
                    >
                      <Ionicons name="pencil" size={14} color={COLORS.primary} />
                      <Text style={styles.editSlTpText}>Edit SL/TP</Text>
                    </TouchableOpacity>
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

        {/* Sentinel Observations for active trades */}
        {isEnabled && autoTrader.activeTrades.length > 0 && (
          <View style={styles.card}>
            <View style={{ flexDirection: 'row', alignItems: 'center', marginBottom: SPACING.sm }}>
              <Ionicons name="eye" size={18} color={COLORS.info} />
              <Text style={[styles.sectionTitle, { marginLeft: 8, marginBottom: 0, marginTop: 0 }]}>
                Sentinel Observations
              </Text>
            </View>
            {autoTrader.activeTrades.map((trade, idx) => {
              const pnl = trade.pnl ?? 0;
              const leverageRisk = (trade as any).leverage >= 50 ? 'EXTREME' : (trade as any).leverage >= 20 ? 'HIGH' : 'MODERATE';
              return (
                <View key={`obs-${trade.id || trade.symbol || idx}`} style={{ marginBottom: 4 }}>
                  <Text style={{ color: COLORS.textSecondary, fontSize: FONT_SIZES.xs }}>
                    <Text style={{ fontWeight: '700', color: pnl >= 5 ? COLORS.success : pnl <= -3 ? COLORS.error : COLORS.textSecondary }}>
                      {trade.symbol}
                    </Text>
                    {' '}
                    {pnl >= 5 ? 'Consider taking partial profit.' : pnl <= -3 ? 'Position at risk — tighten SL.' : 'Position within parameters.'}
                    {leverageRisk === 'EXTREME' && ' Leverage risk: EXTREME.'}
                  </Text>
                </View>
              );
            })}
          </View>
        )}

        <View style={styles.infoBox}>
          <Ionicons name="information-circle-outline" size={16} color={COLORS.info} />
          <Text style={styles.infoText}>
            AutoTrader uses Sentinel AI&apos;s market analysis and executes via your exchange API. Sleep Mode runs autonomously for 8 hours with TA-driven entries. Trade Protection monitors your positions and applies hedge orders or SL adjustments when anomalies are detected. You can stop it or close positions at any time.
          </Text>
        </View>

        <View style={{ height: 32 }} />
      </ScrollView>

      {/* SL/TP Edit Modal */}
      <Modal
        visible={!!editingTrade}
        animationType="slide"
        transparent
        onRequestClose={() => setEditingTrade(null)}
      >
        <View style={styles.modalOverlay}>
          <View style={styles.modalContent}>
            <View style={styles.modalHeader}>
              <Text style={styles.modalTitle}>
                Edit SL/TP — {editingTrade?.symbol}
              </Text>
              <TouchableOpacity onPress={() => setEditingTrade(null)}>
                <Ionicons name="close" size={24} color={COLORS.text} />
              </TouchableOpacity>
            </View>

            <Text style={{ color: COLORS.textSecondary, fontSize: FONT_SIZES.sm, marginBottom: SPACING.md }}>
              {editingTrade?.side === 'BUY' ? 'LONG' : 'SHORT'} @ ${(editingTrade?.entryPrice ?? 0).toFixed(2)} | Current ${(editingTrade?.currentPrice ?? 0).toFixed(2)}
            </Text>

            <Text style={styles.fieldLabel}>Stop Loss %</Text>
            <TextInput
              style={styles.paramInput}
              value={editSL}
              onChangeText={setEditSL}
              keyboardType="numeric"
              placeholder="e.g. 2"
              placeholderTextColor={COLORS.textMuted}
            />

            <Text style={[styles.fieldLabel, { marginTop: SPACING.md }]}>Take Profit %</Text>
            <TextInput
              style={styles.paramInput}
              value={editTP}
              onChangeText={setEditTP}
              keyboardType="numeric"
              placeholder="e.g. 4"
              placeholderTextColor={COLORS.textMuted}
            />

            <TouchableOpacity
              style={[styles.sleepBtn, { marginTop: SPACING.lg, backgroundColor: COLORS.primary }]}
              onPress={handleSaveSlTp}
              disabled={savingSlTp}
            >
              {savingSlTp ? (
                <ActivityIndicator color="#fff" />
              ) : (
                <>
                  <Ionicons name="checkmark" size={18} color="#fff" />
                  <Text style={styles.sleepBtnText}>Save SL/TP</Text>
                </>
              )}
            </TouchableOpacity>
          </View>
        </View>
      </Modal>
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
  sleepBtn: {
    backgroundColor: '#6D28D9',
    borderRadius: BORDER_RADIUS.md,
    paddingVertical: 12,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 8,
  },
  sleepBtnText: { color: '#fff', fontWeight: '700', fontSize: FONT_SIZES.sm },
  sleepStopBtn: {
    borderWidth: 1,
    borderColor: COLORS.error,
    borderRadius: BORDER_RADIUS.md,
    paddingVertical: 12,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 8,
  },
  sleepStopBtnText: { color: COLORS.error, fontWeight: '700', fontSize: FONT_SIZES.sm },
  progressContainer: { marginTop: SPACING.md },
  progressBar: {
    height: 6,
    backgroundColor: 'rgba(255,255,255,0.15)',
    borderRadius: 3,
    overflow: 'hidden',
  },
  progressFill: {
    height: '100%',
    backgroundColor: '#8B5CF6',
    borderRadius: 3,
  },
  progressText: {
    color: COLORS.textMuted,
    fontSize: FONT_SIZES.xs,
    marginTop: 4,
    textAlign: 'right',
  },
  logCard: {
    backgroundColor: COLORS.backgroundCard,
    borderRadius: BORDER_RADIUS.lg,
    padding: SPACING.sm,
    marginBottom: SPACING.md,
  },
  logEntry: {
    color: COLORS.textSecondary,
    fontSize: 10,
    fontFamily: 'monospace' as any,
    lineHeight: 16,
    paddingVertical: 1,
  },
  slTpRow: {
    flexDirection: 'row',
    alignItems: 'center',
    marginTop: SPACING.sm,
    paddingTop: SPACING.sm,
    borderTopWidth: 1,
    borderTopColor: COLORS.border,
    gap: SPACING.md,
  },
  slTpItem: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
  },
  slTpLabel: {
    fontSize: FONT_SIZES.xs,
    fontWeight: '700',
  },
  slTpValue: {
    fontSize: FONT_SIZES.xs,
    fontWeight: '600',
  },
  editSlTpBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
    marginLeft: 'auto',
    paddingHorizontal: SPACING.sm,
    paddingVertical: 4,
    borderRadius: BORDER_RADIUS.sm,
    backgroundColor: COLORS.primary + '15',
    borderWidth: 1,
    borderColor: COLORS.primary + '30',
  },
  editSlTpText: {
    color: COLORS.primary,
    fontSize: FONT_SIZES.xs,
    fontWeight: '600',
  },
  modalOverlay: {
    flex: 1,
    backgroundColor: 'rgba(0,0,0,0.7)',
    justifyContent: 'flex-end',
  },
  modalContent: {
    backgroundColor: COLORS.backgroundCard,
    borderTopLeftRadius: BORDER_RADIUS.xl,
    borderTopRightRadius: BORDER_RADIUS.xl,
    padding: SPACING.lg,
    paddingBottom: SPACING.xl,
  },
  modalHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: SPACING.md,
  },
  modalTitle: {
    color: COLORS.text,
    fontSize: FONT_SIZES.lg,
    fontWeight: '700',
  },
});
