import React, { useState } from 'react';
import {
  View,
  Text,
  StyleSheet,
  TouchableOpacity,
  ScrollView,
  ActivityIndicator,
  Alert,
} from 'react-native';
import { LinearGradient } from 'expo-linear-gradient';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useNavigation } from '@react-navigation/native';
import { NativeStackNavigationProp } from '@react-navigation/native-stack';
import { Ionicons } from '@expo/vector-icons';
import { RootStackParamList } from '../../App';
import { COLORS, SPACING, FONT_SIZES, BORDER_RADIUS } from '../constants/theme';
import { useStore } from '../store/useStore';

type NavigationProp = NativeStackNavigationProp<RootStackParamList, 'ConnectWallet'>;

interface WalletOption {
  id: string;
  name: string;
  icon: keyof typeof Ionicons.glyphMap;
  color: string;
  popular?: boolean;
}

const WALLET_OPTIONS: WalletOption[] = [
  { id: 'walletconnect', name: 'WalletConnect', icon: 'qr-code', color: '#3B99FC', popular: true },
  { id: 'metamask', name: 'MetaMask', icon: 'logo-firefox', color: '#E8831D', popular: true },
  { id: 'trust', name: 'Trust Wallet', icon: 'shield', color: '#3375BB' },
  { id: 'coinbase', name: 'Coinbase Wallet', icon: 'logo-usd', color: '#0052FF' },
  { id: 'rainbow', name: 'Rainbow', icon: 'color-palette', color: '#001E59' },
  { id: 'phantom', name: 'Phantom (Solana)', icon: 'flash', color: '#AB9FF2' },
];

