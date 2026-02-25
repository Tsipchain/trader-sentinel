import React, { useEffect, useState, useCallback } from 'react';
import {
  View,
  Text,
  StyleSheet,
  ScrollView,
  TouchableOpacity,
  TextInput,
  ActivityIndicator,
  RefreshControl,
  KeyboardAvoidingView,
  Platform,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import { LinearGradient } from 'expo-linear-gradient';
import { COLORS, SPACING, FONT_SIZES, BORDER_RADIUS } from '../constants/theme';
import { marketAPI, analystAPI, type RiskReport, type AnalystBriefing } from '../services/api';

const RISK_COLORS: Record<string, string> = {
  NEUTRAL: COLORS.success ?? '#22C55E',
  WATCH: '#84CC16',
  CAUTION: COLORS.warning ?? '#F59E0B',
  DEFENSIVE: '#F97316',
  CRITICAL: COLORS.error ?? '#EF4444',
};

function riskColor(level: string): string {
  return RISK_COLORS[level] ?? COLORS.textMuted;
}

function ScoreBar({ score, color }: { score: number; color: string }) {
  return (
    <View style={styles.scoreBarBg}>
      <View style={[styles.scoreBarFill, { width: `${score * 10}%` as any, backgroundColor: color }]} />
    </View>
  );
}

export default function RiskScreen() {
  const [risk, setRisk] = useState<RiskReport | null>(null);
  const [briefing, setBriefing] = useState<AnalystBriefing | null>(null);
  const [loadingRisk, setLoadingRisk] = useState(true);
  const [loadingBriefing, setLoadingBriefing] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [question, setQuestion] = useState('');
  const [answer, setAnswer] = useState('');
  const [asking, setAsking] = useState(false);
  const [analystOnline, setAnalystOnline] = useState<boolean | null>(null);

  const loadRisk = useCallback(async () => {
    try {
      const data = await marketAPI.getRiskReport('BTC/USDT');
      setRisk(data);
    } catch (e) {
      console.error('Risk fetch failed:', e);
    } finally {
      setLoadingRisk(false);
    }
  }, []);

  const loadBriefing = useCallback(async () => {
    setLoadingBriefing(true);
    try {
      const health = await analystAPI.checkHealth();
      setAnalystOnline(health.ok);
      if (health.ok) {
        const data = await analystAPI.getBriefing();
        setBriefing(data);
      }
    } catch {
      setAnalystOnline(false);
    } finally {
      setLoadingBriefing(false);
    }
  }, []);

  useEffect(() => {
    loadRisk();
    loadBriefing();
  }, [loadRisk, loadBriefing]);

  const onRefresh = useCallback(async () => {
    setRefreshing(true);
    await Promise.all([loadRisk(), loadBriefing()]);
    setRefreshing(false);
  }, [loadRisk, loadBriefing]);

  const handleAsk = async () => {
    if (!question.trim() || asking) return;
    setAsking(true);
    setAnswer('');
    try {
      const res = await analystAPI.ask(question.trim());
      setAnswer(res.answer);
    } catch {
      setAnswer('Could not reach the analyst service. Make sure the Railway URL is configured.');
    } finally {
      setAsking(false);
    }
  };

  const level = risk?.recommendation?.level ?? 'UNKNOWN';
  const color = riskColor(level);
  const score = risk?.composite_score ?? 0;

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      <KeyboardAvoidingView
        style={{ flex: 1 }}
        behavior={Platform.OS === 'ios' ? 'padding' : undefined}
      >
        <ScrollView
          contentContainerStyle={styles.scroll}
          refreshControl={
            <RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={COLORS.primary} />
          }
          keyboardShouldPersistTaps="handled"
        >
          {/* Header */}
          <View style={styles.header}>
            <Text style={styles.title}>Risk & AI Analysis</Text>
            <View style={[styles.statusDot, { backgroundColor: analystOnline ? COLORS.success ?? '#22C55E' : COLORS.textMuted }]} />
          </View>

          {/* ── Composite Risk Card ── */}
          {loadingRisk ? (
            <View style={styles.loadingCard}>
              <ActivityIndicator color={COLORS.primary} />
              <Text style={styles.loadingText}>Loading risk report…</Text>
            </View>
          ) : risk ? (
            <LinearGradient
              colors={[color + '20', COLORS.surface]}
              style={styles.riskCard}
            >
              <View style={styles.riskCardHeader}>
                <View>
                  <Text style={styles.riskLabel}>Composite Risk</Text>
                  <Text style={[styles.riskScore, { color }]}>{score.toFixed(1)}<Text style={styles.riskMax}>/10</Text></Text>
                </View>
                <View style={[styles.riskBadge, { backgroundColor: color + '30', borderColor: color }]}>
                  <Text style={[styles.riskBadgeText, { color }]}>{level}</Text>
                </View>
              </View>

              <ScoreBar score={score} color={color} />

              <Text style={styles.riskDescription}>{risk.recommendation.description}</Text>

              {/* Component scores */}
              <View style={styles.scoreRow}>
                {(['geo', 'calendar', 'technical'] as const).map((key) => (
                  <View key={key} style={styles.scoreItem}>
                    <Text style={styles.scoreItemLabel}>{key.charAt(0).toUpperCase() + key.slice(1)}</Text>
                    <Text style={styles.scoreItemValue}>{(risk.scores[key] ?? 0).toFixed(1)}</Text>
                    <ScoreBar score={risk.scores[key] ?? 0} color={COLORS.primary} />
                  </View>
                ))}
              </View>

              {/* Alerts */}
              {risk.alerts.length > 0 && (
                <View style={styles.alertsBox}>
                  {risk.alerts.slice(0, 4).map((a, i) => (
                    <View key={i} style={styles.alertRow}>
                      <Ionicons name="alert-circle" size={14} color={COLORS.warning} />
                      <Text style={styles.alertText}>{typeof a === 'string' ? a : a.message}</Text>
                    </View>
                  ))}
                </View>
              )}
            </LinearGradient>
          ) : (
            <View style={styles.errorCard}>
              <Ionicons name="cloud-offline" size={32} color={COLORS.textMuted} />
              <Text style={styles.errorText}>Could not load risk report</Text>
            </View>
          )}

          {/* ── AI Briefing Card ── */}
          <View style={styles.sectionHeader}>
            <Ionicons name="sparkles" size={18} color={COLORS.primary} />
            <Text style={styles.sectionTitle}>AI Market Briefing</Text>
          </View>

          {loadingBriefing ? (
            <View style={styles.loadingCard}>
              <ActivityIndicator color={COLORS.primary} />
              <Text style={styles.loadingText}>Asking Claude…</Text>
            </View>
          ) : !analystOnline ? (
            <View style={styles.offlineCard}>
              <Ionicons name="server-outline" size={28} color={COLORS.textMuted} />
              <Text style={styles.offlineTitle}>Analyst Offline</Text>
              <Text style={styles.offlineText}>
                Deploy sentinel-analyst on Railway and set{'\n'}ANALYST_URL in config.ts
              </Text>
            </View>
          ) : briefing ? (
            <View style={styles.briefingCard}>
              <Text style={styles.briefingText}>{briefing.briefing}</Text>
              <Text style={styles.briefingMeta}>Powered by {briefing.model}</Text>
            </View>
          ) : null}

          {/* ── Ask the Analyst ── */}
          {analystOnline && (
            <>
              <View style={styles.sectionHeader}>
                <Ionicons name="chatbubble-ellipses" size={18} color={COLORS.accent ?? '#22D3EE'} />
                <Text style={styles.sectionTitle}>Ask the Analyst</Text>
              </View>

              <View style={styles.askCard}>
                <TextInput
                  style={styles.askInput}
                  placeholder="e.g. Should I long ETH right now?"
                  placeholderTextColor={COLORS.textMuted}
                  value={question}
                  onChangeText={setQuestion}
                  onSubmitEditing={handleAsk}
                  returnKeyType="send"
                  multiline={false}
                />
                <TouchableOpacity
                  style={[styles.askButton, asking && styles.askButtonDisabled]}
                  onPress={handleAsk}
                  disabled={asking}
                >
                  {asking
                    ? <ActivityIndicator size="small" color="#fff" />
                    : <Ionicons name="send" size={18} color="#fff" />
                  }
                </TouchableOpacity>
              </View>

              {answer !== '' && (
                <View style={styles.answerCard}>
                  <Text style={styles.answerText}>{answer}</Text>
                </View>
              )}
            </>
          )}

          <View style={{ height: SPACING.xxl }} />
        </ScrollView>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: COLORS.background },
  scroll: { padding: SPACING.lg },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    marginBottom: SPACING.lg,
  },
  title: { fontSize: FONT_SIZES.xxl, fontWeight: '700', color: COLORS.text },
  statusDot: { width: 10, height: 10, borderRadius: 5 },
  loadingCard: {
    backgroundColor: COLORS.surface,
    borderRadius: BORDER_RADIUS.lg,
    padding: SPACING.xl,
    alignItems: 'center',
    gap: SPACING.sm,
    marginBottom: SPACING.lg,
    borderWidth: 1,
    borderColor: COLORS.border,
  },
  loadingText: { fontSize: FONT_SIZES.sm, color: COLORS.textMuted },
  riskCard: {
    borderRadius: BORDER_RADIUS.xl,
    padding: SPACING.lg,
    marginBottom: SPACING.lg,
    borderWidth: 1,
    borderColor: COLORS.border,
  },
  riskCardHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'flex-start',
    marginBottom: SPACING.md,
  },
  riskLabel: { fontSize: FONT_SIZES.sm, color: COLORS.textMuted, marginBottom: 2 },
  riskScore: { fontSize: 40, fontWeight: '800', lineHeight: 46 },
  riskMax: { fontSize: FONT_SIZES.lg, fontWeight: '400', color: COLORS.textMuted },
  riskBadge: {
    paddingHorizontal: SPACING.md,
    paddingVertical: SPACING.sm,
    borderRadius: BORDER_RADIUS.full,
    borderWidth: 1,
  },
  riskBadgeText: { fontSize: FONT_SIZES.sm, fontWeight: '700' },
  scoreBarBg: {
    height: 6,
    backgroundColor: COLORS.border,
    borderRadius: 3,
    marginBottom: SPACING.md,
    overflow: 'hidden',
  },
  scoreBarFill: { height: '100%', borderRadius: 3 },
  riskDescription: {
    fontSize: FONT_SIZES.md,
    color: COLORS.textSecondary,
    lineHeight: 22,
    marginBottom: SPACING.md,
  },
  scoreRow: { flexDirection: 'row', gap: SPACING.sm, marginBottom: SPACING.md },
  scoreItem: { flex: 1 },
  scoreItemLabel: { fontSize: FONT_SIZES.xs, color: COLORS.textMuted, marginBottom: 2 },
  scoreItemValue: { fontSize: FONT_SIZES.lg, fontWeight: '700', color: COLORS.text, marginBottom: 4 },
  alertsBox: { gap: SPACING.xs },
  alertRow: { flexDirection: 'row', alignItems: 'flex-start', gap: SPACING.xs },
  alertText: { flex: 1, fontSize: FONT_SIZES.xs, color: COLORS.textSecondary, lineHeight: 18 },
  errorCard: {
    backgroundColor: COLORS.surface,
    borderRadius: BORDER_RADIUS.lg,
    padding: SPACING.xl,
    alignItems: 'center',
    gap: SPACING.sm,
    marginBottom: SPACING.lg,
    borderWidth: 1,
    borderColor: COLORS.border,
  },
  errorText: { fontSize: FONT_SIZES.sm, color: COLORS.textMuted },
  sectionHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: SPACING.sm,
    marginBottom: SPACING.md,
  },
  sectionTitle: { fontSize: FONT_SIZES.lg, fontWeight: '600', color: COLORS.text },
  offlineCard: {
    backgroundColor: COLORS.surface,
    borderRadius: BORDER_RADIUS.lg,
    padding: SPACING.xl,
    alignItems: 'center',
    gap: SPACING.sm,
    marginBottom: SPACING.lg,
    borderWidth: 1,
    borderColor: COLORS.border,
  },
  offlineTitle: { fontSize: FONT_SIZES.lg, fontWeight: '600', color: COLORS.textSecondary },
  offlineText: { fontSize: FONT_SIZES.sm, color: COLORS.textMuted, textAlign: 'center', lineHeight: 20 },
  briefingCard: {
    backgroundColor: COLORS.surface,
    borderRadius: BORDER_RADIUS.lg,
    padding: SPACING.lg,
    marginBottom: SPACING.lg,
    borderWidth: 1,
    borderColor: COLORS.primary + '40',
  },
  briefingText: { fontSize: FONT_SIZES.md, color: COLORS.text, lineHeight: 24 },
  briefingMeta: { fontSize: FONT_SIZES.xs, color: COLORS.textMuted, marginTop: SPACING.sm },
  askCard: {
    flexDirection: 'row',
    backgroundColor: COLORS.surface,
    borderRadius: BORDER_RADIUS.lg,
    borderWidth: 1,
    borderColor: COLORS.border,
    marginBottom: SPACING.md,
    overflow: 'hidden',
  },
  askInput: {
    flex: 1,
    padding: SPACING.md,
    fontSize: FONT_SIZES.md,
    color: COLORS.text,
  },
  askButton: {
    width: 52,
    justifyContent: 'center',
    alignItems: 'center',
    backgroundColor: COLORS.primary,
  },
  askButtonDisabled: { opacity: 0.5 },
  answerCard: {
    backgroundColor: COLORS.primary + '15',
    borderRadius: BORDER_RADIUS.lg,
    padding: SPACING.lg,
    marginBottom: SPACING.lg,
    borderWidth: 1,
    borderColor: COLORS.primary + '40',
  },
  answerText: { fontSize: FONT_SIZES.md, color: COLORS.text, lineHeight: 24 },
});
