import React, { useState } from 'react';
import {
  View,
  Text,
  StyleSheet,
  ScrollView,
  TouchableOpacity,
  Modal,
  ActivityIndicator,
  Alert,
} from 'react-native';
import { LinearGradient } from 'expo-linear-gradient';
import { Ionicons } from '@expo/vector-icons';
import * as WebBrowser from 'expo-web-browser';
import { COLORS, SPACING, FONT_SIZES, BORDER_RADIUS } from '../constants/theme';
import { useStore } from '../store/useStore';
import { CONFIG } from '../config';
import { thronosService } from '../services/thronos';

type PaymentMethod = 'crypto' | 'fiat';
type CryptoChain = keyof typeof CONFIG.SUPPORTED_CHAINS;

interface Package {
  id: string;
  name: string;
  priceUSD: number;
  priceThronos: number;
  features: string[];
  rewardsMultiplier: number;
  popular?: boolean;
}

const packages: Package[] = [
  { ...CONFIG.PACKAGES.STARTER },
  { ...CONFIG.PACKAGES.PRO, popular: true },
  { ...CONFIG.PACKAGES.ELITE },
  { ...CONFIG.PACKAGES.WHALE },
];

export default function SubscriptionScreen() {
  const { subscription, wallet, setSubscription } = useStore();
  const [selectedPackage, setSelectedPackage] = useState<Package | null>(null);
  const [paymentMethod, setPaymentMethod] = useState<PaymentMethod>('crypto');
  const [selectedChain, setSelectedChain] = useState<CryptoChain>('ETHEREUM');
  const [selectedToken, setSelectedToken] = useState<string>('USDT');
  const [showPaymentModal, setShowPaymentModal] = useState(false);
  const [processing, setProcessing] = useState(false);

  const chains = Object.entries(CONFIG.SUPPORTED_CHAINS).filter(
    ([key]) => key !== 'SOLANA'
  );

  const handleSelectPackage = (pkg: Package) => {
    setSelectedPackage(pkg);
    setShowPaymentModal(true);
  };

  const handleCryptoPayment = async () => {
    if (!selectedPackage || !wallet.address) return;

    setProcessing(true);
    try {
      const chainInfo = CONFIG.SUPPORTED_CHAINS[selectedChain];
      const tokens = CONFIG.PAYMENT_TOKENS[chainInfo.chainId as keyof typeof CONFIG.PAYMENT_TOKENS];
      const token = tokens?.find((t) => t.symbol === selectedToken);

      if (!token) {
        Alert.alert('Error', 'Token not supported on this chain');
        return;
      }

      // Determine price
      const price = selectedToken === 'THRONOS'
        ? selectedPackage.priceThronos.toString()
        : selectedPackage.priceUSD.toString();

      const result = await thronosService.processPayment({
        packageId: selectedPackage.id,
        chainId: chainInfo.chainId as number,
        tokenAddress: token.address,
        amount: price,
        userAddress: wallet.address,
      });

      if (result.success) {
        setSubscription(selectedPackage.id as any);
        setShowPaymentModal(false);
        Alert.alert(
          'Success!',
          `You are now subscribed to ${selectedPackage.name}!\n\nTransaction: ${result.txHash?.slice(0, 20)}...`,
        );
      } else {
        Alert.alert('Payment Failed', result.error || 'Unknown error');
      }
    } catch (error: any) {
      Alert.alert('Error', error.message || 'Payment failed');
    } finally {
      setProcessing(false);
    }
  };

  const handleFiatPayment = async () => {
    if (!selectedPackage) return;

    setProcessing(true);
    try {
      const { redirectUrl } = await thronosService.processFiatPayment(
        selectedPackage.id,
        'user@email.com'
      );

      await WebBrowser.openBrowserAsync(redirectUrl);
      setShowPaymentModal(false);
    } catch (error: any) {
      Alert.alert('Error', error.message || 'Failed to process payment');
    } finally {
      setProcessing(false);
    }
  };

  const getPackageGradient = (pkg: Package): [string, string] => {
    switch (pkg.id) {
      case 'whale':
        return [COLORS.thronosGold, '#B8860B'];
      case 'elite':
        return [COLORS.thronosPurple, '#6D28D9'];
      case 'pro':
        return [COLORS.primary, COLORS.primaryDark];
      default:
        return [COLORS.surface, COLORS.backgroundCard];
    }
  };

  return (
    <View style={styles.container}>
      <ScrollView
        style={styles.scrollView}
        showsVerticalScrollIndicator={false}
      >
        {/* Header Info */}
        <View style={styles.headerInfo}>
          <View style={styles.currentPlan}>
            <Text style={styles.currentPlanLabel}>Current Plan</Text>
            <Text style={styles.currentPlanValue}>{subscription.toUpperCase()}</Text>
          </View>
          <View style={styles.savingsInfo}>
            <Ionicons name="star" size={16} color={COLORS.thronosGold} />
            <Text style={styles.savingsText}>
              Pay with THRONOS & save up to 25%
            </Text>
          </View>
        </View>

        {/* Packages */}
        {packages.map((pkg) => (
          <TouchableOpacity
            key={pkg.id}
            onPress={() => handleSelectPackage(pkg)}
            disabled={subscription === pkg.id}
          >
            <LinearGradient
              colors={getPackageGradient(pkg)}
              style={[
                styles.packageCard,
                pkg.popular && styles.popularPackage,
                subscription === pkg.id && styles.currentPackage,
              ]}
              start={{ x: 0, y: 0 }}
              end={{ x: 1, y: 1 }}
            >
              {pkg.popular && (
                <View style={styles.popularBadge}>
                  <Text style={styles.popularBadgeText}>MOST POPULAR</Text>
                </View>
              )}

              <View style={styles.packageHeader}>
                <View>
                  <Text style={styles.packageName}>{pkg.name}</Text>
                  <Text style={styles.rewardsMultiplier}>
                    {pkg.rewardsMultiplier}x Rewards
                  </Text>
                </View>
                <View style={styles.priceContainer}>
                  <Text style={styles.priceUSD}>${pkg.priceUSD}</Text>
                  <Text style={styles.priceThronos}>
                    or {pkg.priceThronos} THRONOS
                  </Text>
                  <Text style={styles.pricePeriod}>/month</Text>
                </View>
              </View>

              <View style={styles.featuresList}>
                {pkg.features.map((feature, index) => (
                  <View key={index} style={styles.featureItem}>
                    <Ionicons
                      name="checkmark-circle"
                      size={18}
                      color={pkg.id === 'starter' ? COLORS.success : COLORS.text}
                    />
                    <Text
                      style={[
                        styles.featureText,
                        pkg.id !== 'starter' && { color: COLORS.text },
                      ]}
                    >
                      {feature}
                    </Text>
                  </View>
                ))}
              </View>

              {subscription === pkg.id ? (
                <View style={styles.currentBadge}>
                  <Text style={styles.currentBadgeText}>Current Plan</Text>
                </View>
              ) : (
                <TouchableOpacity
                  style={styles.selectButton}
                  onPress={() => handleSelectPackage(pkg)}
                >
                  <Text style={styles.selectButtonText}>
                    Select {pkg.name}
                  </Text>
                </TouchableOpacity>
              )}
            </LinearGradient>
          </TouchableOpacity>
        ))}

        {/* Thronos Benefits */}
        <View style={styles.benefitsSection}>
          <Text style={styles.benefitsTitle}>Thronos Ecosystem Benefits</Text>
          <View style={styles.benefitItem}>
            <Ionicons name="gift" size={24} color={COLORS.thronosGold} />
            <View style={styles.benefitText}>
              <Text style={styles.benefitName}>Earn Rewards</Text>
              <Text style={styles.benefitDesc}>
                Get THRONOS tokens for using signals
              </Text>
            </View>
          </View>
          <View style={styles.benefitItem}>
            <Ionicons name="water" size={24} color={COLORS.accent} />
            <View style={styles.benefitText}>
              <Text style={styles.benefitName}>Liquidity Pools</Text>
              <Text style={styles.benefitDesc}>
                Earn up to 12% APY providing liquidity
              </Text>
            </View>
          </View>
          <View style={styles.benefitItem}>
            <Ionicons name="people" size={24} color={COLORS.success} />
            <View style={styles.benefitText}>
              <Text style={styles.benefitName}>Referrals</Text>
              <Text style={styles.benefitDesc}>
                Earn 50 THRONOS per referred friend
              </Text>
            </View>
          </View>
        </View>

        <View style={styles.bottomSpacing} />
      </ScrollView>

      {/* Payment Modal */}
      <Modal
        visible={showPaymentModal}
        animationType="slide"
        transparent={true}
        onRequestClose={() => setShowPaymentModal(false)}
      >
        <View style={styles.modalOverlay}>
          <View style={styles.modalContent}>
            {/* Modal Header */}
            <View style={styles.modalHeader}>
              <Text style={styles.modalTitle}>
                Subscribe to {selectedPackage?.name}
              </Text>
              <TouchableOpacity
                onPress={() => setShowPaymentModal(false)}
                disabled={processing}
              >
                <Ionicons name="close" size={24} color={COLORS.text} />
              </TouchableOpacity>
            </View>

            {/* Payment Methods */}
            <View style={styles.paymentMethods}>
              <TouchableOpacity
                style={[
                  styles.paymentMethodButton,
                  paymentMethod === 'crypto' && styles.paymentMethodActive,
                ]}
                onPress={() => setPaymentMethod('crypto')}
              >
                <Ionicons
                  name="wallet"
                  size={24}
                  color={paymentMethod === 'crypto' ? COLORS.primary : COLORS.textMuted}
                />
                <Text
                  style={[
                    styles.paymentMethodText,
                    paymentMethod === 'crypto' && styles.paymentMethodTextActive,
                  ]}
                >
                  Crypto
                </Text>
              </TouchableOpacity>

              <TouchableOpacity
                style={[
                  styles.paymentMethodButton,
                  paymentMethod === 'fiat' && styles.paymentMethodActive,
                ]}
                onPress={() => setPaymentMethod('fiat')}
              >
                <Ionicons
                  name="card"
                  size={24}
                  color={paymentMethod === 'fiat' ? COLORS.primary : COLORS.textMuted}
                />
                <Text
                  style={[
                    styles.paymentMethodText,
                    paymentMethod === 'fiat' && styles.paymentMethodTextActive,
                  ]}
                >
                  Card/Fiat
                </Text>
              </TouchableOpacity>
            </View>

            {paymentMethod === 'crypto' ? (
              <>
                {/* Chain Selection */}
                <Text style={styles.sectionLabel}>Select Network</Text>
                <ScrollView
                  horizontal
                  showsHorizontalScrollIndicator={false}
                  style={styles.chainSelector}
                >
                  {chains.map(([key, chain]) => (
                    <TouchableOpacity
                      key={key}
                      style={[
                        styles.chainButton,
                        selectedChain === key && styles.chainButtonActive,
                      ]}
                      onPress={() => setSelectedChain(key as CryptoChain)}
                    >
                      <Text
                        style={[
                          styles.chainButtonText,
                          selectedChain === key && styles.chainButtonTextActive,
                        ]}
                      >
                        {(chain as any).name}
                      </Text>
                    </TouchableOpacity>
                  ))}
                </ScrollView>

                {/* Token Selection */}
                <Text style={styles.sectionLabel}>Pay with</Text>
                <View style={styles.tokenSelector}>
                  {['USDT', 'USDC', 'THRONOS'].map((token) => (
                    <TouchableOpacity
                      key={token}
                      style={[
                        styles.tokenButton,
                        selectedToken === token && styles.tokenButtonActive,
                      ]}
                      onPress={() => setSelectedToken(token)}
                    >
                      <Text
                        style={[
                          styles.tokenButtonText,
                          selectedToken === token && styles.tokenButtonTextActive,
                        ]}
                      >
                        {token}
                      </Text>
                      {token === 'THRONOS' && (
                        <View style={styles.discountBadge}>
                          <Text style={styles.discountText}>-25%</Text>
                        </View>
                      )}
                    </TouchableOpacity>
                  ))}
                </View>

                {/* Price Summary */}
                <View style={styles.priceSummary}>
                  <Text style={styles.summaryLabel}>Total</Text>
                  <Text style={styles.summaryValue}>
                    {selectedToken === 'THRONOS'
                      ? `${selectedPackage?.priceThronos} THRONOS`
                      : `${selectedPackage?.priceUSD} ${selectedToken}`
                    }
                  </Text>
                </View>

                {/* Pay Button */}
                <TouchableOpacity
                  style={styles.payButton}
                  onPress={handleCryptoPayment}
                  disabled={processing}
                >
                  <LinearGradient
                    colors={[COLORS.primary, COLORS.primaryDark]}
                    style={styles.payButtonGradient}
                  >
                    {processing ? (
                      <ActivityIndicator color={COLORS.text} />
                    ) : (
                      <>
                        <Ionicons name="flash" size={20} color={COLORS.text} />
                        <Text style={styles.payButtonText}>
                          Pay with {selectedToken}
                        </Text>
                      </>
                    )}
                  </LinearGradient>
                </TouchableOpacity>
              </>
            ) : (
              <>
                {/* Fiat Payment */}
                <View style={styles.fiatInfo}>
                  <Ionicons name="shield-checkmark" size={48} color={COLORS.success} />
                  <Text style={styles.fiatTitle}>Secure Payment via Thronos Gateway</Text>
                  <Text style={styles.fiatDesc}>
                    Pay with credit card, debit card, or bank transfer. Powered by Stripe.
                  </Text>
                </View>

                <View style={styles.priceSummary}>
                  <Text style={styles.summaryLabel}>Total</Text>
                  <Text style={styles.summaryValue}>
                    ${selectedPackage?.priceUSD} USD
                  </Text>
                </View>

                <TouchableOpacity
                  style={styles.payButton}
                  onPress={handleFiatPayment}
                  disabled={processing}
                >
                  <LinearGradient
                    colors={[COLORS.success, COLORS.successDark]}
                    style={styles.payButtonGradient}
                  >
                    {processing ? (
                      <ActivityIndicator color={COLORS.text} />
                    ) : (
                      <>
                        <Ionicons name="card" size={20} color={COLORS.text} />
                        <Text style={styles.payButtonText}>
                          Pay ${selectedPackage?.priceUSD}
                        </Text>
                      </>
                    )}
                  </LinearGradient>
                </TouchableOpacity>
              </>
            )}

            <Text style={styles.securityNote}>
              <Ionicons name="lock-closed" size={12} color={COLORS.textMuted} />
              {' '}Secured by Thronos Gateway
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
  headerInfo: {
    marginVertical: SPACING.lg,
  },
  currentPlan: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: SPACING.md,
  },
  currentPlanLabel: {
    fontSize: FONT_SIZES.md,
    color: COLORS.textSecondary,
  },
  currentPlanValue: {
    fontSize: FONT_SIZES.lg,
    fontWeight: '600',
    color: COLORS.primary,
  },
  savingsInfo: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: COLORS.thronosGold + '20',
    padding: SPACING.md,
    borderRadius: BORDER_RADIUS.md,
  },
  savingsText: {
    fontSize: FONT_SIZES.sm,
    color: COLORS.thronosGold,
    marginLeft: SPACING.sm,
    fontWeight: '500',
  },
  packageCard: {
    borderRadius: BORDER_RADIUS.xl,
    padding: SPACING.lg,
    marginBottom: SPACING.lg,
    borderWidth: 1,
    borderColor: COLORS.border,
  },
  popularPackage: {
    borderColor: COLORS.primary,
    borderWidth: 2,
  },
  currentPackage: {
    opacity: 0.7,
  },
  popularBadge: {
    position: 'absolute',
    top: -12,
    alignSelf: 'center',
    backgroundColor: COLORS.primary,
    paddingHorizontal: SPACING.md,
    paddingVertical: SPACING.xs,
    borderRadius: BORDER_RADIUS.full,
  },
  popularBadgeText: {
    fontSize: FONT_SIZES.xs,
    fontWeight: '700',
    color: COLORS.text,
  },
  packageHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'flex-start',
    marginBottom: SPACING.lg,
  },
  packageName: {
    fontSize: FONT_SIZES.xxl,
    fontWeight: '700',
    color: COLORS.text,
  },
  rewardsMultiplier: {
    fontSize: FONT_SIZES.sm,
    color: COLORS.thronosGold,
    fontWeight: '500',
    marginTop: 2,
  },
  priceContainer: {
    alignItems: 'flex-end',
  },
  priceUSD: {
    fontSize: FONT_SIZES.xxxl,
    fontWeight: '700',
    color: COLORS.text,
  },
  priceThronos: {
    fontSize: FONT_SIZES.sm,
    color: COLORS.thronosGold,
  },
  pricePeriod: {
    fontSize: FONT_SIZES.sm,
    color: COLORS.textMuted,
  },
  featuresList: {
    marginBottom: SPACING.lg,
  },
  featureItem: {
    flexDirection: 'row',
    alignItems: 'center',
    marginBottom: SPACING.sm,
  },
  featureText: {
    fontSize: FONT_SIZES.md,
    color: COLORS.textSecondary,
    marginLeft: SPACING.sm,
    flex: 1,
  },
  currentBadge: {
    backgroundColor: COLORS.success + '20',
    padding: SPACING.md,
    borderRadius: BORDER_RADIUS.md,
    alignItems: 'center',
  },
  currentBadgeText: {
    fontSize: FONT_SIZES.md,
    fontWeight: '600',
    color: COLORS.success,
  },
  selectButton: {
    backgroundColor: 'rgba(255,255,255,0.2)',
    padding: SPACING.md,
    borderRadius: BORDER_RADIUS.md,
    alignItems: 'center',
  },
  selectButtonText: {
    fontSize: FONT_SIZES.md,
    fontWeight: '600',
    color: COLORS.text,
  },
  benefitsSection: {
    backgroundColor: COLORS.surface,
    borderRadius: BORDER_RADIUS.lg,
    padding: SPACING.lg,
    marginBottom: SPACING.lg,
  },
  benefitsTitle: {
    fontSize: FONT_SIZES.lg,
    fontWeight: '600',
    color: COLORS.text,
    marginBottom: SPACING.lg,
  },
  benefitItem: {
    flexDirection: 'row',
    alignItems: 'center',
    marginBottom: SPACING.md,
  },
  benefitText: {
    marginLeft: SPACING.md,
  },
  benefitName: {
    fontSize: FONT_SIZES.md,
    fontWeight: '600',
    color: COLORS.text,
  },
  benefitDesc: {
    fontSize: FONT_SIZES.sm,
    color: COLORS.textSecondary,
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
    maxHeight: '85%',
  },
  modalHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: SPACING.lg,
  },
  modalTitle: {
    fontSize: FONT_SIZES.xl,
    fontWeight: '700',
    color: COLORS.text,
  },
  paymentMethods: {
    flexDirection: 'row',
    marginBottom: SPACING.lg,
    gap: SPACING.md,
  },
  paymentMethodButton: {
    flex: 1,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    padding: SPACING.md,
    borderRadius: BORDER_RADIUS.lg,
    backgroundColor: COLORS.surface,
    borderWidth: 1,
    borderColor: COLORS.border,
  },
  paymentMethodActive: {
    borderColor: COLORS.primary,
    backgroundColor: COLORS.primary + '15',
  },
  paymentMethodText: {
    fontSize: FONT_SIZES.md,
    fontWeight: '600',
    color: COLORS.textMuted,
    marginLeft: SPACING.sm,
  },
  paymentMethodTextActive: {
    color: COLORS.primary,
  },
  sectionLabel: {
    fontSize: FONT_SIZES.sm,
    fontWeight: '600',
    color: COLORS.textSecondary,
    marginBottom: SPACING.sm,
  },
  chainSelector: {
    marginBottom: SPACING.lg,
  },
  chainButton: {
    paddingHorizontal: SPACING.md,
    paddingVertical: SPACING.sm,
    borderRadius: BORDER_RADIUS.full,
    backgroundColor: COLORS.surface,
    marginRight: SPACING.sm,
    borderWidth: 1,
    borderColor: COLORS.border,
  },
  chainButtonActive: {
    backgroundColor: COLORS.primary + '20',
    borderColor: COLORS.primary,
  },
  chainButtonText: {
    fontSize: FONT_SIZES.sm,
    color: COLORS.textMuted,
    fontWeight: '500',
  },
  chainButtonTextActive: {
    color: COLORS.primary,
  },
  tokenSelector: {
    flexDirection: 'row',
    marginBottom: SPACING.lg,
    gap: SPACING.sm,
  },
  tokenButton: {
    flex: 1,
    padding: SPACING.md,
    borderRadius: BORDER_RADIUS.md,
    backgroundColor: COLORS.surface,
    alignItems: 'center',
    borderWidth: 1,
    borderColor: COLORS.border,
  },
  tokenButtonActive: {
    borderColor: COLORS.primary,
    backgroundColor: COLORS.primary + '15',
  },
  tokenButtonText: {
    fontSize: FONT_SIZES.md,
    fontWeight: '600',
    color: COLORS.textMuted,
  },
  tokenButtonTextActive: {
    color: COLORS.primary,
  },
  discountBadge: {
    position: 'absolute',
    top: -8,
    right: -8,
    backgroundColor: COLORS.thronosGold,
    paddingHorizontal: SPACING.xs,
    paddingVertical: 2,
    borderRadius: BORDER_RADIUS.sm,
  },
  discountText: {
    fontSize: FONT_SIZES.xs,
    fontWeight: '700',
    color: COLORS.background,
  },
  priceSummary: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    backgroundColor: COLORS.surface,
    padding: SPACING.md,
    borderRadius: BORDER_RADIUS.md,
    marginBottom: SPACING.lg,
  },
  summaryLabel: {
    fontSize: FONT_SIZES.md,
    color: COLORS.textSecondary,
  },
  summaryValue: {
    fontSize: FONT_SIZES.xl,
    fontWeight: '700',
    color: COLORS.text,
  },
  payButton: {
    borderRadius: BORDER_RADIUS.lg,
    overflow: 'hidden',
    marginBottom: SPACING.md,
  },
  payButtonGradient: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    padding: SPACING.md + 4,
  },
  payButtonText: {
    fontSize: FONT_SIZES.lg,
    fontWeight: '600',
    color: COLORS.text,
    marginLeft: SPACING.sm,
  },
  fiatInfo: {
    alignItems: 'center',
    padding: SPACING.xl,
  },
  fiatTitle: {
    fontSize: FONT_SIZES.lg,
    fontWeight: '600',
    color: COLORS.text,
    marginTop: SPACING.md,
    textAlign: 'center',
  },
  fiatDesc: {
    fontSize: FONT_SIZES.md,
    color: COLORS.textSecondary,
    textAlign: 'center',
    marginTop: SPACING.sm,
  },
  securityNote: {
    fontSize: FONT_SIZES.xs,
    color: COLORS.textMuted,
    textAlign: 'center',
  },
});
