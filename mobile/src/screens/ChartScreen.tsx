import React, { useEffect, useState, useCallback, useMemo } from 'react';
import {
  View,
  Text,
  StyleSheet,
  Dimensions,
  TouchableOpacity,
  ActivityIndicator,
  ScrollView,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRoute, RouteProp, useNavigation } from '@react-navigation/native';
import { Ionicons } from '@expo/vector-icons';
import Svg, { Rect, Line, Text as SvgText } from 'react-native-svg';
import { COLORS, SPACING, FONT_SIZES, BORDER_RADIUS } from '../constants/theme';
import { chartAPI, Candle, Timeframe, TIMEFRAMES } from '../services/chartApi';
import { RootStackParamList } from '../../App';
import { useStore } from '../store/useStore';

const { width: SCREEN_W } = Dimensions.get('window');
const H_PAD = 16;
const PRICE_AXIS_W = 62;
const CHART_INNER_W = SCREEN_W - H_PAD * 2 - PRICE_AXIS_W;
const CHART_H = 210;
const TIME_AXIS_H = 20;
const SVG_W = SCREEN_W - H_PAD * 2;
const SVG_H = CHART_H + TIME_AXIS_H;
const DEFAULT_CANDLE_LIMIT = 120;
const MIN_VISIBLE_CANDLES = 20;
const MAX_VISIBLE_CANDLES = 120;
const ZOOM_STEP = 10;

function fmtPrice(p: number): string {
  if (p >= 10_000) return p.toLocaleString('en-US', { maximumFractionDigits: 0 });
  if (p >= 1) return p.toFixed(2);
  return p.toFixed(4);
}