export default function ConnectWalletScreen() {
  const navigation = useNavigation<NavigationProp>();
  const [connecting, setConnecting] = useState<string | null>(null);
  const { setWallet, setUser } = useStore();

  const handleConnect = async (walletId: string) => {
    setConnecting(walletId);

    try {
      // Simulate wallet connection
      // In production, this would use WalletConnect or native wallet SDKs
      await new Promise((resolve) => setTimeout(resolve, 2000));

      // Mock connected wallet data
      const mockAddress = '0x' + Math.random().toString(16).slice(2, 42);

      setWallet({
        isConnected: true,
        address: mockAddress,
        chainId: 1,
        balance: '0.5',
      });

      setUser({
        id: mockAddress,
        walletAddress: mockAddress,
        subscription: 'free',
        thronosBalance: 0,
        rewardsBalance: 0,
        referralCode: mockAddress.slice(0, 8).toUpperCase(),
        createdAt: new Date().toISOString(),
      });

      // Navigate to main app
      navigation.reset({
        index: 0,
        routes: [{ name: 'MainTabs' }],
      });
    } catch (error) {
      Alert.alert('Connection Failed', 'Failed to connect wallet. Please try again.');
    } finally {
      setConnecting(null);
    }
  };

  return (
    <SafeAreaView style={styles.container}>
      <LinearGradient
        colors={[COLORS.background, COLORS.backgroundLight]}
        style={styles.gradient}
      >
        {/* Header */}
        <View style={styles.header}>
          <TouchableOpacity
            style={styles.backButton}
            onPress={() => navigation.goBack()}
          >
            <Ionicons name="arrow-back" size={24} color={COLORS.text} />
          </TouchableOpacity>
          <Text style={styles.title}>Connect Wallet</Text>
          <View style={styles.placeholder} />
        </View>

        {/* Description */}
        <View style={styles.description}>
          <Text style={styles.descTitle}>Choose your wallet</Text>
          <Text style={styles.descText}>
            Connect your wallet to access Trader Sentinel and start earning Thronos rewards
          </Text>
        </View>

        {/* Wallet Options */}
        <ScrollView
          style={styles.walletList}
          showsVerticalScrollIndicator={false}
        >
          {WALLET_OPTIONS.map((wallet) => (
            <TouchableOpacity
              key={wallet.id}
              style={[
                styles.walletOption,
                connecting === wallet.id && styles.walletOptionActive,
              ]}
              onPress={() => handleConnect(wallet.id)}
              disabled={connecting !== null}
            >
              <View
                style={[
                  styles.walletIcon,
                  { backgroundColor: wallet.color + '20' },
                ]}
              >
                <Ionicons name={wallet.icon} size={28} color={wallet.color} />
              </View>
              <View style={styles.walletInfo}>
                <Text style={styles.walletName}>{wallet.name}</Text>
                {wallet.popular && (
                  <View style={styles.popularBadge}>
                    <Text style={styles.popularText}>Popular</Text>
                  </View>
                )}
              </View>
              {connecting === wallet.id ? (
                <ActivityIndicator color={COLORS.primary} />
              ) : (
                <Ionicons name="chevron-forward" size={24} color={COLORS.textMuted} />
              )}
            </TouchableOpacity>
          ))}

          {/* QR Code Option */}
          <TouchableOpacity style={styles.qrOption}>
            <LinearGradient
              colors={[COLORS.surface, COLORS.backgroundCard]}
              style={styles.qrGradient}
            >
              <Ionicons name="scan" size={32} color={COLORS.primary} />
              <Text style={styles.qrText}>Scan QR Code</Text>
              <Text style={styles.qrSubtext}>
                Connect with any WalletConnect compatible wallet
              </Text>
            </LinearGradient>
          </TouchableOpacity>
        </ScrollView>

        {/* Thronos Benefits */}
        <View style={styles.benefits}>
          <Text style={styles.benefitsTitle}>
            <Ionicons name="star" size={16} color={COLORS.thronosGold} /> Thronos Benefits
          </Text>
          <Text style={styles.benefitsText}>
            Earn rewards, stake tokens, and participate in governance when you connect your wallet
          </Text>
        </View>

        {/* Security Note */}
        <View style={styles.securityNote}>
          <Ionicons name="lock-closed" size={16} color={COLORS.success} />
          <Text style={styles.securityText}>
            Your keys, your crypto. We never have access to your funds.
          </Text>
        </View>
      </LinearGradient>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: COLORS.background,
  },
  gradient: {
    flex: 1,
    paddingHorizontal: SPACING.lg,
  },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingVertical: SPACING.md,
  },
  backButton: {
    width: 40,
    height: 40,
    borderRadius: BORDER_RADIUS.md,
    backgroundColor: COLORS.surface,
    justifyContent: 'center',
    alignItems: 'center',
  },
  title: {
    fontSize: FONT_SIZES.xl,
    fontWeight: '600',
    color: COLORS.text,
  },
  placeholder: {
    width: 40,
  },
  description: {
    marginBottom: SPACING.lg,
  },
  descTitle: {
    fontSize: FONT_SIZES.xxl,
    fontWeight: '700',
    color: COLORS.text,
    marginBottom: SPACING.xs,
  },
  descText: {
    fontSize: FONT_SIZES.md,
    color: COLORS.textSecondary,
    lineHeight: 22,
  },
  walletList: {
    flex: 1,
  },
  walletOption: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: COLORS.surface,
    borderRadius: BORDER_RADIUS.lg,
    padding: SPACING.md,
    marginBottom: SPACING.md,
    borderWidth: 1,
    borderColor: COLORS.border,
  },
  walletOptionActive: {
    borderColor: COLORS.primary,
    backgroundColor: COLORS.backgroundCard,
  },
  walletIcon: {
    width: 52,
    height: 52,
    borderRadius: BORDER_RADIUS.md,
    justifyContent: 'center',
    alignItems: 'center',
    marginRight: SPACING.md,
  },
  walletInfo: {
    flex: 1,
  },
  walletName: {
    fontSize: FONT_SIZES.lg,
    fontWeight: '600',
    color: COLORS.text,
  },
  popularBadge: {
    backgroundColor: COLORS.primary + '30',
    paddingHorizontal: SPACING.sm,
    paddingVertical: 2,
    borderRadius: BORDER_RADIUS.sm,
    alignSelf: 'flex-start',
    marginTop: 4,
  },
  popularText: {
    fontSize: FONT_SIZES.xs,
    color: COLORS.primary,
    fontWeight: '600',
  },
  qrOption: {
    marginVertical: SPACING.md,
    borderRadius: BORDER_RADIUS.lg,
    overflow: 'hidden',
  },
  qrGradient: {
    padding: SPACING.xl,
    alignItems: 'center',
    borderWidth: 1,
    borderColor: COLORS.border,
    borderRadius: BORDER_RADIUS.lg,
    borderStyle: 'dashed',
  },
  qrText: {
    fontSize: FONT_SIZES.lg,
    fontWeight: '600',
    color: COLORS.text,
    marginTop: SPACING.sm,
  },
  qrSubtext: {
    fontSize: FONT_SIZES.sm,
    color: COLORS.textSecondary,
    marginTop: SPACING.xs,
    textAlign: 'center',
  },
  benefits: {
    backgroundColor: COLORS.thronosGold + '15',
    borderRadius: BORDER_RADIUS.lg,
    padding: SPACING.md,
    marginBottom: SPACING.md,
    borderWidth: 1,
    borderColor: COLORS.thronosGold + '30',
  },
  benefitsTitle: {
    fontSize: FONT_SIZES.md,
    fontWeight: '600',
    color: COLORS.thronosGold,
    marginBottom: SPACING.xs,
  },
  benefitsText: {
    fontSize: FONT_SIZES.sm,
    color: COLORS.textSecondary,
    lineHeight: 20,
  },
  securityNote: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    paddingVertical: SPACING.md,
    marginBottom: SPACING.md,
  },
  securityText: {
    fontSize: FONT_SIZES.sm,
    color: COLORS.textMuted,
    marginLeft: SPACING.xs,
  },
});
