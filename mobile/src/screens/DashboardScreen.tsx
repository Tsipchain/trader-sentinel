import React, { useEffect, useState, useCallback } from 'react';
import {
  View,
  Text,
  StyleSheet,
  ScrollView,
  TouchableOpacity,
  RefreshControl,
} from 'react-native';
import { LinearGradient } from 'expo-linear-gradient';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useNavigation } from '@react-navigation/native';
import { NativeStackNavigationProp } from '@react-navigation/native-stack';
import { Ionicons } from '@expo/vector-icons';
import { RootStackParamList } from '../../App';
import { COLORS, SPACING, FONT_SIZES, BORDER_RADIUS, SHADOWS } from '../constants/theme';
import { useStore } from '../store/useStore';
import { marketAPI, ArbitrageData } from '../services/api';
import { CONFIG } from '../config';

type NavigationProp = NativeStackNavigationProp<RootStackParamList>;

interface PriceData {
  symbol: string;
  price: number;
  change24h: number;
  spread: number;
  bestBid: string;
  bestAsk: string;
}

export default function DashboardScreen() {
  const navigation = useNavigation<NavigationProp>();
  const { wallet, subscription, watchlist, rewards, signals } = useStore();
  const [refreshing, setRefreshing] = useState(false);
  const [prices, setPrices] = useState<PriceData[]>([]);
  const [arbitrageOpps, setArbitrageOpps] = useState<ArbitrageData[]>([]);
  const [loading, setLoading] = useState(true);

  const fetchData = useCallback(async () => {
    try {
      const results = await Promise.all(
        watchlist.map(async (symbol) => {
          const arb = await marketAPI.getArbitrage(symbol);
          return {
            symbol,
            price: arb.best_bid,
            change24h: Math.random() * 10 - 5, // Mock 24h change
            spread: arb.spread,
            bestBid: arb.best_bid_venue,
            bestAsk: arb.best_ask_venue,
            ...arb,
          };
        })
      );
      setPrices(results);

      // Filter for good arbitrage opportunities
      const opps = results.filter((r) => (r.spread / r.price) * 100 > 0.1);
      setArbitrageOpps(opps as any);
    } catch (error) {
      console.error('Failed to fetch data:', error);
    } finally {
      setLoading(false);
    }
  }, [watchlist]);

  useEffect(() => {
    fetchData();
    const interval = setInterval(fetchData, 5000);
    return () => clearInterval(interval);
  }, [fetchData]);

  const onRefresh = useCallback(async () => {
    setRefreshing(true);
    await fetchData();
    setRefreshing(false);
  }, [fetchData]);

  const shortenAddress = (address: string) => {
    return `${address.slice(0, 6)}...${address.slice(-4)}`;
  };

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      <ScrollView
        style={styles.scrollView}
        refreshControl={
          <RefreshControl
            refreshing={refreshing}
            onRefresh={onRefresh}
            tintColor={COLORS.primary}
          />
        }
        showsVerticalScrollIndicator={false}
      >
        {/* Header */}
        <View style={styles.header}>
          <View>
            <Text style={styles.greeting}>Welcome back</Text>
            <Text style={styles.address}>
              {wallet.address ? shortenAddress(wallet.address) : 'Not connected'}
            </Text>
          </View>
          <TouchableOpacity
            style={styles.notificationButton}
            onPress={() => {}}
          >
            <Ionicons name="notifications-outline" size={24} color={COLORS.text} />
            {signals.length > 0 && <View style={styles.notificationBadge} />}
          </TouchableOpacity>
        </View>

        {/* Subscription Banner */}
        {subscription === 'free' && (
          <TouchableOpacity
            onPress={() => navigation.navigate('Subscription')}
          >
            <LinearGradient
              colors={[COLORS.primary, COLORS.primaryDark]}
              style={styles.upgradeBanner}
              start={{ x: 0, y: 0 }}
              end={{ x: 1, y: 0 }}
            >
              <View style={styles.upgradeContent}>
                <Ionicons name="rocket" size={24} color={COLORS.text} />
                <View style={styles.upgradeText}>
                  <Text style={styles.upgradeTitle}>Upgrade to Pro</Text>
                  <Text style={styles.upgradeDesc}>Unlock advanced signals & rewards</Text>
                </View>
              </View>
              <Ionicons name="chevron-forward" size={24} color={COLORS.text} />
            </LinearGradient>
          </TouchableOpacity>
        )}

        {/* Stats Cards */}
        <View style={styles.statsRow}>
          <TouchableOpacity
            style={styles.statCard}
            onPress={() => navigation.navigate('Rewards')}
          >
            <LinearGradient
              colors={[COLORS.thronosGold + '30', COLORS.backgroundCard]}
              style={styles.statGradient}
            >
              <View style={styles.statIcon}>
                <Ionicons name="star" size={20} color={COLORS.thronosGold} />
              </View>
              <Text style={styles.statValue}>{rewards.total.toFixed(2)}</Text>
              <Text style={styles.statLabel}>THRONOS Rewards</Text>
            </LinearGradient>
          </TouchableOpacity>

          <TouchableOpacity
            style={styles.statCard}
            onPress={() => navigation.navigate('Liquidity')}
          >
            <LinearGradient
              colors={[COLORS.accent + '30', COLORS.backgroundCard]}
              style={styles.statGradient}
            >
              <View style={styles.statIcon}>
                <Ionicons name="water" size={20} color={COLORS.accent} />
              </View>
              <Text style={styles.statValue}>12.5%</Text>
              <Text style={styles.statLabel}>Pool APY</Text>
            </LinearGradient>
          </TouchableOpacity>
        </View>

        {/* Arbitrage Opportunities */}
        <View style={styles.section}>
          <View style={styles.sectionHeader}>
            <Text style={styles.sectionTitle}>
              <Ionicons name="flash" size={18} color={COLORS.primary} /> Arbitrage Opportunities
            </Text>
            <TouchableOpacity>
              <Text style={styles.seeAll}>See All</Text>
            </TouchableOpacity>
          </View>

          {arbitrageOpps.length > 0 ? (
            arbitrageOpps.slice(0, 3).map((opp, index) => (
              <View key={index} style={styles.arbCard}>
                <View style={styles.arbHeader}>
                  <Text style={styles.arbSymbol}>{(opp as any).symbol}</Text>
                  <View style={styles.arbProfit}>
                    <Ionicons name="trending-up" size={16} color={COLORS.success} />
                    <Text style={styles.arbProfitText}>
                      {((opp.spread / opp.best_ask) * 100).toFixed(3)}%
                    </Text>
                  </View>
                </View>
                <View style={styles.arbDetails}>
                  <View style={styles.arbVenue}>
                    <Text style={styles.arbVenueLabel}>Buy on</Text>
                    <Text style={styles.arbVenueName}>{opp.best_ask_venue}</Text>
                    <Text style={styles.arbPrice}>${opp.best_ask.toFixed(2)}</Text>
                  </View>
                  <Ionicons name="arrow-forward" size={20} color={COLORS.textMuted} />
                  <View style={styles.arbVenue}>
                    <Text style={styles.arbVenueLabel}>Sell on</Text>
                    <Text style={styles.arbVenueName}>{opp.best_bid_venue}</Text>
                    <Text style={styles.arbPrice}>${opp.best_bid.toFixed(2)}</Text>
                  </View>
                </View>
              </View>
            ))
          ) : (
            <View style={styles.noData}>
              <Ionicons name="search" size={32} color={COLORS.textMuted} />
              <Text style={styles.noDataText}>Scanning for opportunities...</Text>
            </View>
          )}
        </View>

        {/* Watchlist */}
        <View style={styles.section}>
          <View style={styles.sectionHeader}>
            <Text style={styles.sectionTitle}>
              <Ionicons name="eye" size={18} color={COLORS.accent} /> Watchlist
            </Text>
            <TouchableOpacity>
              <Ionicons name="add-circle-outline" size={24} color={COLORS.primary} />
            </TouchableOpacity>
          </View>

          {prices.map((item, index) => (
            <View key={index} style={styles.watchlistItem}>
              <View style={styles.watchlistLeft}>
                <Text style={styles.watchlistSymbol}>{item.symbol}</Text>
                <Text style={styles.watchlistExchange}>
                  Best: {item.bestBid} / {item.bestAsk}
                </Text>
              </View>
              <View style={styles.watchlistRight}>
                <Text style={styles.watchlistPrice}>${item.price.toLocaleString()}</Text>
                <Text
                  style={[
                    styles.watchlistChange,
                    { color: item.change24h >= 0 ? COLORS.success : COLORS.error },
                  ]}
                >
                  {item.change24h >= 0 ? '+' : ''}{item.change24h.toFixed(2)}%
                </Text>
              </View>
            </View>
          ))}
        </View>

        {/* Quick Actions */}
        <View style={styles.quickActions}>
          <TouchableOpacity
            style={styles.quickAction}
            onPress={() => navigation.navigate('Subscription')}
          >
            <View style={[styles.quickActionIcon, { backgroundColor: COLORS.primary + '20' }]}>
              <Ionicons name="diamond" size={24} color={COLORS.primary} />
            </View>
            <Text style={styles.quickActionText}>Plans</Text>
          </TouchableOpacity>

          <TouchableOpacity
            style={styles.quickAction}
            onPress={() => navigation.navigate('Rewards')}
          >
            <View style={[styles.quickActionIcon, { backgroundColor: COLORS.thronosGold + '20' }]}>
              <Ionicons name="gift" size={24} color={COLORS.thronosGold} />
            </View>
            <Text style={styles.quickActionText}>Rewards</Text>
          </TouchableOpacity>

          <TouchableOpacity
            style={styles.quickAction}
            onPress={() => navigation.navigate('Liquidity')}
          >
            <View style={[styles.quickActionIcon, { backgroundColor: COLORS.accent + '20' }]}>
              <Ionicons name="water" size={24} color={COLORS.accent} />
            </View>
            <Text style={styles.quickActionText}>Liquidity</Text>
          </TouchableOpacity>

          <TouchableOpacity style={styles.quickAction}>
            <View style={[styles.quickActionIcon, { backgroundColor: COLORS.success + '20' }]}>
              <Ionicons name="people" size={24} color={COLORS.success} />
            </View>
            <Text style={styles.quickActionText}>Refer</Text>
          </TouchableOpacity>
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
  greeting: {
    fontSize: FONT_SIZES.md,
    color: COLORS.textSecondary,
  },
  address: {
    fontSize: FONT_SIZES.xl,
    fontWeight: '700',
    color: COLORS.text,
  },
  notificationButton: {
    width: 44,
    height: 44,
    borderRadius: BORDER_RADIUS.md,
    backgroundColor: COLORS.surface,
    justifyContent: 'center',
    alignItems: 'center',
  },
  notificationBadge: {
    position: 'absolute',
    top: 10,
    right: 10,
    width: 10,
    height: 10,
    borderRadius: 5,
    backgroundColor: COLORS.error,
  },
  upgradeBanner: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    padding: SPACING.md,
    borderRadius: BORDER_RADIUS.lg,
    marginBottom: SPACING.lg,
  },
  upgradeContent: {
    flexDirection: 'row',
    alignItems: 'center',
  },
  upgradeText: {
    marginLeft: SPACING.md,
  },
  upgradeTitle: {
    fontSize: FONT_SIZES.lg,
    fontWeight: '600',
    color: COLORS.text,
  },
  upgradeDesc: {
    fontSize: FONT_SIZES.sm,
    color: COLORS.text,
    opacity: 0.8,
  },
  statsRow: {
    flexDirection: 'row',
    gap: SPACING.md,
    marginBottom: SPACING.lg,
  },
  statCard: {
    flex: 1,
    borderRadius: BORDER_RADIUS.lg,
    overflow: 'hidden',
  },
  statGradient: {
    padding: SPACING.md,
    borderRadius: BORDER_RADIUS.lg,
    borderWidth: 1,
    borderColor: COLORS.border,
  },
  statIcon: {
    marginBottom: SPACING.sm,
  },
  statValue: {
    fontSize: FONT_SIZES.xxl,
    fontWeight: '700',
    color: COLORS.text,
  },
  statLabel: {
    fontSize: FONT_SIZES.sm,
    color: COLORS.textSecondary,
    marginTop: 2,
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
  },
  seeAll: {
    fontSize: FONT_SIZES.md,
    color: COLORS.primary,
    fontWeight: '500',
  },
  arbCard: {
    backgroundColor: COLORS.surface,
    borderRadius: BORDER_RADIUS.lg,
    padding: SPACING.md,
    marginBottom: SPACING.md,
    borderWidth: 1,
    borderColor: COLORS.border,
  },
  arbHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: SPACING.md,
  },
  arbSymbol: {
    fontSize: FONT_SIZES.lg,
    fontWeight: '700',
    color: COLORS.text,
  },
  arbProfit: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: COLORS.success + '20',
    paddingHorizontal: SPACING.sm,
    paddingVertical: SPACING.xs,
    borderRadius: BORDER_RADIUS.md,
  },
  arbProfitText: {
    fontSize: FONT_SIZES.md,
    fontWeight: '600',
    color: COLORS.success,
    marginLeft: SPACING.xs,
  },
  arbDetails: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  arbVenue: {
    flex: 1,
  },
  arbVenueLabel: {
    fontSize: FONT_SIZES.xs,
    color: COLORS.textMuted,
  },
  arbVenueName: {
    fontSize: FONT_SIZES.md,
    fontWeight: '600',
    color: COLORS.text,
  },
  arbPrice: {
    fontSize: FONT_SIZES.sm,
    color: COLORS.textSecondary,
  },
  noData: {
    alignItems: 'center',
    padding: SPACING.xl,
    backgroundColor: COLORS.surface,
    borderRadius: BORDER_RADIUS.lg,
  },
  noDataText: {
    fontSize: FONT_SIZES.md,
    color: COLORS.textMuted,
    marginTop: SPACING.sm,
  },
  watchlistItem: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    backgroundColor: COLORS.surface,
    borderRadius: BORDER_RADIUS.lg,
    padding: SPACING.md,
    marginBottom: SPACING.sm,
    borderWidth: 1,
    borderColor: COLORS.border,
  },
  watchlistLeft: {},
  watchlistSymbol: {
    fontSize: FONT_SIZES.lg,
    fontWeight: '600',
    color: COLORS.text,
  },
  watchlistExchange: {
    fontSize: FONT_SIZES.xs,
    color: COLORS.textMuted,
    marginTop: 2,
  },
  watchlistRight: {
    alignItems: 'flex-end',
  },
  watchlistPrice: {
    fontSize: FONT_SIZES.lg,
    fontWeight: '600',
    color: COLORS.text,
  },
  watchlistChange: {
    fontSize: FONT_SIZES.sm,
    fontWeight: '500',
  },
  quickActions: {
    flexDirection: 'row',
    justifyContent: 'space-around',
    marginTop: SPACING.md,
  },
  quickAction: {
    alignItems: 'center',
  },
  quickActionIcon: {
    width: 56,
    height: 56,
    borderRadius: BORDER_RADIUS.lg,
    justifyContent: 'center',
    alignItems: 'center',
    marginBottom: SPACING.sm,
  },
  quickActionText: {
    fontSize: FONT_SIZES.sm,
    color: COLORS.textSecondary,
    fontWeight: '500',
  },
  bottomSpacing: {
    height: SPACING.xxl,
  },
});
