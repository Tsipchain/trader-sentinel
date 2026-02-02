import React, { useState } from 'react';
import {
  View,
  Text,
  StyleSheet,
  ScrollView,
  TouchableOpacity,
  Modal,
  TextInput,
  ActivityIndicator,
  Alert,
} from 'react-native';
import { LinearGradient } from 'expo-linear-gradient';
import { Ionicons } from '@expo/vector-icons';
import { COLORS, SPACING, FONT_SIZES, BORDER_RADIUS } from '../constants/theme';
import { useStore } from '../store/useStore';
import { CONFIG } from '../config';
import { thronosService } from '../services/thronos';

interface Pool {
  id: string;
  tokenA: string;
  tokenB: string;
  tvl: number;
  apr: number;
  yourPosition: number;
  yourRewards: number;
  volume24h: number;
}

const pools: Pool[] = [
  {
    id: 'thronos-usdt',
    tokenA: 'THRONOS',
    tokenB: 'USDT',
    tvl: 2500000,
    apr: 12.5,
    yourPosition: 0,
    yourRewards: 0,
    volume24h: 450000,
  },
  {
    id: 'thronos-eth',
    tokenA: 'THRONOS',
    tokenB: 'ETH',
    tvl: 1800000,
    apr: 15.2,
    yourPosition: 0,
    yourRewards: 0,
    volume24h: 320000,
  },
  {
    id: 'thronos-bnb',
    tokenA: 'THRONOS',
    tokenB: 'BNB',
    tvl: 980000,
    apr: 18.7,
    yourPosition: 0,
    yourRewards: 0,
    volume24h: 180000,
  },
];

