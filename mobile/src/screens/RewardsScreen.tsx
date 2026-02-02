import React, { useState } from 'react';
import {
  View,
  Text,
  StyleSheet,
  ScrollView,
  TouchableOpacity,
  ActivityIndicator,
  Alert,
} from 'react-native';
import { LinearGradient } from 'expo-linear-gradient';
import { Ionicons } from '@expo/vector-icons';
import { COLORS, SPACING, FONT_SIZES, BORDER_RADIUS } from '../constants/theme';
import { useStore } from '../store/useStore';
import { CONFIG } from '../config';
import { thronosService } from '../services/thronos';

export default function RewardsScreen() {
  const { rewards, claimRewards, wallet, subscription } = useStore();
  const [claiming, setClaiming] = useState(false);

  const handleClaimRewards = async () => {
    if (rewards.pending <= 0) {
      Alert.alert('No Rewards', 'You have no pending rewards to claim.');
      return;
    }

    setClaiming(true);
    try {
      const result = await thronosService.claimRewards();
      if (result.success) {
        claimRewards();
        Alert.alert(
          'Success!',
          `You have claimed ${rewards.pending.toFixed(2)} THRONOS tokens!`,
        );
      } else {
        Alert.alert('Claim Failed', result.error || 'Unknown error');
      }
    } catch (error: any) {
      Alert.alert('Error', error.message || 'Failed to claim rewards');
    } finally {
      setClaiming(false);
    }
  };

  const getMultiplier = () => {
    const pkg = Object.values(CONFIG.PACKAGES).find((p) => p.id === subscription);
    return pkg?.rewardsMultiplier || 1.0;
  };

  const rewardTypes = [
    {
      type: 'Signal Usage',
      amount: CONFIG.REWARDS.TRADE_SIGNAL_USAGE,
      icon: 'flash',
      color: COLORS.primary,
      description: 'Earned per signal used',
    },
    {
      type: 'Daily Login',
      amount: CONFIG.REWARDS.DAILY_LOGIN_BONUS,
      icon: 'calendar',
      color: COLORS.success,
      description: 'Daily bonus for active users',
    },
    {
      type: 'Referral',
      amount: CONFIG.REWARDS.REFERRAL_BONUS,
      icon: 'people',
      color: COLORS.accent,
      description: 'Per referred friend',
    },
    {
      type: 'Staking APY',
      amount: CONFIG.REWARDS.STAKING_APY * 100,
      icon: 'lock-closed',
      color: COLORS.thronosPurple,
      description: 'Annual percentage yield',
      isPercent: true,
    },
    {
      type: 'Liquidity APY',
      amount: CONFIG.REWARDS.LIQUIDITY_PROVISION_APY * 100,
      icon: 'water',
      color: COLORS.info,
      description: 'For liquidity providers',
      isPercent: true,
    },
  ];

  return (
    <ScrollView
      style={styles.container}
      showsVerticalScrollIndicator={false}
    >
      {/* Main Rewards Card */}
      <LinearGradient
        colors={[COLORS.thronosGold, '#B8860B']}
        style={styles.mainCard}
        start={{ x: 0, y: 0 }}
        end={{ x: 1, y: 1 }}
      >
        <View style={styles.mainCardHeader}>
          <Ionicons name="star" size={32} color={COLORS.background} />
          <Text style={styles.multiplierBadge}>
            {getMultiplier()}x Multiplier
          </Text>
        </View>

        <Text style={styles.totalRewardsLabel}>Total Rewards Earned</Text>
        <Text style={styles.totalRewardsValue}>
          {rewards.total.toFixed(2)} THRONOS
        </Text>

        <View style={styles.rewardsBreakdown}>
          <View style={styles.breakdownItem}>
            <Text style={styles.breakdownLabel}>Pending</Text>
            <Text style={styles.breakdownValue}>{rewards.pending.toFixed(2)}</Text>
          </View>
          <View style={styles.breakdownDivider} />
          <View style={styles.breakdownItem}>
            <Text style={styles.breakdownLabel}>Claimed</Text>
            <Text style={styles.breakdownValue}>{rewards.claimed.toFixed(2)}</Text>
          </View>
        </View>

        <TouchableOpacity
          style={styles.claimButton}
          onPress={handleClaimRewards}
          disabled={claiming || rewards.pending <= 0}
        >
          {claiming ? (
            <ActivityIndicator color={COLORS.thronosGold} />
          ) : (
            <>
              <Ionicons name="download" size={20} color={COLORS.thronosGold} />
              <Text style={styles.claimButtonText}>
                {rewards.pending > 0
                  ? `Claim ${rewards.pending.toFixed(2)} THRONOS`
                  : 'No rewards to claim'
                }
              </Text>
            </>
          )}
        </TouchableOpacity>
      </LinearGradient>

      {/* Staking Card */}
      <View style={styles.stakingCard}>
        <View style={styles.stakingHeader}>
          <View style={[styles.stakingIcon, { backgroundColor: COLORS.thronosPurple + '20' }]}>
            <Ionicons name="lock-closed" size={24} color={COLORS.thronosPurple} />
          </View>
          <View style={styles.stakingInfo}>
            <Text style={styles.stakingTitle}>Stake THRONOS</Text>
            <Text style={styles.stakingApy}>Earn {CONFIG.REWARDS.STAKING_APY * 100}% APY</Text>
          </View>
        </View>

        <View style={styles.stakingStats}>
          <View style={styles.stakingStat}>
            <Text style={styles.stakingStatLabel}>Your Stake</Text>
            <Text style={styles.stakingStatValue}>0 THRONOS</Text>
          </View>
          <View style={styles.stakingStat}>
            <Text style={styles.stakingStatLabel}>Rewards</Text>
            <Text style={styles.stakingStatValue}>0 THRONOS</Text>
          </View>
        </View>

        <TouchableOpacity style={styles.stakeButton}>
          <Text style={styles.stakeButtonText}>Stake Now</Text>
        </TouchableOpacity>
      </View>

      {/* Reward Types */}
      <View style={styles.section}>
        <Text style={styles.sectionTitle}>Ways to Earn</Text>

        {rewardTypes.map((reward, index) => (
          <View key={index} style={styles.rewardTypeCard}>
            <View style={[styles.rewardTypeIcon, { backgroundColor: reward.color + '20' }]}>
              <Ionicons name={reward.icon as any} size={24} color={reward.color} />
            </View>
            <View style={styles.rewardTypeInfo}>
              <Text style={styles.rewardTypeName}>{reward.type}</Text>
              <Text style={styles.rewardTypeDesc}>{reward.description}</Text>
            </View>
            <View style={styles.rewardTypeAmount}>
              <Text style={[styles.rewardTypeValue, { color: reward.color }]}>
                {reward.isPercent ? `${reward.amount}%` : `+${reward.amount}`}
              </Text>
              {!reward.isPercent && (
                <Text style={styles.rewardTypeUnit}>THRONOS</Text>
              )}
            </View>
          </View>
        ))}
      </View>

      {/* History */}
      <View style={styles.section}>
        <Text style={styles.sectionTitle}>Rewards History</Text>

        {rewards.history.length > 0 ? (
          rewards.history.slice(0, 10).map((item, index) => (
            <View key={index} style={styles.historyItem}>
              <View style={styles.historyIcon}>
                <Ionicons name="add-circle" size={20} color={COLORS.success} />
              </View>
              <View style={styles.historyInfo}>
                <Text style={styles.historyType}>{item.type}</Text>
                <Text style={styles.historyDate}>
                  {new Date(item.date).toLocaleDateString()}
                </Text>
              </View>
              <Text style={styles.historyAmount}>+{item.amount} THRONOS</Text>
            </View>
          ))
        ) : (
          <View style={styles.emptyHistory}>
            <Ionicons name="time-outline" size={48} color={COLORS.textMuted} />
            <Text style={styles.emptyHistoryText}>No rewards history yet</Text>
            <Text style={styles.emptyHistorySubtext}>
              Start using signals to earn rewards
            </Text>
          </View>
        )}
      </View>

      {/* Tier Benefits */}
      <View style={styles.tierCard}>
        <Text style={styles.tierTitle}>Upgrade for More Rewards</Text>
        <Text style={styles.tierDesc}>
          Higher subscription tiers earn up to 5x more rewards on all activities
        </Text>

        <View style={styles.tierList}>
          {Object.values(CONFIG.PACKAGES).map((pkg) => (
            <View key={pkg.id} style={styles.tierItem}>
              <Text style={styles.tierName}>{pkg.name}</Text>
              <Text style={styles.tierMultiplier}>{pkg.rewardsMultiplier}x</Text>
            </View>
          ))}
        </View>
      </View>

      <View style={styles.bottomSpacing} />
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: COLORS.background,
    paddingHorizontal: SPACING.lg,
  },
  mainCard: {
    borderRadius: BORDER_RADIUS.xl,
    padding: SPACING.lg,
    marginTop: SPACING.lg,
    marginBottom: SPACING.lg,
  },
  mainCardHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: SPACING.lg,
  },
  multiplierBadge: {
    backgroundColor: 'rgba(0,0,0,0.2)',
    paddingHorizontal: SPACING.md,
    paddingVertical: SPACING.xs,
    borderRadius: BORDER_RADIUS.full,
    fontSize: FONT_SIZES.sm,
    fontWeight: '600',
    color: COLORS.background,
  },
  totalRewardsLabel: {
    fontSize: FONT_SIZES.md,
    color: 'rgba(0,0,0,0.6)',
  },
  totalRewardsValue: {
    fontSize: FONT_SIZES.display,
    fontWeight: '700',
    color: COLORS.background,
    marginBottom: SPACING.lg,
  },
  rewardsBreakdown: {
    flexDirection: 'row',
    backgroundColor: 'rgba(0,0,0,0.15)',
    borderRadius: BORDER_RADIUS.lg,
    padding: SPACING.md,
    marginBottom: SPACING.lg,
  },
  breakdownItem: {
    flex: 1,
    alignItems: 'center',
  },
  breakdownDivider: {
    width: 1,
    backgroundColor: 'rgba(0,0,0,0.2)',
  },
  breakdownLabel: {
    fontSize: FONT_SIZES.sm,
    color: 'rgba(0,0,0,0.6)',
  },
  breakdownValue: {
    fontSize: FONT_SIZES.xl,
    fontWeight: '700',
    color: COLORS.background,
  },
  claimButton: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: COLORS.background,
    padding: SPACING.md,
    borderRadius: BORDER_RADIUS.lg,
  },
  claimButtonText: {
    fontSize: FONT_SIZES.md,
    fontWeight: '600',
    color: COLORS.thronosGold,
    marginLeft: SPACING.sm,
  },
  stakingCard: {
    backgroundColor: COLORS.surface,
    borderRadius: BORDER_RADIUS.xl,
    padding: SPACING.lg,
    marginBottom: SPACING.lg,
    borderWidth: 1,
    borderColor: COLORS.thronosPurple + '30',
  },
  stakingHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    marginBottom: SPACING.lg,
  },
  stakingIcon: {
    width: 48,
    height: 48,
    borderRadius: BORDER_RADIUS.md,
    justifyContent: 'center',
    alignItems: 'center',
    marginRight: SPACING.md,
  },
  stakingInfo: {},
  stakingTitle: {
    fontSize: FONT_SIZES.lg,
    fontWeight: '600',
    color: COLORS.text,
  },
  stakingApy: {
    fontSize: FONT_SIZES.sm,
    color: COLORS.thronosPurple,
    fontWeight: '500',
  },
  stakingStats: {
    flexDirection: 'row',
    marginBottom: SPACING.lg,
  },
  stakingStat: {
    flex: 1,
  },
  stakingStatLabel: {
    fontSize: FONT_SIZES.sm,
    color: COLORS.textSecondary,
  },
  stakingStatValue: {
    fontSize: FONT_SIZES.lg,
    fontWeight: '600',
    color: COLORS.text,
  },
  stakeButton: {
    backgroundColor: COLORS.thronosPurple,
    padding: SPACING.md,
    borderRadius: BORDER_RADIUS.md,
    alignItems: 'center',
  },
  stakeButtonText: {
    fontSize: FONT_SIZES.md,
    fontWeight: '600',
    color: COLORS.text,
  },
  section: {
    marginBottom: SPACING.lg,
  },
  sectionTitle: {
    fontSize: FONT_SIZES.lg,
    fontWeight: '600',
    color: COLORS.text,
    marginBottom: SPACING.md,
  },
  rewardTypeCard: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: COLORS.surface,
    borderRadius: BORDER_RADIUS.lg,
    padding: SPACING.md,
    marginBottom: SPACING.sm,
    borderWidth: 1,
    borderColor: COLORS.border,
  },
  rewardTypeIcon: {
    width: 44,
    height: 44,
    borderRadius: BORDER_RADIUS.md,
    justifyContent: 'center',
    alignItems: 'center',
    marginRight: SPACING.md,
  },
  rewardTypeInfo: {
    flex: 1,
  },
  rewardTypeName: {
    fontSize: FONT_SIZES.md,
    fontWeight: '600',
    color: COLORS.text,
  },
  rewardTypeDesc: {
    fontSize: FONT_SIZES.sm,
    color: COLORS.textSecondary,
  },
  rewardTypeAmount: {
    alignItems: 'flex-end',
  },
  rewardTypeValue: {
    fontSize: FONT_SIZES.lg,
    fontWeight: '700',
  },
  rewardTypeUnit: {
    fontSize: FONT_SIZES.xs,
    color: COLORS.textMuted,
  },
  historyItem: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: COLORS.surface,
    borderRadius: BORDER_RADIUS.lg,
    padding: SPACING.md,
    marginBottom: SPACING.sm,
  },
  historyIcon: {
    marginRight: SPACING.md,
  },
  historyInfo: {
    flex: 1,
  },
  historyType: {
    fontSize: FONT_SIZES.md,
    fontWeight: '500',
    color: COLORS.text,
  },
  historyDate: {
    fontSize: FONT_SIZES.sm,
    color: COLORS.textMuted,
  },
  historyAmount: {
    fontSize: FONT_SIZES.md,
    fontWeight: '600',
    color: COLORS.success,
  },
  emptyHistory: {
    alignItems: 'center',
    padding: SPACING.xl,
    backgroundColor: COLORS.surface,
    borderRadius: BORDER_RADIUS.lg,
  },
  emptyHistoryText: {
    fontSize: FONT_SIZES.md,
    fontWeight: '600',
    color: COLORS.text,
    marginTop: SPACING.md,
  },
  emptyHistorySubtext: {
    fontSize: FONT_SIZES.sm,
    color: COLORS.textMuted,
    marginTop: SPACING.xs,
  },
  tierCard: {
    backgroundColor: COLORS.primary + '15',
    borderRadius: BORDER_RADIUS.lg,
    padding: SPACING.lg,
    borderWidth: 1,
    borderColor: COLORS.primary + '30',
  },
  tierTitle: {
    fontSize: FONT_SIZES.lg,
    fontWeight: '600',
    color: COLORS.text,
    marginBottom: SPACING.xs,
  },
  tierDesc: {
    fontSize: FONT_SIZES.sm,
    color: COLORS.textSecondary,
    marginBottom: SPACING.lg,
  },
  tierList: {
    flexDirection: 'row',
    justifyContent: 'space-between',
  },
  tierItem: {
    alignItems: 'center',
  },
  tierName: {
    fontSize: FONT_SIZES.sm,
    color: COLORS.textSecondary,
    marginBottom: 2,
  },
  tierMultiplier: {
    fontSize: FONT_SIZES.lg,
    fontWeight: '700',
    color: COLORS.primary,
  },
  bottomSpacing: {
    height: SPACING.xxl,
  },
});
