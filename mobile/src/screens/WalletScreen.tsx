import React, { useState } from 'react';
import {
  View,
  Text,
  StyleSheet,
  ScrollView,
  TouchableOpacity,
  Share,
} from 'react-native';
import { LinearGradient } from 'expo-linear-gradient';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useNavigation } from '@react-navigation/native';
import { NativeStackNavigationProp } from '@react-navigation/native-stack';
import { Ionicons } from '@expo/vector-icons';
import * as Clipboard from 'expo-clipboard';
import { RootStackParamList } from '../../App';
import { COLORS, SPACING, FONT_SIZES, BORDER_RADIUS } from '../constants/theme';
import { useStore } from '../store/useStore';
import { CONFIG } from '../config';

type NavigationProp = NativeStackNavigationProp<RootStackParamList>;

export default function WalletScreen() {
  const navigation = useNavigation<NavigationProp>();
  const { wallet, user, rewards, subscription } = useStore();
  const [copied, setCopied] = useState(false);

  const copyAddress = async () => {
    if (wallet.address) {
      await Clipboard.setStringAsync(wallet.address);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  };

  const shareReferralLink = async () => {
    if (user?.referralCode) {
      await Share.share({
        message: `Join Trader Sentinel and earn THRONOS rewards! Use my referral code: ${user.referralCode}\n\nDownload now: https://tradersentinel.app/ref/${user.referralCode}`,
      });
    }
  };

  const shortenAddress = (address: string) => {
    return `${address.slice(0, 10)}...${address.slice(-8)}`;
  };

  const getSubscriptionColor = () => {
    switch (subscription) {
      case 'whale':
        return COLORS.thronosGold;
      case 'elite':
        return COLORS.thronosPurple;
      case 'pro':
        return COLORS.primary;
      default:
        return COLORS.textMuted;
    }
  };

  const tokens = [
    {
      symbol: 'THRONOS',
      name: 'Thronos Token',
      balance: user?.thronosBalance || 0,
      value: (user?.thronosBalance || 0) * 0.85,
      change: 12.5,
      icon: 'star',
      color: COLORS.thronosGold,
    },
    {
      symbol: 'ETH',
      name: 'Ethereum',
      balance: parseFloat(wallet.balance) || 0,
      value: (parseFloat(wallet.balance) || 0) * 2450,
      change: -2.3,
      icon: 'logo-ethereum',
      color: '#627EEA',
    },
  ];

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      <ScrollView
        style={styles.scrollView}
        showsVerticalScrollIndicator={false}
      >
        {/* Header */}
        <View style={styles.header}>
          <Text style={styles.title}>Wallet</Text>
          <TouchableOpacity style={styles.qrButton}>
            <Ionicons name="qr-code" size={24} color={COLORS.text} />
          </TouchableOpacity>
        </View>

        {/* Wallet Card */}
        <LinearGradient
          colors={[COLORS.primary, COLORS.primaryDark]}
          style={styles.walletCard}
          start={{ x: 0, y: 0 }}
          end={{ x: 1, y: 1 }}
        >
          <View style={styles.walletCardHeader}>
            <View style={styles.networkBadge}>
              <View style={styles.networkDot} />
              <Text style={styles.networkText}>
                {CONFIG.SUPPORTED_CHAINS.ETHEREUM.name}
              </Text>
            </View>
            <View
              style={[
                styles.subscriptionBadge,
                { backgroundColor: getSubscriptionColor() + '30', borderColor: getSubscriptionColor() },
              ]}
            >
              <Text style={[styles.subscriptionText, { color: getSubscriptionColor() }]}>
                {subscription.toUpperCase()}
              </Text>
            </View>
          </View>

          <Text style={styles.walletLabel}>Connected Wallet</Text>
          <TouchableOpacity onPress={copyAddress} style={styles.addressRow}>
            <Text style={styles.walletAddress}>
              {wallet.address ? shortenAddress(wallet.address) : 'Not connected'}
            </Text>
            <Ionicons
              name={copied ? 'checkmark' : 'copy-outline'}
              size={18}
              color={COLORS.text}
            />
          </TouchableOpacity>

          <View style={styles.totalBalance}>
            <Text style={styles.totalBalanceLabel}>Total Balance</Text>
            <Text style={styles.totalBalanceValue}>
              ${tokens.reduce((acc, t) => acc + t.value, 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
            </Text>
          </View>
        </LinearGradient>

        {/* Quick Actions */}
        <View style={styles.quickActions}>
          <TouchableOpacity style={styles.quickAction}>
            <View style={styles.quickActionIcon}>
              <Ionicons name="arrow-down" size={24} color={COLORS.success} />
            </View>
            <Text style={styles.quickActionText}>Receive</Text>
          </TouchableOpacity>

          <TouchableOpacity style={styles.quickAction}>
            <View style={styles.quickActionIcon}>
              <Ionicons name="arrow-up" size={24} color={COLORS.primary} />
            </View>
            <Text style={styles.quickActionText}>Send</Text>
          </TouchableOpacity>

          <TouchableOpacity style={styles.quickAction}>
            <View style={styles.quickActionIcon}>
              <Ionicons name="swap-horizontal" size={24} color={COLORS.accent} />
            </View>
            <Text style={styles.quickActionText}>Swap</Text>
          </TouchableOpacity>

          <TouchableOpacity
            style={styles.quickAction}
            onPress={() => navigation.navigate('Subscription')}
          >
            <View style={styles.quickActionIcon}>
              <Ionicons name="card" size={24} color={COLORS.thronosGold} />
            </View>
            <Text style={styles.quickActionText}>Buy</Text>
          </TouchableOpacity>
        </View>

        {/* Rewards Card */}
        <TouchableOpacity
          style={styles.rewardsCard}
          onPress={() => navigation.navigate('Rewards')}
        >
          <LinearGradient
            colors={[COLORS.thronosGold + '20', COLORS.backgroundCard]}
            style={styles.rewardsGradient}
          >
            <View style={styles.rewardsHeader}>
              <View style={styles.rewardsIcon}>
                <Ionicons name="gift" size={24} color={COLORS.thronosGold} />
              </View>
              <View style={styles.rewardsInfo}>
                <Text style={styles.rewardsLabel}>Thronos Rewards</Text>
                <Text style={styles.rewardsValue}>{rewards.pending.toFixed(2)} THRONOS</Text>
              </View>
              <Ionicons name="chevron-forward" size={24} color={COLORS.textMuted} />
            </View>
            <View style={styles.rewardsProgress}>
              <View style={styles.progressBar}>
                <View
                  style={[
                    styles.progressFill,
                    { width: `${Math.min((rewards.pending / 100) * 100, 100)}%` },
                  ]}
                />
              </View>
              <Text style={styles.progressText}>
                {rewards.pending.toFixed(0)}/100 to next tier
              </Text>
            </View>
          </LinearGradient>
        </TouchableOpacity>

        {/* Token List */}
        <View style={styles.section}>
          <Text style={styles.sectionTitle}>Assets</Text>

          {tokens.map((token, index) => (
            <View key={index} style={styles.tokenItem}>
              <View style={[styles.tokenIcon, { backgroundColor: token.color + '20' }]}>
                <Ionicons name={token.icon as any} size={24} color={token.color} />
              </View>
              <View style={styles.tokenInfo}>
                <Text style={styles.tokenName}>{token.name}</Text>
                <Text style={styles.tokenBalance}>
                  {token.balance.toFixed(4)} {token.symbol}
                </Text>
              </View>
              <View style={styles.tokenValue}>
                <Text style={styles.tokenValueUsd}>
                  ${token.value.toFixed(2)}
                </Text>
                <Text
                  style={[
                    styles.tokenChange,
                    { color: token.change >= 0 ? COLORS.success : COLORS.error },
                  ]}
                >
                  {token.change >= 0 ? '+' : ''}{token.change.toFixed(1)}%
                </Text>
              </View>
            </View>
          ))}
        </View>

        {/* Referral Card */}
        <TouchableOpacity style={styles.referralCard} onPress={shareReferralLink}>
          <View style={styles.referralContent}>
            <View style={styles.referralIcon}>
              <Ionicons name="people" size={28} color={COLORS.success} />
            </View>
            <View style={styles.referralInfo}>
              <Text style={styles.referralTitle}>Refer & Earn</Text>
              <Text style={styles.referralDesc}>
                Earn {CONFIG.REWARDS.REFERRAL_BONUS} THRONOS for each friend
              </Text>
              <View style={styles.referralCode}>
                <Text style={styles.referralCodeText}>
                  Code: {user?.referralCode || 'N/A'}
                </Text>
              </View>
            </View>
          </View>
          <Ionicons name="share-outline" size={24} color={COLORS.success} />
        </TouchableOpacity>

        {/* Activity */}
        <View style={styles.section}>
          <View style={styles.sectionHeader}>
            <Text style={styles.sectionTitle}>Recent Activity</Text>
            <TouchableOpacity>
              <Text style={styles.seeAll}>See All</Text>
            </TouchableOpacity>
          </View>

          <View style={styles.emptyActivity}>
            <Ionicons name="time-outline" size={48} color={COLORS.textMuted} />
            <Text style={styles.emptyActivityText}>No recent transactions</Text>
          </View>
        </View>

        <View style={styles.bottomSpacing} />
      </ScrollView>
    </SafeAreaView>
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
  header: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    paddingVertical: SPACING.md,
  },
  title: {
    fontSize: FONT_SIZES.xxl,
    fontWeight: '700',
    color: COLORS.text,
  },
  qrButton: {
    width: 44,
    height: 44,
    borderRadius: BORDER_RADIUS.md,
    backgroundColor: COLORS.surface,
    justifyContent: 'center',
    alignItems: 'center',
  },
  walletCard: {
    borderRadius: BORDER_RADIUS.xl,
    padding: SPACING.lg,
    marginBottom: SPACING.lg,
  },
  walletCardHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    marginBottom: SPACING.lg,
  },
  networkBadge: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: 'rgba(255,255,255,0.15)',
    paddingHorizontal: SPACING.sm,
    paddingVertical: SPACING.xs,
    borderRadius: BORDER_RADIUS.full,
  },
  networkDot: {
    width: 8,
    height: 8,
    borderRadius: 4,
    backgroundColor: COLORS.success,
    marginRight: SPACING.xs,
  },
  networkText: {
    fontSize: FONT_SIZES.xs,
    color: COLORS.text,
    fontWeight: '500',
  },
  subscriptionBadge: {
    paddingHorizontal: SPACING.sm,
    paddingVertical: SPACING.xs,
    borderRadius: BORDER_RADIUS.full,
    borderWidth: 1,
  },
  subscriptionText: {
    fontSize: FONT_SIZES.xs,
    fontWeight: '600',
  },
  walletLabel: {
    fontSize: FONT_SIZES.sm,
    color: 'rgba(255,255,255,0.7)',
  },
  addressRow: {
    flexDirection: 'row',
    alignItems: 'center',
    marginBottom: SPACING.lg,
  },
  walletAddress: {
    fontSize: FONT_SIZES.md,
    color: COLORS.text,
    fontWeight: '500',
    marginRight: SPACING.sm,
  },
  totalBalance: {
    marginTop: SPACING.md,
  },
  totalBalanceLabel: {
    fontSize: FONT_SIZES.sm,
    color: 'rgba(255,255,255,0.7)',
  },
  totalBalanceValue: {
    fontSize: FONT_SIZES.xxxl,
    fontWeight: '700',
    color: COLORS.text,
  },
  quickActions: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    marginBottom: SPACING.lg,
  },
  quickAction: {
    alignItems: 'center',
  },
  quickActionIcon: {
    width: 56,
    height: 56,
    borderRadius: BORDER_RADIUS.lg,
    backgroundColor: COLORS.surface,
    justifyContent: 'center',
    alignItems: 'center',
    marginBottom: SPACING.sm,
    borderWidth: 1,
    borderColor: COLORS.border,
  },
  quickActionText: {
    fontSize: FONT_SIZES.sm,
    color: COLORS.textSecondary,
    fontWeight: '500',
  },
  rewardsCard: {
    marginBottom: SPACING.lg,
    borderRadius: BORDER_RADIUS.lg,
    overflow: 'hidden',
  },
  rewardsGradient: {
    padding: SPACING.md,
    borderRadius: BORDER_RADIUS.lg,
    borderWidth: 1,
    borderColor: COLORS.thronosGold + '30',
  },
  rewardsHeader: {
    flexDirection: 'row',
    alignItems: 'center',
  },
  rewardsIcon: {
    width: 48,
    height: 48,
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
    fontSize: FONT_SIZES.xl,
    fontWeight: '700',
    color: COLORS.thronosGold,
  },
  rewardsProgress: {
    marginTop: SPACING.md,
  },
  progressBar: {
    height: 6,
    backgroundColor: COLORS.backgroundCard,
    borderRadius: BORDER_RADIUS.full,
    overflow: 'hidden',
  },
  progressFill: {
    height: '100%',
    backgroundColor: COLORS.thronosGold,
    borderRadius: BORDER_RADIUS.full,
  },
  progressText: {
    fontSize: FONT_SIZES.xs,
    color: COLORS.textMuted,
    marginTop: SPACING.xs,
    textAlign: 'right',
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
  sectionTitle: {
    fontSize: FONT_SIZES.lg,
    fontWeight: '600',
    color: COLORS.text,
    marginBottom: SPACING.md,
  },
  seeAll: {
    fontSize: FONT_SIZES.md,
    color: COLORS.primary,
    fontWeight: '500',
  },
  tokenItem: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: COLORS.surface,
    borderRadius: BORDER_RADIUS.lg,
    padding: SPACING.md,
    marginBottom: SPACING.sm,
    borderWidth: 1,
    borderColor: COLORS.border,
  },
  tokenIcon: {
    width: 48,
    height: 48,
    borderRadius: BORDER_RADIUS.md,
    justifyContent: 'center',
    alignItems: 'center',
    marginRight: SPACING.md,
  },
  tokenInfo: {
    flex: 1,
  },
  tokenName: {
    fontSize: FONT_SIZES.md,
    fontWeight: '600',
    color: COLORS.text,
  },
  tokenBalance: {
    fontSize: FONT_SIZES.sm,
    color: COLORS.textSecondary,
  },
  tokenValue: {
    alignItems: 'flex-end',
  },
  tokenValueUsd: {
    fontSize: FONT_SIZES.md,
    fontWeight: '600',
    color: COLORS.text,
  },
  tokenChange: {
    fontSize: FONT_SIZES.sm,
    fontWeight: '500',
  },
  referralCard: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: COLORS.success + '15',
    borderRadius: BORDER_RADIUS.lg,
    padding: SPACING.md,
    marginBottom: SPACING.lg,
    borderWidth: 1,
    borderColor: COLORS.success + '30',
  },
  referralContent: {
    flex: 1,
    flexDirection: 'row',
    alignItems: 'center',
  },
  referralIcon: {
    width: 56,
    height: 56,
    borderRadius: BORDER_RADIUS.md,
    backgroundColor: COLORS.success + '20',
    justifyContent: 'center',
    alignItems: 'center',
    marginRight: SPACING.md,
  },
  referralInfo: {
    flex: 1,
  },
  referralTitle: {
    fontSize: FONT_SIZES.lg,
    fontWeight: '600',
    color: COLORS.text,
  },
  referralDesc: {
    fontSize: FONT_SIZES.sm,
    color: COLORS.textSecondary,
  },
  referralCode: {
    marginTop: SPACING.xs,
  },
  referralCodeText: {
    fontSize: FONT_SIZES.xs,
    color: COLORS.success,
    fontWeight: '600',
  },
  emptyActivity: {
    alignItems: 'center',
    padding: SPACING.xl,
    backgroundColor: COLORS.surface,
    borderRadius: BORDER_RADIUS.lg,
  },
  emptyActivityText: {
    fontSize: FONT_SIZES.md,
    color: COLORS.textMuted,
    marginTop: SPACING.sm,
  },
  bottomSpacing: {
    height: SPACING.xxl,
  },
});