export default function LiquidityScreen() {
  const { wallet, subscription } = useStore();
  const [showAddModal, setShowAddModal] = useState(false);
  const [selectedPool, setSelectedPool] = useState<Pool | null>(null);
  const [amountA, setAmountA] = useState('');
  const [amountB, setAmountB] = useState('');
  const [processing, setProcessing] = useState(false);

  const totalTVL = pools.reduce((acc, p) => acc + p.tvl, 0);
  const yourTotalPosition = pools.reduce((acc, p) => acc + p.yourPosition, 0);
  const yourTotalRewards = pools.reduce((acc, p) => acc + p.yourRewards, 0);

  const handleAddLiquidity = async () => {
    if (!selectedPool || !amountA || !amountB) {
      Alert.alert('Error', 'Please enter both amounts');
      return;
    }

    setProcessing(true);
    try {
      const result = await thronosService.addLiquidity(
        '0x...', // tokenA address
        '0x...', // tokenB address
        amountA,
        amountB
      );

      if (result.success) {
        setShowAddModal(false);
        setAmountA('');
        setAmountB('');
        Alert.alert(
          'Success!',
          'Liquidity added successfully. You will start earning rewards immediately.',
        );
      } else {
        Alert.alert('Failed', result.error || 'Unknown error');
      }
    } catch (error: any) {
      Alert.alert('Error', error.message || 'Failed to add liquidity');
    } finally {
      setProcessing(false);
    }
  };

  const openAddModal = (pool: Pool) => {
    setSelectedPool(pool);
    setShowAddModal(true);
  };

  const formatNumber = (num: number) => {
    if (num >= 1000000) {
      return `$${(num / 1000000).toFixed(2)}M`;
    } else if (num >= 1000) {
      return `$${(num / 1000).toFixed(1)}K`;
    }
    return `$${num.toFixed(2)}`;
  };

  return (
    <View style={styles.container}>
      <ScrollView
        style={styles.scrollView}
        showsVerticalScrollIndicator={false}
      >
        {/* Overview Cards */}
        <View style={styles.overviewCards}>
          <LinearGradient
            colors={[COLORS.accent, COLORS.accentDark]}
            style={styles.overviewCard}
          >
            <Ionicons name="water" size={24} color={COLORS.background} />
            <Text style={styles.overviewLabel}>Total TVL</Text>
            <Text style={styles.overviewValue}>{formatNumber(totalTVL)}</Text>
          </LinearGradient>

          <LinearGradient
            colors={[COLORS.success, COLORS.successDark]}
            style={styles.overviewCard}
          >
            <Ionicons name="trending-up" size={24} color={COLORS.background} />
            <Text style={styles.overviewLabel}>Your Position</Text>
            <Text style={styles.overviewValue}>{formatNumber(yourTotalPosition)}</Text>
          </LinearGradient>
        </View>

        {/* Rewards Card */}
        <View style={styles.rewardsCard}>
          <View style={styles.rewardsHeader}>
            <View style={styles.rewardsIcon}>
              <Ionicons name="gift" size={24} color={COLORS.thronosGold} />
            </View>
            <View style={styles.rewardsInfo}>
              <Text style={styles.rewardsLabel}>Pending LP Rewards</Text>
              <Text style={styles.rewardsValue}>{yourTotalRewards.toFixed(4)} THRONOS</Text>
            </View>
            <TouchableOpacity
              style={styles.claimButton}
              disabled={yourTotalRewards <= 0}
            >
              <Text style={styles.claimButtonText}>Claim</Text>
            </TouchableOpacity>
          </View>

          <View style={styles.apyInfo}>
            <Ionicons name="information-circle" size={16} color={COLORS.textMuted} />
            <Text style={styles.apyInfoText}>
              Earn up to {CONFIG.REWARDS.LIQUIDITY_PROVISION_APY * 100}% APY by providing liquidity
            </Text>
          </View>
        </View>

        {/* How It Works */}
        <View style={styles.howItWorks}>
          <Text style={styles.sectionTitle}>How It Works</Text>
          <View style={styles.stepsList}>
            <View style={styles.step}>
              <View style={styles.stepNumber}>
                <Text style={styles.stepNumberText}>1</Text>
              </View>
              <View style={styles.stepContent}>
                <Text style={styles.stepTitle}>Add Liquidity</Text>
                <Text style={styles.stepDesc}>Deposit token pairs to liquidity pools</Text>
              </View>
            </View>
            <View style={styles.step}>
              <View style={styles.stepNumber}>
                <Text style={styles.stepNumberText}>2</Text>
              </View>
              <View style={styles.stepContent}>
                <Text style={styles.stepTitle}>Earn Rewards</Text>
                <Text style={styles.stepDesc}>Get THRONOS tokens as LP rewards</Text>
              </View>
            </View>
            <View style={styles.step}>
              <View style={styles.stepNumber}>
                <Text style={styles.stepNumberText}>3</Text>
              </View>
              <View style={styles.stepContent}>
                <Text style={styles.stepTitle}>Compound or Claim</Text>
                <Text style={styles.stepDesc}>Reinvest or withdraw anytime</Text>
              </View>
            </View>
          </View>
        </View>

        {/* Available Pools */}
        <View style={styles.section}>
          <View style={styles.sectionHeader}>
            <Text style={styles.sectionTitle}>Available Pools</Text>
            <TouchableOpacity>
              <Text style={styles.seeAll}>Sort by APR</Text>
            </TouchableOpacity>
          </View>

          {pools.map((pool) => (
            <View key={pool.id} style={styles.poolCard}>
              <View style={styles.poolHeader}>
                <View style={styles.poolTokens}>
                  <View style={styles.tokenPair}>
                    <View style={[styles.tokenBadge, { backgroundColor: COLORS.thronosGold + '20' }]}>
                      <Text style={styles.tokenBadgeText}>{pool.tokenA[0]}</Text>
                    </View>
                    <View style={[styles.tokenBadge, styles.tokenBadgeOverlap, { backgroundColor: COLORS.accent + '20' }]}>
                      <Text style={styles.tokenBadgeText}>{pool.tokenB[0]}</Text>
                    </View>
                  </View>
                  <View>
                    <Text style={styles.poolName}>{pool.tokenA}/{pool.tokenB}</Text>
                    <Text style={styles.poolVolume}>24h Vol: {formatNumber(pool.volume24h)}</Text>
                  </View>
                </View>
                <View style={styles.poolApr}>
                  <Text style={styles.poolAprValue}>{pool.apr.toFixed(1)}%</Text>
                  <Text style={styles.poolAprLabel}>APR</Text>
                </View>
              </View>

              <View style={styles.poolStats}>
                <View style={styles.poolStat}>
                  <Text style={styles.poolStatLabel}>TVL</Text>
                  <Text style={styles.poolStatValue}>{formatNumber(pool.tvl)}</Text>
                </View>
                <View style={styles.poolStat}>
                  <Text style={styles.poolStatLabel}>Your Position</Text>
                  <Text style={styles.poolStatValue}>{formatNumber(pool.yourPosition)}</Text>
                </View>
                <View style={styles.poolStat}>
                  <Text style={styles.poolStatLabel}>Rewards</Text>
                  <Text style={[styles.poolStatValue, { color: COLORS.thronosGold }]}>
                    {pool.yourRewards.toFixed(4)}
                  </Text>
                </View>
              </View>

              <View style={styles.poolActions}>
                <TouchableOpacity
                  style={styles.poolActionButton}
                  onPress={() => openAddModal(pool)}
                >
                  <Ionicons name="add" size={18} color={COLORS.primary} />
                  <Text style={styles.poolActionText}>Add</Text>
                </TouchableOpacity>

                <TouchableOpacity
                  style={[styles.poolActionButton, styles.poolActionSecondary]}
                  disabled={pool.yourPosition <= 0}
                >
                  <Ionicons name="remove" size={18} color={COLORS.textMuted} />
                  <Text style={[styles.poolActionText, { color: COLORS.textMuted }]}>Remove</Text>
                </TouchableOpacity>
              </View>
            </View>
          ))}
        </View>

        {/* Benefits */}
        <View style={styles.benefitsCard}>
          <Text style={styles.benefitsTitle}>LP Benefits</Text>
          <View style={styles.benefitItem}>
            <Ionicons name="checkmark-circle" size={20} color={COLORS.success} />
            <Text style={styles.benefitText}>No impermanent loss protection on THRONOS pairs</Text>
          </View>
          <View style={styles.benefitItem}>
            <Ionicons name="checkmark-circle" size={20} color={COLORS.success} />
            <Text style={styles.benefitText}>Boosted rewards for subscription holders</Text>
          </View>
          <View style={styles.benefitItem}>
            <Ionicons name="checkmark-circle" size={20} color={COLORS.success} />
            <Text style={styles.benefitText}>Compound rewards automatically</Text>
          </View>
          <View style={styles.benefitItem}>
            <Ionicons name="checkmark-circle" size={20} color={COLORS.success} />
            <Text style={styles.benefitText}>Withdraw anytime, no lock-up</Text>
          </View>
        </View>

        <View style={styles.bottomSpacing} />
      </ScrollView>

      {/* Add Liquidity Modal */}
      <Modal
        visible={showAddModal}
        animationType="slide"
        transparent={true}
        onRequestClose={() => setShowAddModal(false)}
      >
        <View style={styles.modalOverlay}>
          <View style={styles.modalContent}>
            <View style={styles.modalHeader}>
              <Text style={styles.modalTitle}>
                Add Liquidity to {selectedPool?.tokenA}/{selectedPool?.tokenB}
              </Text>
              <TouchableOpacity
                onPress={() => setShowAddModal(false)}
                disabled={processing}
              >
                <Ionicons name="close" size={24} color={COLORS.text} />
              </TouchableOpacity>
            </View>

            <View style={styles.inputGroup}>
              <Text style={styles.inputLabel}>{selectedPool?.tokenA} Amount</Text>
              <View style={styles.inputWrapper}>
                <TextInput
                  style={styles.input}
                  value={amountA}
                  onChangeText={setAmountA}
                  placeholder="0.00"
                  placeholderTextColor={COLORS.textMuted}
                  keyboardType="decimal-pad"
                />
                <TouchableOpacity style={styles.maxButton}>
                  <Text style={styles.maxButtonText}>MAX</Text>
                </TouchableOpacity>
              </View>
            </View>

            <View style={styles.plusIcon}>
              <Ionicons name="add" size={24} color={COLORS.textMuted} />
            </View>

            <View style={styles.inputGroup}>
              <Text style={styles.inputLabel}>{selectedPool?.tokenB} Amount</Text>
              <View style={styles.inputWrapper}>
                <TextInput
                  style={styles.input}
                  value={amountB}
                  onChangeText={setAmountB}
                  placeholder="0.00"
                  placeholderTextColor={COLORS.textMuted}
                  keyboardType="decimal-pad"
                />
                <TouchableOpacity style={styles.maxButton}>
                  <Text style={styles.maxButtonText}>MAX</Text>
                </TouchableOpacity>
              </View>
            </View>

            <View style={styles.estimatedRewards}>
              <Text style={styles.estimatedLabel}>Estimated Annual Rewards</Text>
              <Text style={styles.estimatedValue}>
                {selectedPool ? (parseFloat(amountA || '0') * (selectedPool.apr / 100)).toFixed(2) : '0'} THRONOS
              </Text>
            </View>

            <TouchableOpacity
              style={styles.addButton}
              onPress={handleAddLiquidity}
              disabled={processing || !amountA || !amountB}
            >
              <LinearGradient
                colors={[COLORS.accent, COLORS.accentDark]}
                style={styles.addButtonGradient}
              >
                {processing ? (
                  <ActivityIndicator color={COLORS.text} />
                ) : (
                  <>
                    <Ionicons name="water" size={20} color={COLORS.text} />
                    <Text style={styles.addButtonText}>Add Liquidity</Text>
                  </>
                )}
              </LinearGradient>
            </TouchableOpacity>

            <Text style={styles.disclaimer}>
              By adding liquidity, you agree to the pool terms and conditions.
            </Text>
          </View>
        </View>
      </Modal>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: COLORS.background,
  },
  scrollView: {
    flex: 1,
    paddingHorizontal: SPACING.lg,
  },
  overviewCards: {
    flexDirection: 'row',
    gap: SPACING.md,
    marginTop: SPACING.lg,
    marginBottom: SPACING.lg,
  },
  overviewCard: {
    flex: 1,
    borderRadius: BORDER_RADIUS.lg,
    padding: SPACING.md,
  },
  overviewLabel: {
    fontSize: FONT_SIZES.sm,
    color: 'rgba(0,0,0,0.6)',
    marginTop: SPACING.sm,
  },
  overviewValue: {
    fontSize: FONT_SIZES.xxl,
    fontWeight: '700',
    color: COLORS.background,
  },
  rewardsCard: {
    backgroundColor: COLORS.surface,
    borderRadius: BORDER_RADIUS.lg,
    padding: SPACING.md,
    marginBottom: SPACING.lg,
    borderWidth: 1,
    borderColor: COLORS.thronosGold + '30',
  },
  rewardsHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    marginBottom: SPACING.md,
  },
  rewardsIcon: {
    width: 44,
    height: 44,
    borderRadius: BORDER_RADIUS.md,
    backgroundColor: COLORS.thronosGold + '20',
    justifyContent: 'center',
    alignItems: 'center',
    marginRight: SPACING.md,
  },
  rewardsInfo: {
    flex: 1,
  },
  rewardsLabel: {
    fontSize: FONT_SIZES.sm,
    color: COLORS.textSecondary,
  },
  rewardsValue: {
    fontSize: FONT_SIZES.lg,
    fontWeight: '700',
    color: COLORS.thronosGold,
  },
  claimButton: {
    backgroundColor: COLORS.thronosGold,
    paddingHorizontal: SPACING.md,
    paddingVertical: SPACING.sm,
    borderRadius: BORDER_RADIUS.md,
  },
  claimButtonText: {
    fontSize: FONT_SIZES.sm,
    fontWeight: '600',
    color: COLORS.background,
  },
  apyInfo: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: COLORS.backgroundCard,
    padding: SPACING.sm,
    borderRadius: BORDER_RADIUS.md,
  },
  apyInfoText: {
    fontSize: FONT_SIZES.sm,
    color: COLORS.textMuted,
    marginLeft: SPACING.xs,
  },
  howItWorks: {
    marginBottom: SPACING.lg,
  },
  sectionTitle: {
    fontSize: FONT_SIZES.lg,
    fontWeight: '600',
    color: COLORS.text,
    marginBottom: SPACING.md,
  },
  stepsList: {},
  step: {
    flexDirection: 'row',
    alignItems: 'center',
    marginBottom: SPACING.md,
  },
  stepNumber: {
    width: 32,
    height: 32,
    borderRadius: 16,
    backgroundColor: COLORS.primary,
    justifyContent: 'center',
    alignItems: 'center',
    marginRight: SPACING.md,
  },
  stepNumberText: {
    fontSize: FONT_SIZES.md,
    fontWeight: '700',
    color: COLORS.text,
  },
  stepContent: {},
  stepTitle: {
    fontSize: FONT_SIZES.md,
    fontWeight: '600',
    color: COLORS.text,
  },
  stepDesc: {
    fontSize: FONT_SIZES.sm,
    color: COLORS.textSecondary,
  },
  section: {
    marginBottom: SPACING.lg,
  },
  sectionHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: SPACING.md,
  },
  seeAll: {
    fontSize: FONT_SIZES.sm,
    color: COLORS.primary,
    fontWeight: '500',
  },
  poolCard: {
    backgroundColor: COLORS.surface,
    borderRadius: BORDER_RADIUS.lg,
    padding: SPACING.md,
    marginBottom: SPACING.md,
    borderWidth: 1,
    borderColor: COLORS.border,
  },
  poolHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: SPACING.md,
  },
  poolTokens: {
    flexDirection: 'row',
    alignItems: 'center',
  },
  tokenPair: {
    flexDirection: 'row',
    marginRight: SPACING.md,
  },
  tokenBadge: {
    width: 36,
    height: 36,
    borderRadius: 18,
    justifyContent: 'center',
    alignItems: 'center',
    borderWidth: 2,
    borderColor: COLORS.surface,
  },
  tokenBadgeOverlap: {
    marginLeft: -12,
  },
  tokenBadgeText: {
    fontSize: FONT_SIZES.sm,
    fontWeight: '700',
    color: COLORS.text,
  },
  poolName: {
    fontSize: FONT_SIZES.md,
    fontWeight: '600',
    color: COLORS.text,
  },
  poolVolume: {
    fontSize: FONT_SIZES.xs,
    color: COLORS.textMuted,
  },
  poolApr: {
    alignItems: 'flex-end',
  },
  poolAprValue: {
    fontSize: FONT_SIZES.xl,
    fontWeight: '700',
    color: COLORS.success,
  },
  poolAprLabel: {
    fontSize: FONT_SIZES.xs,
    color: COLORS.textMuted,
  },
  poolStats: {
    flexDirection: 'row',
    borderTopWidth: 1,
    borderTopColor: COLORS.border,
    paddingTop: SPACING.md,
    marginBottom: SPACING.md,
  },
  poolStat: {
    flex: 1,
  },
  poolStatLabel: {
    fontSize: FONT_SIZES.xs,
    color: COLORS.textMuted,
  },
  poolStatValue: {
    fontSize: FONT_SIZES.md,
    fontWeight: '600',
    color: COLORS.text,
  },
  poolActions: {
    flexDirection: 'row',
    gap: SPACING.sm,
  },
  poolActionButton: {
    flex: 1,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: COLORS.primary + '15',
    padding: SPACING.sm,
    borderRadius: BORDER_RADIUS.md,
    borderWidth: 1,
    borderColor: COLORS.primary + '30',
  },
  poolActionSecondary: {
    backgroundColor: COLORS.backgroundCard,
    borderColor: COLORS.border,
  },
  poolActionText: {
    fontSize: FONT_SIZES.sm,
    fontWeight: '600',
    color: COLORS.primary,
    marginLeft: SPACING.xs,
  },
  benefitsCard: {
    backgroundColor: COLORS.success + '15',
    borderRadius: BORDER_RADIUS.lg,
    padding: SPACING.lg,
    borderWidth: 1,
    borderColor: COLORS.success + '30',
  },
  benefitsTitle: {
    fontSize: FONT_SIZES.lg,
    fontWeight: '600',
    color: COLORS.text,
    marginBottom: SPACING.md,
  },
  benefitItem: {
    flexDirection: 'row',
    alignItems: 'center',
    marginBottom: SPACING.sm,
  },
  benefitText: {
    fontSize: FONT_SIZES.sm,
    color: COLORS.textSecondary,
    marginLeft: SPACING.sm,
    flex: 1,
  },
  bottomSpacing: {
    height: SPACING.xxl,
  },
  // Modal Styles
  modalOverlay: {
    flex: 1,
    backgroundColor: COLORS.overlay,
    justifyContent: 'flex-end',
  },
  modalContent: {
    backgroundColor: COLORS.backgroundCard,
    borderTopLeftRadius: BORDER_RADIUS.xxl,
    borderTopRightRadius: BORDER_RADIUS.xxl,
    padding: SPACING.lg,
  },
  modalHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: SPACING.lg,
  },
  modalTitle: {
    fontSize: FONT_SIZES.lg,
    fontWeight: '700',
    color: COLORS.text,
    flex: 1,
  },
  inputGroup: {
    marginBottom: SPACING.md,
  },
  inputLabel: {
    fontSize: FONT_SIZES.sm,
    fontWeight: '600',
    color: COLORS.textSecondary,
    marginBottom: SPACING.sm,
  },
  inputWrapper: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: COLORS.surface,
    borderRadius: BORDER_RADIUS.md,
    borderWidth: 1,
    borderColor: COLORS.border,
  },
  input: {
    flex: 1,
    padding: SPACING.md,
    fontSize: FONT_SIZES.lg,
    color: COLORS.text,
  },
  maxButton: {
    backgroundColor: COLORS.primary,
    paddingHorizontal: SPACING.md,
    paddingVertical: SPACING.sm,
    borderRadius: BORDER_RADIUS.sm,
    marginRight: SPACING.sm,
  },
  maxButtonText: {
    fontSize: FONT_SIZES.xs,
    fontWeight: '700',
    color: COLORS.text,
  },
  plusIcon: {
    alignItems: 'center',
    marginVertical: SPACING.sm,
  },
  estimatedRewards: {
    backgroundColor: COLORS.success + '15',
    padding: SPACING.md,
    borderRadius: BORDER_RADIUS.md,
    marginBottom: SPACING.lg,
    alignItems: 'center',
  },
  estimatedLabel: {
    fontSize: FONT_SIZES.sm,
    color: COLORS.textSecondary,
  },
  estimatedValue: {
    fontSize: FONT_SIZES.xl,
    fontWeight: '700',
    color: COLORS.success,
  },
  addButton: {
    borderRadius: BORDER_RADIUS.lg,
    overflow: 'hidden',
    marginBottom: SPACING.md,
  },
  addButtonGradient: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    padding: SPACING.md + 4,
  },
  addButtonText: {
    fontSize: FONT_SIZES.lg,
    fontWeight: '600',
    color: COLORS.text,
    marginLeft: SPACING.sm,
  },
  disclaimer: {
    fontSize: FONT_SIZES.xs,
    color: COLORS.textMuted,
    textAlign: 'center',
  },
});
