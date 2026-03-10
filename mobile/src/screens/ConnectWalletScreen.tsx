import React, { useState } from 'react';
import {
  View,
  Text,
  StyleSheet,
  TouchableOpacity,
  ScrollView,
  ActivityIndicator,
  Alert,
  TextInput,
  Modal,
} from 'react-native';
import { LinearGradient } from 'expo-linear-gradient';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useNavigation } from '@react-navigation/native';
import { NativeStackNavigationProp } from '@react-navigation/native-stack';
import { Ionicons } from '@expo/vector-icons';
import * as Linking from 'expo-linking';
import { RootStackParamList } from '../../App';
import { COLORS, SPACING, FONT_SIZES, BORDER_RADIUS } from '../constants/theme';
import { useStore } from '../store/useStore';
import {
  fetchETHBalance,
  createThronosWallet,
  importThronosWallet,
  getSavedThronosWallet,
  fetchThronosBalances,
  isValidEVMAddress,
  isValidThronosAddress,
} from '../services/walletConnect';
import { CONFIG } from '../config';

type NavigationProp = NativeStackNavigationProp<RootStackParamList, 'ConnectWallet'>;

type ThronosModalMode = 'choose' | 'create' | 'import';

interface WalletOption {
  id: string;
  name: string;
  icon: keyof typeof Ionicons.glyphMap;
  color: string;
  popular?: boolean;
  type: 'evm' | 'thronos';
  deepLink?: string;
}

const WALLET_OPTIONS: WalletOption[] = [
  {
    id: 'thronos',
    name: 'Thronos Wallet',
    icon: 'planet',
    color: '#FFD700',
    popular: true,
    type: 'thronos',
  },
  {
    id: 'metamask',
    name: 'MetaMask',
    icon: 'logo-firefox',
    color: '#E8831D',
    popular: true,
    type: 'evm',
    deepLink: 'metamask://',
  },
  {
    id: 'trust',
    name: 'Trust Wallet',
    icon: 'shield',
    color: '#3375BB',
    type: 'evm',
    deepLink: 'trust://',
  },
  {
    id: 'coinbase',
    name: 'Coinbase Wallet',
    icon: 'logo-usd',
    color: '#0052FF',
    type: 'evm',
    deepLink: 'cbwallet://',
  },
  {
    id: 'phantom',
    name: 'Phantom (Solana)',
    icon: 'flash',
    color: '#AB9FF2',
    type: 'evm',
    deepLink: 'phantom://',
  },
];


