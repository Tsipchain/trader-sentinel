import React from 'react';
import {
  View,
  Text,
  StyleSheet,
  ScrollView,
  TouchableOpacity,
  Switch,
  Alert,
  Linking,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useNavigation } from '@react-navigation/native';
import { NativeStackNavigationProp } from '@react-navigation/native-stack';
import { Ionicons } from '@expo/vector-icons';
import { RootStackParamList } from '../../App';
import { COLORS, SPACING, FONT_SIZES, BORDER_RADIUS } from '../constants/theme';
import { useStore } from '../store/useStore';
import { CONFIG } from '../config';

type NavigationProp = NativeStackNavigationProp<RootStackParamList>;

interface SettingItemProps {
  icon: keyof typeof Ionicons.glyphMap;
  iconColor?: string;
  title: string;
  subtitle?: string;
  rightElement?: React.ReactNode;
  onPress?: () => void;
}

const SettingItem = ({ icon, iconColor = COLORS.primary, title, subtitle, rightElement, onPress }: SettingItemProps) => (
  <TouchableOpacity
    style={styles.settingItem}
    onPress={onPress}
    disabled={!onPress && !rightElement}
  >
    <View style={[styles.settingIcon, { backgroundColor: iconColor + '20' }]}>
      <Ionicons name={icon} size={22} color={iconColor} />
    </View>
    <View style={styles.settingContent}>
      <Text style={styles.settingTitle}>{title}</Text>
      {subtitle && <Text style={styles.settingSubtitle}>{subtitle}</Text>}
    </View>
    {rightElement || (
      onPress && <Ionicons name="chevron-forward" size={20} color={COLORS.textMuted} />
    )}
  </TouchableOpacity>
);

