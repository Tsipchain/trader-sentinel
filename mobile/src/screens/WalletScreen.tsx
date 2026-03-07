import React, { useState, useEffect, useCallback } from 'react';
import {
  View,
  Text,
  StyleSheet,
  ScrollView,
  TouchableOpacity,
  Share,
  RefreshControl,
  ActivityIndicator,
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
import {
  fetchETHBalance,
  fetchTokenPrices,
  fetchThronosBalances,
  isValidThronosAddress,
} from '../services/walletConnect';

type NavigationProp = NativeStackNavigationProp<RootStackParamList>;

interface TokenDisplay {
  symbol: string;
  name: string;
  balance: number;
  value: number;
  change: number;
  icon: keyof typeof Ionicons.glyphMap;
  color: string;
}

export default function WalletScreen() {
  const navigation = useNavigation<NavigationProp>();
  const { wallet, user, rewards, subscription, setWallet } = useStore();
  const [copied, setCopied] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [tokens, setTokens] = useState<TokenDisplay[]>([]);
  const [loading, setLoading] = useState(true);
  const [prices, setPrices] = useState<Record<string, number>>({});
  const [chainPickerOpen, setChainPickerOpen] = useState(false);

  const isThronosWallet = wallet.walletType === 'thronos' || (wallet.address?.startsWith('THR') ?? false);

  // Available chains for current wallet type
  const availableChainKeys: string[] = isThronosWallet
    ? (CONFIG.WALLET_CHAINS.thronos || ['THRONOS'])
    : wallet.walletType === 'phantom'
      ? (CONFIG.WALLET_CHAINS.phantom || ['SOLANA'])
      : (CONFIG.WALLET_CHAINS.evm || ['ETHEREUM']);

  const currentChainKey = wallet.selectedChainKey || (isThronosWallet ? 'THRONOS' : 'ETHEREUM');
  const currentChain = CONFIG.SUPPORTED_CHAINS[currentChainKey as keyof typeof CONFIG.SUPPORTED_CHAINS];

  const switchChain = async (chainKey: string) => {
    const chain = CONFIG.SUPPORTED_CHAINS[chainKey as keyof typeof CONFIG.SUPPORTED_CHAINS];
    if (!chain) return;
    setChainPickerOpen(false);
    setLoading(true);
    setWallet({ selectedChainKey: chainKey, chainId: chain.chainId });
  };

  const loadBalances = useCallback(async () => {
    if (!wallet.address) return;

    try {
      const priceData = await fetchTokenPrices();
      setPrices(priceData);

      const tokenList: TokenDisplay[] = [];

      if (isThronosWallet && currentChainKey === 'THRONOS') {
        // Fetch Thronos chain balances
        const data = await fetchThronosBalances(wallet.address);
        if (data.tokens && data.tokens.length > 0) {
          for (const t of data.tokens) {
            if (t.balance > 0 || t.symbol === 'THR') {
              tokenList.push({
                symbol: t.symbol,
                name: t.name || t.symbol,
                balance: t.balance,
                value: t.symbol === 'THR' ? t.balance * 0.85 : t.balance,
                change: 0,
                icon: t.symbol === 'THR' ? 'planet' : 'cube',
                color: t.symbol === 'THR' ? COLORS.thronosGold : COLORS.accent,
              });
            }
          }
        }

        // Always show THR if not in list
        if (!tokenList.find((t) => t.symbol === 'THR')) {
          tokenList.unshift({
            symbol: 'THR',
            name: 'Thronos',
            balance: user?.thronosBalance || 0,
            value: (user?.thronosBalance || 0) * 0.85,
            change: 0,
            icon: 'planet',
            color: COLORS.thronosGold,
          });
        }
      } else if (currentChainKey === 'BTC') {
        // BTC balance — bridged via Thronos Chain
        tokenList.push({
          symbol: 'BTC',
          name: 'Bitcoin (Bridge)',
          balance: 0,
          value: 0,
          change: 0,
          icon: 'logo-bitcoin',
          color: '#F7931A',
        });
      } else if (currentChainKey === 'XRP') {
        // XRP Ledger balance
        tokenList.push({
          symbol: 'XRP',
          name: 'XRP Ledger',
          balance: 0,
          value: 0,
          change: 0,
          icon: 'pulse',
          color: '#23292F',
        });
      } else {
        // EVM wallet - fetch native balance for selected chain
        const nativeChainId = typeof currentChain?.chainId === 'number' ? currentChain.chainId : 1;
        const nativeSymbol = currentChain?.symbol || 'ETH';
        const nativeBalance = await fetchETHBalance(wallet.address, nativeChainId);
        const nativePrice = priceData[nativeSymbol] || priceData.ETH || 0;

        // Update stored balance
        setWallet({ balance: nativeBalance });

        tokenList.push({
          symbol: nativeSymbol,
          name: currentChain?.name || 'Unknown',
          balance: parseFloat(nativeBalance) || 0,
          value: (parseFloat(nativeBalance) || 0) * nativePrice,
          change: 0,
          icon: nativeSymbol === 'BNB' ? 'cube' : nativeSymbol === 'AVAX' ? 'snow' : 'diamond-outline',
          color: nativeSymbol === 'BNB' ? '#F0B90B' : nativeSymbol === 'MATIC' ? '#8247E5' : nativeSymbol === 'AVAX' ? '#E84142' : '#627EEA',
        });

        // Show THR rewards balance
        if (user?.thronosBalance && user.thronosBalance > 0) {
          tokenList.push({
            symbol: 'THR',
            name: 'Thronos Token',
            balance: user.thronosBalance,
            value: user.thronosBalance * 0.85,
            change: 0,
            icon: 'star',
            color: COLORS.thronosGold,
          });
        }
      }

      setTokens(tokenList);
    } catch (error) {
      console.warn('Failed to load balances:', error);
      const sym = currentChain?.symbol || 'ETH';
      setTokens([
        {
          symbol: isThronosWallet ? 'THR' : sym,
          name: isThronosWallet ? 'Thronos' : (currentChain?.name || 'Unknown'),
          balance: parseFloat(wallet.balance) || 0,
          value: 0,
          change: 0,
          icon: isThronosWallet ? 'planet' : 'diamond-outline',
          color: isThronosWallet ? COLORS.thronosGold : '#627EEA',
        },
      ]);
    } finally {
      setLoading(false);
    }
  }, [wallet.address, wallet.chainId, isThronosWallet, currentChainKey, user?.thronosBalance]);

  useEffect(() => {
    loadBalances();
  }, [loadBalances]);

  const onRefresh = useCallback(async () => {
    setRefreshing(true);
    await loadBalances();
    setRefreshing(false);
  }, [loadBalances]);

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
        message: `Join Trader Sentinel and earn THR rewards! Use my referral code: ${user.referralCode}\n\nDownload now: https://tradersentinel.app/ref/${user.referralCode}`,
      });
    }
  };

  const shortenAddress = (address: string) => {
    if (address.startsWith('THR')) {
      return `${address.slice(0, 10)}...${address.slice(-6)}`;
    }
    return `${address.slice(0, 10)}...${address.slice(-8)}`;
  };

  const getSubscriptionColor = () => {
    switch (subscription) {
      case 'whale': return COLORS.thronosGold;
      case 'elite': return COLORS.thronosPurple;
      case 'pro': return COLORS.primary;
      default: return COLORS.textMuted;
    }
  };

  const getNetworkName = () => {
    return currentChain?.name || 'Unknown';
  };

  const totalValue = tokens.reduce((acc, t) => acc + t.value, 0);

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      <ScrollView
        style={styles.scrollView}
        showsVerticalScrollIndicator={false}
        refreshControl={
          <RefreshControl
            refreshing={refreshing}
            onRefresh={onRefresh}
            tintColor={COLORS.primary}
          />
        }
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
          colors={isThronosWallet ? [COLORS.thronosGold, '#DAA520'] : [COLORS.primary, COLORS.primaryDark]}
          style={styles.walletCard}
          start={{ x: 0, y: 0 }}
          end={{ x: 1, y: 1 }}
        >
          <View style={styles.walletCardHeader}>
            <TouchableOpacity
              style={styles.networkBadge}
              onPress={() => availableChainKeys.length > 1 && setChainPickerOpen(!chainPickerOpen)}
              activeOpacity={availableChainKeys.length > 1 ? 0.6 : 1}
            >
              <View style={[styles.networkDot, isThronosWallet && { backgroundColor: COLORS.thronosGold }]} />
              <Text style={styles.networkText}>{getNetworkName()}</Text>
              {availableChainKeys.length > 1 && (
                <Ionicons name={chainPickerOpen ? 'chevron-up' : 'chevron-down'} size={14} color={COLORS.text} style={{ marginLeft: 4 }} />
              )}
            </TouchableOpacity>
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
            <Text style={[styles.walletAddress, isThronosWallet && { color: COLORS.background }]}>
              {wallet.address ? shortenAddress(wallet.address) : 'Not connected'}
            </Text>
            <Ionicons
              name={copied ? 'checkmark' : 'copy-outline'}
              size={18}
              color={isThronosWallet ? COLORS.background : COLORS.text}
            />
          </TouchableOpacity>

          <View style={styles.totalBalance}>
            <Text style={[styles.totalBalanceLabel, isThronosWallet && { color: 'rgba(0,0,0,0.6)' }]}>
              Total Balance
            </Text>
            {loading ? (
              <ActivityIndicator color={isThronosWallet ? COLORS.background : COLORS.text} />
            ) : (
              <Text style={[styles.totalBalanceValue, isThronosWallet && { color: COLORS.background }]}>
                ${totalValue.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
              </Text>
            )}
          </View>
        </LinearGradient>

        {/* Chain Picker Dropdown */}
        {chainPickerOpen && availableChainKeys.length > 1 && (
          <View style={styles.chainPicker}>
            {availableChainKeys.map((key) => {
              const chain = CONFIG.SUPPORTED_CHAINS[key as keyof typeof CONFIG.SUPPORTED_CHAINS];
              if (!chain) return null;
              const isActive = key === currentChainKey;
              return (
                <TouchableOpacity
                  key={key}
                  style={[styles.chainPickerItem, isActive && styles.chainPickerItemActive]}
                  onPress={() => switchChain(key)}
                >
                  <View style={[styles.chainPickerDot, isActive && styles.chainPickerDotActive]} />
                  <Text style={[styles.chainPickerText, isActive && styles.chainPickerTextActive]}>
                    {chain.name}
                  </Text>
                  <Text style={styles.chainPickerSymbol}>{chain.symbol}</Text>
                  {isActive && <Ionicons name="checkmark" size={18} color={COLORS.primary} />}
                </TouchableOpacity>
              );
            })}
          </View>
        )}

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
                <Text style={styles.rewardsValue}>{rewards.pending.toFixed(2)} THR</Text>
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

          {loading ? (
            <View style={styles.loadingContainer}>
              <ActivityIndicator size="small" color={COLORS.primary} />
              <Text style={styles.loadingText}>Fetching balances...</Text>
            </View>
          ) : tokens.length === 0 ? (
            <View style={styles.emptyActivity}>
              <Ionicons name="wallet-outline" size={48} color={COLORS.textMuted} />
              <Text style={styles.emptyActivityText}>No tokens found</Text>
            </View>
          ) : (
            tokens.map((token, index) => (
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
                </View>
              </View>
            ))
          )}
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
                Earn {CONFIG.REWARDS.REFERRAL_BONUS} THR for each friend
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
  chainPicker: {
    backgroundColor: COLORS.surface,
    borderRadius: BORDER_RADIUS.lg,
    marginBottom: SPACING.lg,
    borderWidth: 1,
    borderColor: COLORS.border,
    overflow: 'hidden',
  },
  chainPickerItem: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: SPACING.md,
    paddingVertical: SPACING.md,
    borderBottomWidth: 1,
    borderBottomColor: COLORS.border,
  },
  chainPickerItemActive: {
    backgroundColor: COLORS.primary + '10',
  },
  chainPickerDot: {
    width: 10,
    height: 10,
    borderRadius: 5,
    backgroundColor: COLORS.textMuted,
    marginRight: SPACING.sm,
  },
  chainPickerDotActive: {
    backgroundColor: COLORS.success,
  },
  chainPickerText: {
    flex: 1,
    fontSize: FONT_SIZES.md,
    color: COLORS.text,
    fontWeight: '500',
  },
  chainPickerTextActive: {
    color: COLORS.primary,
    fontWeight: '600',
  },
  chainPickerSymbol: {
    fontSize: FONT_SIZES.sm,
    color: COLORS.textMuted,
    marginRight: SPACING.sm,
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
  loadingContainer: {
    alignItems: 'center',
    padding: SPACING.xl,
    backgroundColor: COLORS.surface,
    borderRadius: BORDER_RADIUS.lg,
    gap: SPACING.sm,
  },
  loadingText: {
    fontSize: FONT_SIZES.sm,
    color: COLORS.textMuted,
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
