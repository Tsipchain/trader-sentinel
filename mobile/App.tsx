import 'react-native-get-random-values';
import React, { useEffect, useState } from 'react';
import { StatusBar } from 'expo-status-bar';
import { NavigationContainer } from '@react-navigation/native';
import { createNativeStackNavigator } from '@react-navigation/native-stack';
import { createBottomTabNavigator } from '@react-navigation/bottom-tabs';
import { View, ActivityIndicator, StyleSheet } from 'react-native';
import { SafeAreaProvider } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import * as SplashScreen from 'expo-splash-screen';

// Screens
import WelcomeScreen from './src/screens/WelcomeScreen';
import ConnectWalletScreen from './src/screens/ConnectWalletScreen';
import DashboardScreen from './src/screens/DashboardScreen';
import SignalsScreen from './src/screens/SignalsScreen';
import WalletScreen from './src/screens/WalletScreen';
import SettingsScreen from './src/screens/SettingsScreen';
import SubscriptionScreen from './src/screens/SubscriptionScreen';
import RewardsScreen from './src/screens/RewardsScreen';
import LiquidityScreen from './src/screens/LiquidityScreen';
import RiskScreen from './src/screens/RiskScreen';
import AutoTraderScreen from './src/screens/AutoTraderScreen';
import HistoryScreen from './src/screens/HistoryScreen';
import ChartScreen from './src/screens/ChartScreen';

// Store
import { useStore } from './src/store/useStore';
import { COLORS } from './src/constants/theme';

// Types
export type RootStackParamList = {
  Welcome: undefined;
  ConnectWallet: undefined;
  MainTabs: undefined;
  Subscription: undefined;
  Rewards: undefined;
  Liquidity: undefined;
  Chart: { symbol: string };
};

export type TabParamList = {
  Dashboard: undefined;
  Signals: undefined;
  AutoTrader: undefined;
  History: undefined;
  Risk: undefined;
  Wallet: undefined;
  Settings: undefined;
};

const Stack = createNativeStackNavigator<RootStackParamList>();
const Tab = createBottomTabNavigator<TabParamList>();

SplashScreen.preventAutoHideAsync();

function MainTabs() {
  return (
    <Tab.Navigator
      screenOptions={({ route }) => ({
        tabBarIcon: ({ focused, color, size }) => {
          let iconName: keyof typeof Ionicons.glyphMap = 'home';

          switch (route.name) {
            case 'Dashboard':
              iconName = focused ? 'home' : 'home-outline';
              break;
            case 'Signals':
              iconName = focused ? 'flash' : 'flash-outline';
              break;
            case 'AutoTrader':
              iconName = focused ? 'hardware-chip' : 'hardware-chip-outline';
              break;
            case 'History':
              iconName = focused ? 'time' : 'time-outline';
              break;
            case 'Risk':
              iconName = focused ? 'shield' : 'shield-outline';
              break;
            case 'Wallet':
              iconName = focused ? 'wallet' : 'wallet-outline';
              break;
            case 'Settings':
              iconName = focused ? 'settings' : 'settings-outline';
              break;
          }

          return <Ionicons name={iconName} size={size} color={color} />;
        },
        tabBarActiveTintColor: COLORS.primary,
        tabBarInactiveTintColor: COLORS.textMuted,
        tabBarStyle: {
          backgroundColor: COLORS.backgroundCard,
          borderTopColor: COLORS.border,
          borderTopWidth: 1,
          paddingBottom: 8,
          paddingTop: 8,
          height: 65,
        },
        tabBarLabelStyle: {
          fontSize: 11,
          fontWeight: '500',
        },
        headerStyle: { backgroundColor: COLORS.background },
        headerTintColor: COLORS.text,
        headerTitleStyle: { fontWeight: '600' },
      })}
    >
      <Tab.Screen name="Dashboard" component={DashboardScreen} options={{ title: 'Dashboard' }} />
      <Tab.Screen name="Signals" component={SignalsScreen} options={{ title: 'Signals' }} />
      <Tab.Screen name="AutoTrader" component={AutoTraderScreen} options={{ title: 'AutoTrader' }} />
      <Tab.Screen name="History" component={HistoryScreen} options={{ title: 'History' }} />
      <Tab.Screen name="Risk" component={RiskScreen} options={{ title: 'Risk & AI' }} />
      <Tab.Screen name="Wallet" component={WalletScreen} options={{ title: 'Wallet' }} />
      <Tab.Screen name="Settings" component={SettingsScreen} options={{ title: 'Settings' }} />
    </Tab.Navigator>
  );
}

export default function App() {
  const [isReady, setIsReady] = useState(false);
  const { isAuthenticated, wallet } = useStore();

  useEffect(() => {
    async function prepare() {
      try {
        await new Promise((resolve) => setTimeout(resolve, 1000));
      } catch (e) {
        console.warn(e);
      } finally {
        setIsReady(true);
        await SplashScreen.hideAsync();
      }
    }
    prepare();
  }, []);

  if (!isReady) {
    return (
      <View style={styles.loading}>
        <ActivityIndicator size="large" color={COLORS.primary} />
      </View>
    );
  }

  const isLoggedIn = isAuthenticated && wallet.isConnected;

  return (
    <SafeAreaProvider>
      <NavigationContainer
        theme={{
          dark: true,
          colors: {
            primary: COLORS.primary,
            background: COLORS.background,
            card: COLORS.backgroundCard,
            text: COLORS.text,
            border: COLORS.border,
            notification: COLORS.error,
          },
        }}
      >
        <Stack.Navigator
          screenOptions={{
            headerShown: false,
            contentStyle: { backgroundColor: COLORS.background },
            animation: 'slide_from_right',
          }}
        >
          {!isLoggedIn ? (
            <>
              <Stack.Screen name="Welcome" component={WelcomeScreen} />
              <Stack.Screen name="ConnectWallet" component={ConnectWalletScreen} />
            </>
          ) : (
            <>
              <Stack.Screen name="MainTabs" component={MainTabs} />
              <Stack.Screen
                name="Subscription"
                component={SubscriptionScreen}
                options={{
                  headerShown: true,
                  title: 'Subscription Plans',
                  headerStyle: { backgroundColor: COLORS.background },
                  headerTintColor: COLORS.text,
                  presentation: 'modal',
                }}
              />
              <Stack.Screen
                name="Rewards"
                component={RewardsScreen}
                options={{
                  headerShown: true,
                  title: 'Rewards',
                  headerStyle: { backgroundColor: COLORS.background },
                  headerTintColor: COLORS.text,
                  presentation: 'modal',
                }}
              />
              <Stack.Screen
                name="Liquidity"
                component={LiquidityScreen}
                options={{
                  headerShown: true,
                  title: 'Liquidity Pools',
                  headerStyle: { backgroundColor: COLORS.background },
                  headerTintColor: COLORS.text,
                  presentation: 'modal',
                }}
              />
              <Stack.Screen
                name="Chart"
                component={ChartScreen}
                options={{
                  headerShown: false,
                  animation: 'slide_from_bottom',
                }}
              />
            </>
          )}
        </Stack.Navigator>
        <StatusBar style="light" />
      </NavigationContainer>
    </SafeAreaProvider>
  );
}

const styles = StyleSheet.create({
  loading: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
    backgroundColor: COLORS.background,
  },
});