export default function ConnectWalletScreen() {
  const navigation = useNavigation<NavigationProp>();
  const [connecting, setConnecting] = useState<string | null>(null);
  const { setWallet, setUser, wallet, user } = useStore();

  // Thronos wallet modal state
  const [thronosModalVisible, setThronosModalVisible] = useState(false);
  const [thronosMode, setThronosMode] = useState<ThronosModalMode>('choose');
  const [importAddress, setImportAddress] = useState('');
  const [importSecret, setImportSecret] = useState('');
  const [createdWallet, setCreatedWallet] = useState<{ address: string; secret: string } | null>(null);

  // EVM manual connect modal
  const [evmModalVisible, setEvmModalVisible] = useState(false);
  const [manualAddress, setManualAddress] = useState('');
  const [selectedEvmWallet, setSelectedEvmWallet] = useState<string>('');
  const [selectedChainKey, setSelectedChainKey] = useState<string>('ETHEREUM');

  const finalizeConnection = async (address: string, chainId: number | string, provider: string, walletType: 'thronos' | 'evm' | 'phantom' = 'evm', chainKey: string = 'ETHEREUM') => {
    let balance = '0';

    // Fetch real balance for EVM wallets
    if (address.startsWith('0x') && typeof chainId === 'number') {
      try {
        balance = await fetchETHBalance(address, chainId);
      } catch {
        // Keep 0 if fetching fails
      }
    }

    // For Thronos wallets, fetch THR balance
    if (address.startsWith('THR')) {
      try {
        const data = await fetchThronosBalances(address);
        const thr = data.tokens?.find((t) => t.symbol === 'THR');
        balance = thr ? String(thr.balance) : '0';
      } catch {
        // Keep 0
      }
    }

    setWallet({
      isConnected: true,
      address,
      chainId,
      balance,
      walletType,
      selectedChainKey: chainKey,
      provider,
    });

    // Preserve subscription tier across reconnections — never downgrade a paid user.
    // The store's persisted `subscription` field is the source of truth.
    const currentTier = useStore.getState().subscription;
    const userTier = user?.subscription || 'free';
    const bestTier = currentTier !== 'free' ? currentTier : userTier;

    setUser({
      id: user?.id || address,
      walletAddress: address,
      subscription: bestTier,
      thronosBalance: user?.thronosBalance || 0,
      rewardsBalance: user?.rewardsBalance || 0,
      referralCode: user?.referralCode || address.slice(address.startsWith('THR') ? 3 : 2, address.startsWith('THR') ? 11 : 10).toUpperCase(),
      createdAt: user?.createdAt || new Date().toISOString(),
    });

    // Also ensure store-level subscription matches
    if (bestTier !== 'free') {
      useStore.getState().setSubscription(bestTier as any);
    }

    navigation.reset({
      index: 0,
      routes: [{ name: 'MainTabs' }],
    });
  };

  // ── Thronos Wallet Handlers ──────────────────────────────────────────────

  const handleThronosCreate = async () => {
    setConnecting('thronos');
    try {
      const result = await createThronosWallet();
      setCreatedWallet(result);
      setThronosMode('create');
    } catch (error: any) {
      Alert.alert('Creation Failed', error.message || 'Could not create Thronos wallet. Check your connection.');
    } finally {
      setConnecting(null);
    }
  };

  const handleThronosImport = async () => {
    const address = importAddress.trim();
    const secret = importSecret.trim();

    if (!address || !secret) {
      Alert.alert('Missing Fields', 'Please enter both your Thronos address and secret key.');
      return;
    }

    if (!isValidThronosAddress(address)) {
      Alert.alert('Invalid Address', 'Please enter a valid Thronos address (starts with THR).');
      return;
    }

    setConnecting('thronos');
    try {
      await importThronosWallet(address, secret);
      await finalizeConnection(address, 'thronos', 'thronos', 'thronos', 'THRONOS');
      setThronosModalVisible(false);
      setImportAddress('');
      setImportSecret('');
    } catch (error: any) {
      Alert.alert('Connection Failed', error?.message || 'Failed to connect wallet. Please try again.');
    } finally {
      setConnecting(null);
    }
  };

  const handleThronosCreatedContinue = async () => {
    if (!createdWallet) return;
    setThronosModalVisible(false);
    setConnecting('thronos');
    try {
      await finalizeConnection(createdWallet.address, 'thronos', 'thronos', 'thronos', 'THRONOS');
    } finally {
      setConnecting(null);
    }
  };

  const handleThronosConnect = async () => {
    // Check if there's a saved Thronos wallet
    const saved = await getSavedThronosWallet();
    if (saved) {
      Alert.alert(
        'Existing Wallet Found',
        `Found saved wallet: ${saved.address.slice(0, 12)}...`,
        [
          {
            text: 'Use This Wallet',
            onPress: async () => {
              setConnecting('thronos');
              try {
                await finalizeConnection(saved.address, 'thronos', 'thronos', 'thronos', 'THRONOS');
              } finally {
                setConnecting(null);
              }
            },
          },
          {
            text: 'Use Different Wallet',
            onPress: () => {
              setThronosMode('choose');
              setThronosModalVisible(true);
            },
          },
          { text: 'Cancel', style: 'cancel' },
        ],
      );
      return;
    }

    setThronosMode('choose');
    setThronosModalVisible(true);
  };

  // ── EVM Wallet Handlers ─────────────────────────────────────────────────

  const handleEVMConnect = async (walletId: string) => {
    const option = WALLET_OPTIONS.find((w) => w.id === walletId);
    if (!option) return;

    // Try to open the wallet app via deep-link
    if (option.deepLink) {
      try {
        const canOpen = await Linking.canOpenURL(option.deepLink);
        if (canOpen) {
          // Open wallet app - user will be redirected back with their address
          // For now, show the manual entry dialog as the callback mechanism
          await Linking.openURL(option.deepLink);
        }
      } catch {
        // App not installed
      }
    }

    // Show manual address entry (the standard approach for Expo managed apps
    // since WalletConnect native SDK requires bare workflow for full session handling)
    setSelectedEvmWallet(walletId);
    setEvmModalVisible(true);
  };

  const handleEVMManualConnect = async () => {
    const address = manualAddress.trim();
    if (!isValidEVMAddress(address)) {
      Alert.alert('Invalid Address', 'Please enter a valid Ethereum address (0x...)');
      return;
    }

    const chain = CONFIG.SUPPORTED_CHAINS[selectedChainKey as keyof typeof CONFIG.SUPPORTED_CHAINS];
    const chainId = chain?.chainId ?? 1;

    setEvmModalVisible(false);
    setConnecting(selectedEvmWallet);
    try {
      await finalizeConnection(address, chainId, selectedEvmWallet, 'evm', selectedChainKey);
    } finally {
      setConnecting(null);
    }
  };

  const handleConnect = async (walletId: string) => {
    const option = WALLET_OPTIONS.find((w) => w.id === walletId);
    if (!option) return;

    if (option.type === 'thronos') {
      await handleThronosConnect();
    } else {
      await handleEVMConnect(walletId);
    }
  };

  // ── Render ──────────────────────────────────────────────────────────────

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
          <Text style={styles.descNote}>
            Demo build: wallet connection is simulated (no real on-chain transaction signing yet).
          </Text>
        </View>

        {/* Wallet Options */}
        <ScrollView
          style={styles.walletList}
          showsVerticalScrollIndicator={false}
        >
          {WALLET_OPTIONS.map((w) => (
            <TouchableOpacity
              key={w.id}
              style={[
                styles.walletOption,
                connecting === w.id && styles.walletOptionActive,
                w.id === 'thronos' && styles.thronosWalletOption,
              ]}
              onPress={() => handleConnect(w.id)}
              disabled={connecting !== null}
            >
              <View
                style={[
                  styles.walletIcon,
                  { backgroundColor: w.color + '20' },
                ]}
              >
                <Ionicons name={w.icon} size={28} color={w.color} />
              </View>
              <View style={styles.walletInfo}>
                <Text style={styles.walletName}>{w.name}</Text>
                {w.popular && (
                  <View style={[styles.popularBadge, w.id === 'thronos' && styles.thronosBadge]}>
                    <Text style={[styles.popularText, w.id === 'thronos' && styles.thronosBadgeText]}>
                      {w.id === 'thronos' ? 'Native' : 'Popular'}
                    </Text>
                  </View>
                )}
              </View>
              {connecting === w.id ? (
                <ActivityIndicator color={w.id === 'thronos' ? COLORS.thronosGold : COLORS.primary} />
              ) : (
                <Ionicons name="chevron-forward" size={24} color={COLORS.textMuted} />
              )}
            </TouchableOpacity>
          ))}
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

      {/* ── Thronos Wallet Modal ──────────────────────────────────────────── */}
      <Modal
        visible={thronosModalVisible}
        animationType="slide"
        presentationStyle="pageSheet"
        onRequestClose={() => setThronosModalVisible(false)}
      >
        <View style={styles.modalContainer}>
          <LinearGradient
            colors={[COLORS.background, COLORS.backgroundLight]}
            style={styles.modalGradient}
          >
            <View style={styles.modalHeader}>
              <Text style={styles.modalTitle}>
                {thronosMode === 'choose' ? 'Thronos Wallet' : thronosMode === 'create' ? 'New Wallet Created' : 'Import Wallet'}
              </Text>
              <TouchableOpacity onPress={() => setThronosModalVisible(false)}>
                <Ionicons name="close" size={28} color={COLORS.text} />
              </TouchableOpacity>
            </View>

            {thronosMode === 'choose' && (
              <View style={styles.modalContent}>
                <View style={styles.thronosLogoContainer}>
                  <Ionicons name="planet" size={64} color={COLORS.thronosGold} />
                </View>
                <Text style={styles.modalDesc}>
                  Connect with the native Thronos blockchain wallet. Create a new wallet or import an existing one.
                </Text>

                <TouchableOpacity
                  style={styles.thronosActionButton}
                  onPress={handleThronosCreate}
                  disabled={connecting !== null}
                >
                  <LinearGradient
                    colors={[COLORS.thronosGold, '#DAA520']}
                    style={styles.thronosActionGradient}
                  >
                    {connecting === 'thronos' ? (
                      <ActivityIndicator color={COLORS.background} />
                    ) : (
                      <>
                        <Ionicons name="add-circle" size={24} color={COLORS.background} />
                        <Text style={styles.thronosActionText}>Create New Wallet</Text>
                      </>
                    )}
                  </LinearGradient>
                </TouchableOpacity>

                <TouchableOpacity
                  style={styles.thronosSecondaryButton}
                  onPress={() => setThronosMode('import')}
                >
                  <Ionicons name="download" size={20} color={COLORS.thronosGold} />
                  <Text style={styles.thronosSecondaryText}>Import Existing Wallet</Text>
                </TouchableOpacity>
              </View>
            )}

            {thronosMode === 'create' && createdWallet && (
              <ScrollView style={styles.modalContent}>
                <View style={styles.successIcon}>
                  <Ionicons name="checkmark-circle" size={64} color={COLORS.success} />
                </View>
                <Text style={styles.successText}>Wallet Created Successfully!</Text>

                <View style={styles.credentialBox}>
                  <Text style={styles.credentialLabel}>Your Address</Text>
                  <Text style={styles.credentialValue} selectable>{createdWallet.address}</Text>
                </View>

                <View style={[styles.credentialBox, styles.secretBox]}>
                  <Text style={styles.credentialLabel}>
                    <Ionicons name="warning" size={14} color={COLORS.warning} /> Secret Key (SAVE THIS!)
                  </Text>
                  <Text style={styles.credentialValue} selectable>{createdWallet.secret}</Text>
                </View>

                <View style={styles.warningBox}>
                  <Ionicons name="alert-circle" size={20} color={COLORS.error} />
                  <Text style={styles.warningText}>
                    Write down your secret key and store it safely. If you lose it, you cannot recover your wallet. We do NOT store your secret key.
                  </Text>
                </View>

                <TouchableOpacity
                  style={styles.thronosActionButton}
                  onPress={handleThronosCreatedContinue}
                >
                  <LinearGradient
                    colors={[COLORS.success, COLORS.successDark]}
                    style={styles.thronosActionGradient}
                  >
                    <Ionicons name="checkmark" size={24} color={COLORS.text} />
                    <Text style={styles.thronosActionText}>I've Saved My Key - Continue</Text>
                  </LinearGradient>
                </TouchableOpacity>
              </ScrollView>
            )}

            {thronosMode === 'import' && (
              <View style={styles.modalContent}>
                <Text style={styles.importDesc}>
                  Enter your Thronos wallet address and secret key to import your wallet.
                </Text>

                <Text style={styles.inputLabel}>Thronos Address</Text>
                <TextInput
                  style={styles.textInput}
                  placeholder="THR..."
                  placeholderTextColor={COLORS.textMuted}
                  value={importAddress}
                  onChangeText={setImportAddress}
                  autoCapitalize="none"
                  autoCorrect={false}
                />

                <Text style={styles.inputLabel}>Secret Key</Text>
                <TextInput
                  style={styles.textInput}
                  placeholder="Enter your secret key"
                  placeholderTextColor={COLORS.textMuted}
                  value={importSecret}
                  onChangeText={setImportSecret}
                  autoCapitalize="none"
                  autoCorrect={false}
                  secureTextEntry
                />

                <TouchableOpacity
                  style={styles.thronosActionButton}
                  onPress={handleThronosImport}
                  disabled={connecting !== null}
                >
                  <LinearGradient
                    colors={[COLORS.thronosGold, '#DAA520']}
                    style={styles.thronosActionGradient}
                  >
                    {connecting === 'thronos' ? (
                      <ActivityIndicator color={COLORS.background} />
                    ) : (
                      <>
                        <Ionicons name="download" size={24} color={COLORS.background} />
                        <Text style={styles.thronosActionText}>Import Wallet</Text>
                      </>
                    )}
                  </LinearGradient>
                </TouchableOpacity>

                <TouchableOpacity
                  style={styles.thronosSecondaryButton}
                  onPress={() => setThronosMode('choose')}
                >
                  <Ionicons name="arrow-back" size={20} color={COLORS.textSecondary} />
                  <Text style={styles.thronosSecondaryText}>Back</Text>
                </TouchableOpacity>
              </View>
            )}
          </LinearGradient>
        </View>
      </Modal>

      {/* ── EVM Manual Address Modal ──────────────────────────────────────── */}
      <Modal
        visible={evmModalVisible}
        animationType="slide"
        presentationStyle="pageSheet"
        onRequestClose={() => setEvmModalVisible(false)}
      >
        <View style={styles.modalContainer}>
          <LinearGradient
            colors={[COLORS.background, COLORS.backgroundLight]}
            style={styles.modalGradient}
          >
            <View style={styles.modalHeader}>
              <Text style={styles.modalTitle}>Connect {selectedEvmWallet}</Text>
              <TouchableOpacity onPress={() => setEvmModalVisible(false)}>
                <Ionicons name="close" size={28} color={COLORS.text} />
              </TouchableOpacity>
            </View>

            <ScrollView style={styles.modalContent} showsVerticalScrollIndicator={false}>
              <Text style={styles.importDesc}>
                Select your network, then paste your public address below. This is a read-only connection — your private keys stay safe in your wallet app.
              </Text>

              {/* ── Network Selector ──────────────────────────────── */}
              <Text style={styles.inputLabel}>Select Network</Text>
              <View style={styles.chainSelectorGrid}>
                {(CONFIG.WALLET_CHAINS[selectedEvmWallet === 'phantom' ? 'phantom' : 'evm'] || CONFIG.WALLET_CHAINS.evm).map((key) => {
                  const chain = CONFIG.SUPPORTED_CHAINS[key as keyof typeof CONFIG.SUPPORTED_CHAINS];
                  if (!chain) return null;
                  const isSelected = selectedChainKey === key;
                  return (
                    <TouchableOpacity
                      key={key}
                      style={[styles.chainChip, isSelected && styles.chainChipSelected]}
                      onPress={() => setSelectedChainKey(key)}
                    >
                      <View style={[styles.chainDot, isSelected && styles.chainDotSelected]} />
                      <Text style={[styles.chainChipText, isSelected && styles.chainChipTextSelected]}>
                        {chain.name}
                      </Text>
                    </TouchableOpacity>
                  );
                })}
              </View>

              <Text style={styles.inputLabel}>Wallet Address</Text>
              <TextInput
                style={styles.textInput}
                placeholder="0x..."
                placeholderTextColor={COLORS.textMuted}
                value={manualAddress}
                onChangeText={setManualAddress}
                autoCapitalize="none"
                autoCorrect={false}
              />

              <View style={styles.readOnlyNote}>
                <Ionicons name="eye" size={16} color={COLORS.info} />
                <Text style={styles.readOnlyText}>
                  Read-only connection. Trader Sentinel monitors prices and sends signals — it does not execute trades from your wallet.
                </Text>
              </View>

              <TouchableOpacity
                style={styles.thronosActionButton}
                onPress={handleEVMManualConnect}
              >
                <LinearGradient
                  colors={[COLORS.primary, COLORS.primaryDark]}
                  style={styles.thronosActionGradient}
                >
                  <Ionicons name="wallet" size={24} color={COLORS.text} />
                  <Text style={styles.thronosActionText}>Connect</Text>
                </LinearGradient>
              </TouchableOpacity>
            </ScrollView>
          </LinearGradient>
        </View>
      </Modal>
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
  descNote: {
    marginTop: SPACING.xs,
    fontSize: FONT_SIZES.sm,
    color: COLORS.warning,
    lineHeight: 18,
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
  thronosWalletOption: {
    borderColor: COLORS.thronosGold + '50',
    backgroundColor: COLORS.thronosGold + '08',
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
  thronosBadge: {
    backgroundColor: COLORS.thronosGold + '30',
  },
  thronosBadgeText: {
    color: COLORS.thronosGold,
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

  // ── Modal Styles ──────────────────────────────────────────────────────
  modalContainer: {
    flex: 1,
    backgroundColor: COLORS.background,
  },
  modalGradient: {
    flex: 1,
    paddingHorizontal: SPACING.lg,
  },
  modalHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    paddingVertical: SPACING.lg,
    borderBottomWidth: 1,
    borderBottomColor: COLORS.border,
  },
  modalTitle: {
    fontSize: FONT_SIZES.xxl,
    fontWeight: '700',
    color: COLORS.text,
  },
  modalContent: {
    flex: 1,
    paddingTop: SPACING.xl,
  },
  modalDesc: {
    fontSize: FONT_SIZES.md,
    color: COLORS.textSecondary,
    lineHeight: 22,
    textAlign: 'center',
    marginBottom: SPACING.xl,
  },
  thronosLogoContainer: {
    alignItems: 'center',
    marginBottom: SPACING.lg,
  },
  thronosActionButton: {
    marginTop: SPACING.lg,
    borderRadius: BORDER_RADIUS.lg,
    overflow: 'hidden',
  },
  thronosActionGradient: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    paddingVertical: SPACING.md,
    paddingHorizontal: SPACING.xl,
    gap: SPACING.sm,
  },
  thronosActionText: {
    fontSize: FONT_SIZES.lg,
    fontWeight: '700',
    color: COLORS.background,
  },
  thronosSecondaryButton: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    paddingVertical: SPACING.md,
    marginTop: SPACING.md,
    gap: SPACING.sm,
  },
  thronosSecondaryText: {
    fontSize: FONT_SIZES.md,
    color: COLORS.thronosGold,
    fontWeight: '500',
  },
  successIcon: {
    alignItems: 'center',
    marginBottom: SPACING.md,
  },
  successText: {
    fontSize: FONT_SIZES.xl,
    fontWeight: '700',
    color: COLORS.success,
    textAlign: 'center',
    marginBottom: SPACING.xl,
  },
  credentialBox: {
    backgroundColor: COLORS.surface,
    borderRadius: BORDER_RADIUS.lg,
    padding: SPACING.md,
    marginBottom: SPACING.md,
    borderWidth: 1,
    borderColor: COLORS.border,
  },
  secretBox: {
    borderColor: COLORS.warning + '50',
    backgroundColor: COLORS.warning + '08',
  },
  credentialLabel: {
    fontSize: FONT_SIZES.sm,
    color: COLORS.textSecondary,
    marginBottom: SPACING.xs,
    fontWeight: '600',
  },
  credentialValue: {
    fontSize: FONT_SIZES.md,
    color: COLORS.text,
    fontFamily: 'monospace',
  },
  warningBox: {
    flexDirection: 'row',
    backgroundColor: COLORS.error + '15',
    borderRadius: BORDER_RADIUS.lg,
    padding: SPACING.md,
    marginBottom: SPACING.md,
    borderWidth: 1,
    borderColor: COLORS.error + '30',
    gap: SPACING.sm,
  },
  warningText: {
    flex: 1,
    fontSize: FONT_SIZES.sm,
    color: COLORS.error,
    lineHeight: 20,
  },
  importDesc: {
    fontSize: FONT_SIZES.md,
    color: COLORS.textSecondary,
    lineHeight: 22,
    marginBottom: SPACING.lg,
  },
  inputLabel: {
    fontSize: FONT_SIZES.sm,
    color: COLORS.textSecondary,
    fontWeight: '600',
    marginBottom: SPACING.xs,
    marginTop: SPACING.md,
  },
  textInput: {
    backgroundColor: COLORS.surface,
    borderRadius: BORDER_RADIUS.lg,
    padding: SPACING.md,
    fontSize: FONT_SIZES.md,
    color: COLORS.text,
    borderWidth: 1,
    borderColor: COLORS.border,
  },
  chainSelectorGrid: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: SPACING.sm,
    marginBottom: SPACING.lg,
  },
  chainChip: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: COLORS.surface,
    borderRadius: BORDER_RADIUS.full,
    paddingHorizontal: SPACING.md,
    paddingVertical: SPACING.sm,
    borderWidth: 1,
    borderColor: COLORS.border,
  },
  chainChipSelected: {
    borderColor: COLORS.primary,
    backgroundColor: COLORS.primary + '15',
  },
  chainDot: {
    width: 8,
    height: 8,
    borderRadius: 4,
    backgroundColor: COLORS.textMuted,
    marginRight: SPACING.xs,
  },
  chainDotSelected: {
    backgroundColor: COLORS.primary,
  },
  chainChipText: {
    fontSize: FONT_SIZES.sm,
    color: COLORS.textSecondary,
    fontWeight: '500',
  },
  chainChipTextSelected: {
    color: COLORS.primary,
    fontWeight: '600',
  },
  readOnlyNote: {
    flexDirection: 'row',
    backgroundColor: COLORS.info + '15',
    borderRadius: BORDER_RADIUS.lg,
    padding: SPACING.md,
    marginTop: SPACING.lg,
    gap: SPACING.sm,
    borderWidth: 1,
    borderColor: COLORS.info + '30',
  },
  readOnlyText: {
    flex: 1,
    fontSize: FONT_SIZES.sm,
    color: COLORS.info,
    lineHeight: 20,
  },
});