export default function SettingsScreen() {
  const navigation = useNavigation<NavigationProp>();
  const { settings, updateSettings, wallet, subscription, logout } = useStore();

  const handleDisconnect = () => {
    Alert.alert(
      'Disconnect Wallet',
      'Are you sure you want to disconnect your wallet?',
      [
        { text: 'Cancel', style: 'cancel' },
        {
          text: 'Disconnect',
          style: 'destructive',
          onPress: logout,
        },
      ]
    );
  };

  const handleSupport = () => {
    Linking.openURL(`mailto:${CONFIG.SUPPORT_EMAIL}`);
  };

  const handlePrivacyPolicy = () => {
    Linking.openURL('https://thronos.io/privacy');
  };

  const handleTerms = () => {
    Linking.openURL('https://thronos.io/terms');
  };

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      <ScrollView
        style={styles.scrollView}
        showsVerticalScrollIndicator={false}
      >
        {/* Header */}
        <View style={styles.header}>
          <Text style={styles.title}>Settings</Text>
        </View>

        {/* Account Section */}
        <View style={styles.section}>
          <Text style={styles.sectionTitle}>Account</Text>

          <SettingItem
            icon="wallet"
            title="Connected Wallet"
            subtitle={wallet.address ? `${wallet.address.slice(0, 6)}...${wallet.address.slice(-4)}` : 'Not connected'}
          />

          <SettingItem
            icon="diamond"
            iconColor={COLORS.thronosPurple}
            title="Subscription"
            subtitle={subscription.toUpperCase()}
            onPress={() => navigation.navigate('Subscription')}
          />

          <SettingItem
            icon="gift"
            iconColor={COLORS.thronosGold}
            title="Rewards"
            subtitle="View and claim your rewards"
            onPress={() => navigation.navigate('Rewards')}
          />

          <SettingItem
            icon="water"
            iconColor={COLORS.accent}
            title="Liquidity Pools"
            subtitle="Manage your positions"
            onPress={() => navigation.navigate('Liquidity')}
          />
        </View>

        {/* Notifications Section */}
        <View style={styles.section}>
          <Text style={styles.sectionTitle}>Notifications</Text>

          <SettingItem
            icon="notifications"
            title="Push Notifications"
            subtitle="Get alerts for signals"
            rightElement={
              <Switch
                value={settings.notifications}
                onValueChange={(value) => updateSettings({ notifications: value })}
                trackColor={{ false: COLORS.border, true: COLORS.primary }}
                thumbColor={COLORS.text}
              />
            }
          />

          <SettingItem
            icon="volume-high"
            iconColor={COLORS.warning}
            title="Sound Alerts"
            subtitle="Play sound for important signals"
            rightElement={
              <Switch
                value={settings.soundAlerts}
                onValueChange={(value) => updateSettings({ soundAlerts: value })}
                trackColor={{ false: COLORS.border, true: COLORS.primary }}
                thumbColor={COLORS.text}
              />
            }
          />

          <SettingItem
            icon="phone-portrait"
            iconColor={COLORS.success}
            title="Haptic Feedback"
            subtitle="Vibrate on new signals"
            rightElement={
              <Switch
                value={settings.hapticFeedback}
                onValueChange={(value) => updateSettings({ hapticFeedback: value })}
                trackColor={{ false: COLORS.border, true: COLORS.primary }}
                thumbColor={COLORS.text}
              />
            }
          />
        </View>

        {/* Preferences Section */}
        <View style={styles.section}>
          <Text style={styles.sectionTitle}>Preferences</Text>

          <SettingItem
            icon="moon"
            iconColor={COLORS.primary}
            title="Dark Mode"
            subtitle="Always on"
            rightElement={
              <View style={styles.badge}>
                <Text style={styles.badgeText}>ON</Text>
              </View>
            }
          />

          <SettingItem
            icon="cash"
            iconColor={COLORS.success}
            title="Currency"
            subtitle={settings.currency}
            onPress={() => {
              // Could show currency picker
            }}
          />

          <SettingItem
            icon="language"
            title="Language"
            subtitle="English"
            onPress={() => {
              // Could show language picker
            }}
          />
        </View>

        {/* About Section */}
        <View style={styles.section}>
          <Text style={styles.sectionTitle}>About</Text>

          <SettingItem
            icon="information-circle"
            title="App Version"
            subtitle={CONFIG.APP_VERSION}
          />

          <SettingItem
            icon="document-text"
            title="Terms of Service"
            onPress={handleTerms}
          />

          <SettingItem
            icon="shield-checkmark"
            iconColor={COLORS.success}
            title="Privacy Policy"
            onPress={handlePrivacyPolicy}
          />

          <SettingItem
            icon="help-circle"
            iconColor={COLORS.info}
            title="Help & Support"
            onPress={handleSupport}
          />
        </View>

        {/* Danger Zone */}
        <View style={styles.section}>
          <TouchableOpacity
            style={styles.disconnectButton}
            onPress={handleDisconnect}
          >
            <Ionicons name="log-out" size={20} color={COLORS.error} />
            <Text style={styles.disconnectText}>Disconnect Wallet</Text>
          </TouchableOpacity>
        </View>

        {/* Footer */}
        <View style={styles.footer}>
          <Text style={styles.footerText}>
            {CONFIG.APP_NAME} v{CONFIG.APP_VERSION}
          </Text>
          <Text style={styles.footerText}>Powered by Thronos</Text>
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
    paddingVertical: SPACING.md,
  },
  title: {
    fontSize: FONT_SIZES.xxl,
    fontWeight: '700',
    color: COLORS.text,
  },
  section: {
    marginBottom: SPACING.lg,
  },
  sectionTitle: {
    fontSize: FONT_SIZES.sm,
    fontWeight: '600',
    color: COLORS.textMuted,
    marginBottom: SPACING.md,
    textTransform: 'uppercase',
    letterSpacing: 1,
  },
  settingItem: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: COLORS.surface,
    borderRadius: BORDER_RADIUS.lg,
    padding: SPACING.md,
    marginBottom: SPACING.sm,
    borderWidth: 1,
    borderColor: COLORS.border,
  },
  settingIcon: {
    width: 40,
    height: 40,
    borderRadius: BORDER_RADIUS.md,
    justifyContent: 'center',
    alignItems: 'center',
    marginRight: SPACING.md,
  },
  settingContent: {
    flex: 1,
  },
  settingTitle: {
    fontSize: FONT_SIZES.md,
    fontWeight: '600',
    color: COLORS.text,
  },
  settingSubtitle: {
    fontSize: FONT_SIZES.sm,
    color: COLORS.textSecondary,
    marginTop: 2,
  },
  badge: {
    backgroundColor: COLORS.primary,
    paddingHorizontal: SPACING.sm,
    paddingVertical: 2,
    borderRadius: BORDER_RADIUS.sm,
  },
  badgeText: {
    fontSize: FONT_SIZES.xs,
    fontWeight: '600',
    color: COLORS.text,
  },
  disconnectButton: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: COLORS.error + '15',
    borderRadius: BORDER_RADIUS.lg,
    padding: SPACING.md,
    borderWidth: 1,
    borderColor: COLORS.error + '30',
  },
  disconnectText: {
    fontSize: FONT_SIZES.md,
    fontWeight: '600',
    color: COLORS.error,
    marginLeft: SPACING.sm,
  },
  footer: {
    alignItems: 'center',
    paddingVertical: SPACING.xl,
  },
  footerText: {
    fontSize: FONT_SIZES.sm,
    color: COLORS.textMuted,
    marginBottom: SPACING.xs,
  },
  bottomSpacing: {
    height: SPACING.xxl,
  },
});