function fmtVolume(v: number): string {
  if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(1)}M`;
  if (v >= 1_000) return `${(v / 1_000).toFixed(1)}K`;
  return v.toFixed(0);
}

function fmtTimestamp(ts: number, tf: Timeframe): string {
  const d = new Date(ts);
  if (tf === '1M') return d.toLocaleDateString('en-US', { month: 'short', year: '2-digit' });
  if (tf === '1d') return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
  return d.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', hour12: false });
}

type ChartRoute = RouteProp<RootStackParamList, 'Chart'>;

type AccumulationAnalysis = {
  score: number;
  trend: 'ACCUMULATION' | 'DISTRIBUTION' | 'NEUTRAL';
};

function analyzeAccumulation(source: Candle[], sampleSize: number): AccumulationAnalysis {
  if (!source.length) return { score: 0, trend: 'NEUTRAL' };
  const data = source.slice(-sampleSize);
  const avgVolume = data.reduce((sum, c) => sum + c.volume, 0) / data.length || 1;
  const pressure = data.reduce((sum, c) => {
    const body = c.close - c.open;
    const range = Math.max(c.high - c.low, 0.0000001);
    const bodyRatio = body / range;
    const volumeWeight = Math.min(c.volume / avgVolume, 2.5);
    return sum + bodyRatio * volumeWeight;
  }, 0);

  const normalized = Math.max(-100, Math.min(100, (pressure / data.length) * 60));
  if (normalized > 20) return { score: normalized, trend: 'ACCUMULATION' };
  if (normalized < -20) return { score: normalized, trend: 'DISTRIBUTION' };
  return { score: normalized, trend: 'NEUTRAL' };
}

export default function ChartScreen() {
  const route = useRoute<ChartRoute>();
  const navigation = useNavigation();
  const { symbol } = route.params;
  const subscription = useStore((s) => s.subscription);

  const [timeframe, setTimeframe] = useState<Timeframe>('1h');
  const [candles, setCandles] = useState<Candle[]>([]);
  const [visibleCount, setVisibleCount] = useState(60);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await chartAPI.getCandles(symbol, timeframe, DEFAULT_CANDLE_LIMIT);
      setCandles(data);
      setVisibleCount(Math.min(60, data.length));
    } catch (e: any) {
      setError(e?.message ?? 'Failed to load chart data');
    } finally {
      setLoading(false);
    }
  }, [symbol, timeframe]);

  useEffect(() => {
    load();
  }, [load]);

  const visibleCandles = useMemo(() => {
    if (!candles.length) return [];
    return candles.slice(-visibleCount);
  }, [candles, visibleCount]);

  const last = visibleCandles[visibleCandles.length - 1];
  const first = visibleCandles[0];
  const pctChg = last && first ? ((last.close - first.open) / first.open) * 100 : 0;
  const periodHigh = visibleCandles.length > 0 ? Math.max(...visibleCandles.map((c) => c.high)) : 0;
  const periodLow = visibleCandles.length > 0 ? Math.min(...visibleCandles.map((c) => c.low)) : 0;
  const totalVol = visibleCandles.reduce((s, c) => s + c.volume, 0);

  const paddedMin = visibleCandles.length > 0 ? periodLow - (periodHigh - periodLow) * 0.04 : 0;
  const paddedMax = visibleCandles.length > 0 ? periodHigh + (periodHigh - periodLow) * 0.04 : 1;
  const priceRange = paddedMax - paddedMin || 1;

  const toY = (price: number) => CHART_H - ((price - paddedMin) / priceRange) * CHART_H;

  const num = visibleCandles.length || 1;
  const candleSlot = CHART_INNER_W / num;
  const bodyW = Math.max(1, candleSlot * 0.6);
  const wickW = Math.max(0.5, bodyW * 0.18);
  const candleX = (i: number) => i * candleSlot + (candleSlot - bodyW) / 2;
  const wickX = (i: number) => i * candleSlot + candleSlot / 2;

  const priceGrid = Array.from({ length: 5 }, (_, i) => {
    const price = paddedMin + (priceRange * i) / 4;
    return { price, y: toY(price) };
  });

  const tStep = Math.max(1, Math.floor(num / 4));
  const timeLabels = visibleCandles
    .filter((_, i) => i % tStep === 0)
    .map((c, i) => ({
      label: fmtTimestamp(c.timestamp, timeframe),
      x: wickX(i * tStep),
    }));

  const canSeeAccumulationPanel = subscription === 'pro' || subscription === 'elite' || subscription === 'whale';
  const accumulation4h = analyzeAccumulation(candles, 24);
  const accumulation1d = analyzeAccumulation(candles, 60);

  const zoomIn = () => setVisibleCount((prev) => Math.max(MIN_VISIBLE_CANDLES, prev - ZOOM_STEP));
  const zoomOut = () => setVisibleCount((prev) => Math.min(Math.min(MAX_VISIBLE_CANDLES, candles.length), prev + ZOOM_STEP));

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      <ScrollView>
        <View style={styles.header}>
          <TouchableOpacity onPress={() => navigation.goBack()} style={styles.backBtn}>
            <Ionicons name="arrow-back" size={20} color={COLORS.text} />
          </TouchableOpacity>
          <View style={styles.headerCenter}>
            <Text style={styles.symbol}>{symbol}</Text>
            <Text style={styles.price}>{last ? `$${fmtPrice(last.close)}` : '--'}</Text>
          </View>
          <View style={[styles.badge, { backgroundColor: pctChg >= 0 ? COLORS.chartGreen + '20' : COLORS.chartRed + '20' }]}>
            <Text style={[styles.badgeText, { color: pctChg >= 0 ? COLORS.chartGreen : COLORS.chartRed }]}>
              {pctChg >= 0 ? '+' : ''}{pctChg.toFixed(2)}%
            </Text>
          </View>
        </View>

        <View style={styles.tfRow}>
          {TIMEFRAMES.map((tf) => (
            <TouchableOpacity
              key={tf.value}
              style={[styles.tfBtn, timeframe === tf.value && styles.tfBtnActive]}
              onPress={() => setTimeframe(tf.value)}
            >
              <Text style={[styles.tfLabel, timeframe === tf.value && styles.tfLabelActive]}>{tf.label}</Text>
            </TouchableOpacity>
          ))}
        </View>

        <View style={styles.zoomRow}>
          <Text style={styles.zoomLabel}>Zoom</Text>
          <TouchableOpacity style={styles.zoomBtn} onPress={zoomIn} disabled={visibleCount <= MIN_VISIBLE_CANDLES}>
            <Ionicons name="remove" size={18} color={COLORS.text} />
          </TouchableOpacity>
          <TouchableOpacity style={styles.zoomBtn} onPress={zoomOut} disabled={visibleCount >= Math.min(MAX_VISIBLE_CANDLES, candles.length)}>
            <Ionicons name="add" size={18} color={COLORS.text} />
          </TouchableOpacity>
          <Text style={styles.zoomHint}>{visibleCount} candles</Text>
        </View>

        <View style={styles.chartWrap}>
          {loading ? (
            <View style={styles.centerBox}>
              <ActivityIndicator size="large" color={COLORS.primary} />
              <Text style={styles.loadingText}>Loading {timeframe} data…</Text>
            </View>
          ) : error ? (
            <View style={styles.centerBox}>
              <Ionicons name="warning-outline" size={32} color={COLORS.error} />
              <Text style={styles.errorText}>{error}</Text>
              <TouchableOpacity onPress={load} style={styles.retryBtn}>
                <Text style={styles.retryLabel}>Retry</Text>
              </TouchableOpacity>
            </View>
          ) : (
            <Svg width={SVG_W} height={SVG_H}>
              {priceGrid.map((g, i) => (
                <React.Fragment key={i}>
                  <Line x1={0} y1={g.y} x2={CHART_INNER_W} y2={g.y} stroke={COLORS.border} strokeWidth={0.5} strokeDasharray="3,4" />
                  <SvgText x={CHART_INNER_W + 4} y={g.y + 3} fontSize={9} fill={COLORS.textMuted}>{fmtPrice(g.price)}</SvgText>
                </React.Fragment>
              ))}

              {visibleCandles.map((c, i) => {
                const isBull = c.close >= c.open;
                const color = isBull ? COLORS.chartGreen : COLORS.chartRed;
                const bodyTop = toY(Math.max(c.open, c.close));
                const bodyBot = toY(Math.min(c.open, c.close));
                const bodyH = Math.max(1, bodyBot - bodyTop);
                const wx = wickX(i);
                const bx = candleX(i);

                return (
                  <React.Fragment key={i}>
                    <Line x1={wx} y1={toY(c.high)} x2={wx} y2={toY(c.low)} stroke={color} strokeWidth={wickW} />
                    <Rect x={bx} y={bodyTop} width={bodyW} height={bodyH} fill={color} fillOpacity={isBull ? 0.8 : 1} />
                  </React.Fragment>
                );
              })}

              {timeLabels.map((tl, i) => (
                <SvgText key={i} x={tl.x} y={CHART_H + 14} fontSize={9} fill={COLORS.textMuted} textAnchor="middle">
                  {tl.label}
                </SvgText>
              ))}
            </Svg>
          )}
        </View>

        {!loading && !error && last && (
          <View style={styles.statsCard}>
            <View style={styles.statRow}>
              <View style={styles.statItem}>
                <Text style={styles.statLabel}>High</Text>
                <Text style={[styles.statVal, { color: COLORS.chartGreen }]}>${fmtPrice(periodHigh)}</Text>
              </View>
              <View style={styles.statItem}>
                <Text style={styles.statLabel}>Low</Text>
                <Text style={[styles.statVal, { color: COLORS.chartRed }]}>${fmtPrice(periodLow)}</Text>
              </View>
              <View style={styles.statItem}>
                <Text style={styles.statLabel}>Open</Text>
                <Text style={styles.statVal}>${fmtPrice(first?.open ?? 0)}</Text>
              </View>
              <View style={styles.statItem}>
                <Text style={styles.statLabel}>Volume</Text>
                <Text style={styles.statVal}>{fmtVolume(totalVol)}</Text>
              </View>
            </View>
          </View>
        )}

        {!loading && !error && canSeeAccumulationPanel && (
          <View style={styles.analysisCard}>
            <View style={styles.analysisHeader}>
              <Ionicons name="analytics-outline" size={16} color={COLORS.primary} />
              <Text style={styles.analysisTitle}>Smart Flow Analysis (4H / 1D)</Text>
            </View>
            <Text style={styles.analysisText}>4H: {accumulation4h.trend} ({accumulation4h.score.toFixed(1)})</Text>
            <Text style={styles.analysisText}>1D: {accumulation1d.trend} ({accumulation1d.score.toFixed(1)})</Text>
            <Text style={styles.analysisHint}>Based on candle body pressure and volume-weighted momentum.</Text>
          </View>
        )}

        {!loading && !error && !canSeeAccumulationPanel && (
          <View style={styles.infoChip}>
            <Ionicons name="lock-closed-outline" size={14} color={COLORS.textMuted} />
            <Text style={styles.infoText}>Smart Flow Analysis available on Pro/Elite/Whale tiers.</Text>
          </View>
        )}

        {!loading && !error && (
          <View style={styles.infoChip}>
            <Ionicons name="information-circle-outline" size={14} color={COLORS.textMuted} />
            <Text style={styles.infoText}>Showing {visibleCandles.length} / {candles.length} candles · {timeframe} · Binance</Text>
          </View>
        )}

        <View style={{ height: SPACING.xxl }} />
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: COLORS.background },
  header: { flexDirection: 'row', alignItems: 'center', paddingHorizontal: H_PAD, paddingVertical: SPACING.md },
  backBtn: {
    width: 36,
    height: 36,
    borderRadius: BORDER_RADIUS.md,
    backgroundColor: COLORS.surface,
    justifyContent: 'center',
    alignItems: 'center',
    marginRight: SPACING.sm,
  },
  headerCenter: { flex: 1 },
  symbol: { fontSize: FONT_SIZES.lg, fontWeight: '700', color: COLORS.text },
  price: { fontSize: FONT_SIZES.xxl, fontWeight: '700', color: COLORS.text, marginTop: 2 },
  badge: { paddingHorizontal: SPACING.sm, paddingVertical: SPACING.xs, borderRadius: BORDER_RADIUS.md },
  badgeText: { fontSize: FONT_SIZES.sm, fontWeight: '600' },
  tfRow: { flexDirection: 'row', paddingHorizontal: H_PAD, marginBottom: SPACING.md, gap: SPACING.xs },
  tfBtn: { flex: 1, paddingVertical: SPACING.xs + 2, borderRadius: BORDER_RADIUS.md, backgroundColor: COLORS.surface, alignItems: 'center' },
  tfBtnActive: { backgroundColor: COLORS.primary },
  tfLabel: { fontSize: FONT_SIZES.sm, fontWeight: '600', color: COLORS.textMuted },
  tfLabelActive: { color: COLORS.text },
  zoomRow: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: H_PAD,
    marginBottom: SPACING.sm,
    gap: SPACING.sm,
  },
  zoomLabel: { fontSize: FONT_SIZES.sm, color: COLORS.textMuted, fontWeight: '600' },
  zoomBtn: {
    width: 32,
    height: 32,
    borderRadius: BORDER_RADIUS.md,
    backgroundColor: COLORS.surface,
    borderWidth: 1,
    borderColor: COLORS.border,
    justifyContent: 'center',
    alignItems: 'center',
  },
  zoomHint: { marginLeft: 'auto', fontSize: FONT_SIZES.xs, color: COLORS.textMuted },
  chartWrap: {
    marginHorizontal: H_PAD,
    backgroundColor: COLORS.surface,
    borderRadius: BORDER_RADIUS.lg,
    borderWidth: 1,
    borderColor: COLORS.border,
    paddingVertical: SPACING.md,
    minHeight: SVG_H + SPACING.md * 2,
    justifyContent: 'center',
    alignItems: 'center',
    marginBottom: SPACING.md,
    overflow: 'hidden',
  },
  centerBox: { height: SVG_H, justifyContent: 'center', alignItems: 'center', gap: SPACING.sm },
  loadingText: { fontSize: FONT_SIZES.sm, color: COLORS.textMuted, marginTop: SPACING.sm },
  errorText: {
    fontSize: FONT_SIZES.sm,
    color: COLORS.error,
    textAlign: 'center',
    marginTop: SPACING.xs,
    paddingHorizontal: SPACING.lg,
  },
  retryBtn: {
    marginTop: SPACING.sm,
    backgroundColor: COLORS.primary,
    paddingHorizontal: SPACING.lg,
    paddingVertical: SPACING.sm,
    borderRadius: BORDER_RADIUS.md,
  },
  retryLabel: { color: COLORS.text, fontWeight: '600', fontSize: FONT_SIZES.sm },
  statsCard: {
    marginHorizontal: H_PAD,
    backgroundColor: COLORS.surface,
    borderRadius: BORDER_RADIUS.lg,
    borderWidth: 1,
    borderColor: COLORS.border,
    padding: SPACING.md,
    marginBottom: SPACING.sm,
  },
  statRow: { flexDirection: 'row', justifyContent: 'space-between' },
  statItem: { alignItems: 'center', flex: 1 },
  statLabel: { fontSize: FONT_SIZES.xs, color: COLORS.textMuted, marginBottom: 4 },
  statVal: { fontSize: FONT_SIZES.sm, fontWeight: '600', color: COLORS.text },
  analysisCard: {
    marginHorizontal: H_PAD,
    marginBottom: SPACING.sm,
    backgroundColor: COLORS.surface,
    borderRadius: BORDER_RADIUS.lg,
    borderWidth: 1,
    borderColor: COLORS.border,
    padding: SPACING.md,
    gap: SPACING.xs,
  },
  analysisHeader: { flexDirection: 'row', alignItems: 'center', gap: SPACING.xs },
  analysisTitle: { color: COLORS.text, fontSize: FONT_SIZES.sm, fontWeight: '700' },
  analysisText: { color: COLORS.text, fontSize: FONT_SIZES.sm },
  analysisHint: { color: COLORS.textMuted, fontSize: FONT_SIZES.xs, marginTop: 2 },
  infoChip: { flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 4, marginTop: SPACING.xs },
  infoText: { fontSize: FONT_SIZES.xs, color: COLORS.textMuted },
});
