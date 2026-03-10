// Trader Sentinel Configuration
// Thronos Integration & API Settings


const _toFiniteNumber = (value: string | undefined, fallback: number): number => {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
};

const _toTierLimit = (value: string | undefined, fallback: number): number => {
  if (!value) return fallback;
  const normalized = value.trim().toLowerCase();
  if (normalized === 'infinity' || normalized === 'inf' || normalized === '∞') {
    return Number.POSITIVE_INFINITY;
  }
  return _toFiniteNumber(value, fallback);
};

export const CONFIG = {
  // Backend API (Railway → custom domain sentinel.thronoschain.org)
  API_URL: process.env.EXPO_PUBLIC_API_URL ?? 'https://sentinel.thronoschain.org',

  // LLM Analyst service (Railway)
  ANALYST_URL: process.env.EXPO_PUBLIC_ANALYST_URL ?? 'https://sentinel-analyst.up.railway.app',

  // Neural Prediction Brain service (Railway)
  BRAIN_URL: process.env.EXPO_PUBLIC_BRAIN_URL ?? 'https://alanisys.up.railway.app',

  // Thronos Gateway - Payment & Rewards (routes through blockchain)
  THRONOS_GATEWAY_URL: process.env.EXPO_PUBLIC_THRONOS_GATEWAY ?? 'https://api.thronoschain.org',
  THRONOS_REWARDS_CONTRACT: '0x...', // Thronos Rewards Contract

  // Thronos native chain API (blockchain-verified subscriptions)
  THRONOS_CHAIN_URL: process.env.EXPO_PUBLIC_THRONOS_CHAIN ?? 'https://api.thronoschain.org',

  // Treasury addresses per network — fees collected here
  TREASURY_ADDRESSES: {
    THRONOS: process.env.EXPO_PUBLIC_TREASURY_THR ?? 'THR_SENTINEL_TREASURY_V1',
    ETHEREUM: process.env.EXPO_PUBLIC_TREASURY_ETH ?? '',
    BSC: process.env.EXPO_PUBLIC_TREASURY_BSC ?? '',
    POLYGON: process.env.EXPO_PUBLIC_TREASURY_POLYGON ?? '',
    ARBITRUM: process.env.EXPO_PUBLIC_TREASURY_ARB ?? '',
    AVALANCHE: process.env.EXPO_PUBLIC_TREASURY_AVAX ?? '',
    BASE: process.env.EXPO_PUBLIC_TREASURY_BASE ?? '',
    SOLANA: process.env.EXPO_PUBLIC_TREASURY_SOL ?? '',
  },

  // Subscription fee split
  FEE_SPLIT: {
    TREASURY_SHARE: 0.50,  // 50% to treasury
    BURN_SHARE: 0.25,      // 25% burned (deflation)
    LP_REWARDS_SHARE: 0.25, // 25% to LP rewards pool
  },

  // Supported Networks for Crosschain Payments & Wallet
  SUPPORTED_CHAINS: {
    THRONOS: {
      chainId: 'thronos',
      name: 'Thronos Chain',
      symbol: 'THR',
      rpcUrl: process.env.EXPO_PUBLIC_THRONOS_CHAIN ?? 'https://api.thronoschain.org',
      explorerUrl: 'https://explorer.thronoschain.org',
      family: 'thronos',
    },
    BTC: {
      chainId: 'btc',
      name: 'Bitcoin',
      symbol: 'BTC',
      rpcUrl: '',
      explorerUrl: 'https://mempool.space',
      family: 'btc',
    },
    ETHEREUM: {
      chainId: 1,
      name: 'Ethereum',
      symbol: 'ETH',
      rpcUrl: 'https://rpc.ankr.com/eth',
      explorerUrl: 'https://etherscan.io',
      family: 'evm',
    },
    BSC: {
      chainId: 56,
      name: 'BNB Smart Chain',
      symbol: 'BNB',
      rpcUrl: 'https://bsc-dataseed.binance.org',
      explorerUrl: 'https://bscscan.com',
      family: 'evm',
    },
    POLYGON: {
      chainId: 137,
      name: 'Polygon',
      symbol: 'MATIC',
      rpcUrl: 'https://polygon-rpc.com',
      explorerUrl: 'https://polygonscan.com',
      family: 'evm',
    },
    ARBITRUM: {
      chainId: 42161,
      name: 'Arbitrum',
      symbol: 'ETH',
      rpcUrl: 'https://arb1.arbitrum.io/rpc',
      explorerUrl: 'https://arbiscan.io',
      family: 'evm',
    },
    AVALANCHE: {
      chainId: 43114,
      name: 'Avalanche',
      symbol: 'AVAX',
      rpcUrl: 'https://api.avax.network/ext/bc/C/rpc',
      explorerUrl: 'https://snowtrace.io',
      family: 'evm',
    },
    BASE: {
      chainId: 8453,
      name: 'Base',
      symbol: 'ETH',
      rpcUrl: 'https://mainnet.base.org',
      explorerUrl: 'https://basescan.org',
      family: 'evm',
    },
    XRP: {
      chainId: 'xrp',
      name: 'XRP Ledger',
      symbol: 'XRP',
      rpcUrl: 'https://xrplcluster.com',
      explorerUrl: 'https://xrpscan.com',
      family: 'xrp',
    },
    SOLANA: {
      chainId: 'solana',
      name: 'Solana',
      symbol: 'SOL',
      rpcUrl: 'https://api.mainnet-beta.solana.com',
      explorerUrl: 'https://solscan.io',
      family: 'solana',
    },
  },

  // Which chains are available per wallet type
  WALLET_CHAINS: {
    thronos: ['THRONOS', 'BTC'],
    evm: ['ETHEREUM', 'BSC', 'POLYGON', 'ARBITRUM', 'AVALANCHE', 'BASE'],
    phantom: ['SOLANA'],
  } as Record<string, string[]>,

  // Supported Payment Tokens per Chain
  PAYMENT_TOKENS: {
    // Thronos native chain — THR direct payment (no ERC20)
    thronos: [
      { symbol: 'THR', address: 'native', decimals: 6 },
    ],
    1: [
      { symbol: 'USDT', address: '0xdAC17F958D2ee523a2206206994597C13D831ec7', decimals: 6 },
      { symbol: 'USDC', address: '0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48', decimals: 6 },
      { symbol: 'THR', address: '0x...', decimals: 18 },
    ],
    56: [
      { symbol: 'USDT', address: '0x55d398326f99059fF775485246999027B3197955', decimals: 18 },
      { symbol: 'USDC', address: '0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d', decimals: 18 },
      { symbol: 'BUSD', address: '0xe9e7CEA3DedcA5984780Bafc599bD69ADd087D56', decimals: 18 },
      { symbol: 'THR', address: '0x...', decimals: 18 },
    ],
    137: [
      { symbol: 'USDT', address: '0xc2132D05D31c914a87C6611C10748AEb04B58e8F', decimals: 6 },
      { symbol: 'USDC', address: '0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174', decimals: 6 },
      { symbol: 'THR', address: '0x...', decimals: 18 },
    ],
    42161: [
      { symbol: 'USDT', address: '0xFd086bC7CD5C481DCC9C85ebE478A1C0b69FCbb9', decimals: 6 },
      { symbol: 'USDC', address: '0xFF970A61A04b1cA14834A43f5dE4533eBDDB5CC8', decimals: 6 },
      { symbol: 'THR', address: '0x...', decimals: 18 },
    ],
  },

  // Subscription Packages — what each tier ACTUALLY unlocks
  PACKAGES: {
    STARTER: {
      id: 'starter',
      name: 'Starter',
      priceUSD: 29,
      priceTHR: 25,
      features: [
        'BTC + ETH directional signals',
        'Cross-exchange arbitrage (all pairs)',
        'AI Risk Monitor (Francis)',
        'Technical analysis (RSI, MACD, Bollinger)',
        'Push notifications',
        '15s signal refresh rate',
        '2 directional signals at a time',
      ],
      rewardsMultiplier: 1.0,
    },
    PRO: {
      id: 'pro',
      name: 'Pro',
      priceUSD: 99,
      priceTHR: 79,
      popular: true,
      features: [
        'All 24+ trading pairs unlocked',
        'New coin / early listing alerts',
        'AI Strategy Advisor (Analyst)',
        'Neural Brain — personal trade model',
        'Futures trade history sync',
        'Live position monitoring (15min)',
        'Custom watchlist with Sentinel alerts',
        '12s signal refresh · 5 directional signals',
        'Telegram premium signals',
      ],
      rewardsMultiplier: 1.5,
    },
    ELITE: {
      id: 'elite',
      name: 'Elite',
      priceUSD: 299,
      priceTHR: 229,
      features: [
        'Everything in Pro',
        'AutoTrader — AI-powered execution',
        'Sleep Mode (24/7 autonomous trading)',
        'Multi-exchange portfolio tracking',
        'Geopolitical risk intelligence',
        'Advanced risk alerts & stop-loss AI',
        '9s refresh · 10 directional signals',
        'Priority 24/7 support',
        '2.5x THR rewards multiplier',
        'Liquidity pool bonus rewards',
      ],
      rewardsMultiplier: 2.5,
    },
    WHALE: {
      id: 'whale',
      name: 'Whale',
      priceUSD: 999,
      priceTHR: 749,
      features: [
        'Everything in Elite',
        'Unlimited directional signals',
        '7s fastest refresh rate',
        'Personal AI trading assistant',
        'Custom strategy development',
        'Revenue sharing from platform fees',
        'Governance voting rights',
        '5x THR rewards multiplier',
        'Direct line to dev team',
        'Early access to all new features',
      ],
      rewardsMultiplier: 5.0,
    },
  },

  // Rewards Configuration (all amounts in THR)
  REWARDS: {
    REFERRAL_BONUS: 50,        // 50 THR per referral
    DAILY_LOGIN_BONUS: 1,      // 1 THR daily
    TRADE_SIGNAL_USAGE: 0.5,   // 0.5 THR per signal used
    LIQUIDITY_PROVISION_APY: 0.12, // 12% on LP
    STAKING_APY: 0.08,         // 8% on staking
  },

  // WalletConnect Configuration
  WALLETCONNECT_PROJECT_ID: 'b4954b0dc4eb9832e0f03ef0f25cc744',

  // App Settings
  APP_NAME: 'Pytheia — Trader Sentinel',
  APP_VERSION: '1.1.0',
  SUPPORT_EMAIL: 'support@thronoschain.org',


  // Subscription tier limits (single source of truth)
  TIER_LIMITS: {
    free: _toTierLimit(process.env.EXPO_PUBLIC_TIER_LIMIT_FREE, 1),
    starter: _toTierLimit(process.env.EXPO_PUBLIC_TIER_LIMIT_STARTER, 5),
    pro: _toTierLimit(process.env.EXPO_PUBLIC_TIER_LIMIT_PRO, 10),
    elite: _toTierLimit(process.env.EXPO_PUBLIC_TIER_LIMIT_ELITE, 15),
    whale: _toTierLimit(process.env.EXPO_PUBLIC_TIER_LIMIT_WHALE, Number.POSITIVE_INFINITY),
  },

  // Public treasury addresses only (safe for Expo public env)
  TREASURY_ADDRESSES: {
    ETH: process.env.EXPO_PUBLIC_TREASURY_ETH ?? '',
    BSC: process.env.EXPO_PUBLIC_TREASURY_BSC ?? '',
    POLYGON: process.env.EXPO_PUBLIC_TREASURY_POLYGON ?? '',
    ARBITRUM: process.env.EXPO_PUBLIC_TREASURY_ARBITRUM ?? '',
    BASE: process.env.EXPO_PUBLIC_TREASURY_BASE ?? '',
    SOLANA: process.env.EXPO_PUBLIC_TREASURY_SOLANA ?? '',
  },


  // Subscription tier limits (single source of truth)
  TIER_LIMITS: {
    free: _toTierLimit(process.env.EXPO_PUBLIC_TIER_LIMIT_FREE, 1),
    starter: _toTierLimit(process.env.EXPO_PUBLIC_TIER_LIMIT_STARTER, 5),
    pro: _toTierLimit(process.env.EXPO_PUBLIC_TIER_LIMIT_PRO, 10),
    elite: _toTierLimit(process.env.EXPO_PUBLIC_TIER_LIMIT_ELITE, 15),
    whale: _toTierLimit(process.env.EXPO_PUBLIC_TIER_LIMIT_WHALE, Number.POSITIVE_INFINITY),
  },

  // Public treasury addresses only (safe for Expo public env)
  TREASURY_ADDRESSES: {
    ETH: process.env.EXPO_PUBLIC_TREASURY_ETH ?? '',
    BSC: process.env.EXPO_PUBLIC_TREASURY_BSC ?? '',
    POLYGON: process.env.EXPO_PUBLIC_TREASURY_POLYGON ?? '',
    ARBITRUM: process.env.EXPO_PUBLIC_TREASURY_ARBITRUM ?? '',
    BASE: process.env.EXPO_PUBLIC_TREASURY_BASE ?? '',
    SOLANA: process.env.EXPO_PUBLIC_TREASURY_SOLANA ?? '',
  },


  // Subscription tier limits (single source of truth)
  TIER_LIMITS: {
    free: _toTierLimit(process.env.EXPO_PUBLIC_TIER_LIMIT_FREE, 1),
    starter: _toTierLimit(process.env.EXPO_PUBLIC_TIER_LIMIT_STARTER, 5),
    pro: _toTierLimit(process.env.EXPO_PUBLIC_TIER_LIMIT_PRO, 10),
    elite: _toTierLimit(process.env.EXPO_PUBLIC_TIER_LIMIT_ELITE, 15),
    whale: _toTierLimit(process.env.EXPO_PUBLIC_TIER_LIMIT_WHALE, Number.POSITIVE_INFINITY),
  },

  // Public treasury addresses only (safe for Expo public env)
  TREASURY_ADDRESSES: {
    ETH: process.env.EXPO_PUBLIC_TREASURY_ETH ?? '',
    BSC: process.env.EXPO_PUBLIC_TREASURY_BSC ?? '',
    POLYGON: process.env.EXPO_PUBLIC_TREASURY_POLYGON ?? '',
    ARBITRUM: process.env.EXPO_PUBLIC_TREASURY_ARBITRUM ?? '',
    BASE: process.env.EXPO_PUBLIC_TREASURY_BASE ?? '',
    SOLANA: process.env.EXPO_PUBLIC_TREASURY_SOLANA ?? '',
  },


  // Subscription tier limits (single source of truth)
  TIER_LIMITS: {
    free: _toTierLimit(process.env.EXPO_PUBLIC_TIER_LIMIT_FREE, 1),
    starter: _toTierLimit(process.env.EXPO_PUBLIC_TIER_LIMIT_STARTER, 5),
    pro: _toTierLimit(process.env.EXPO_PUBLIC_TIER_LIMIT_PRO, 10),
    elite: _toTierLimit(process.env.EXPO_PUBLIC_TIER_LIMIT_ELITE, 15),
    whale: _toTierLimit(process.env.EXPO_PUBLIC_TIER_LIMIT_WHALE, Number.POSITIVE_INFINITY),
  },

  // Public treasury addresses only (safe for Expo public env)
  TREASURY_ADDRESSES: {
    ETH: process.env.EXPO_PUBLIC_TREASURY_ETH ?? '',
    BSC: process.env.EXPO_PUBLIC_TREASURY_BSC ?? '',
    POLYGON: process.env.EXPO_PUBLIC_TREASURY_POLYGON ?? '',
    ARBITRUM: process.env.EXPO_PUBLIC_TREASURY_ARBITRUM ?? '',
    BASE: process.env.EXPO_PUBLIC_TREASURY_BASE ?? '',
    SOLANA: process.env.EXPO_PUBLIC_TREASURY_SOLANA ?? '',
  },


  // Subscription tier limits (single source of truth)
  TIER_LIMITS: {
    free: _toTierLimit(process.env.EXPO_PUBLIC_TIER_LIMIT_FREE, 1),
    starter: _toTierLimit(process.env.EXPO_PUBLIC_TIER_LIMIT_STARTER, 5),
    pro: _toTierLimit(process.env.EXPO_PUBLIC_TIER_LIMIT_PRO, 10),
    elite: _toTierLimit(process.env.EXPO_PUBLIC_TIER_LIMIT_ELITE, 15),
    whale: _toTierLimit(process.env.EXPO_PUBLIC_TIER_LIMIT_WHALE, Number.POSITIVE_INFINITY),
  },

  // Public treasury addresses only (safe for Expo public env)
  TREASURY_ADDRESSES: {
    ETH: process.env.EXPO_PUBLIC_TREASURY_ETH ?? '',
    BSC: process.env.EXPO_PUBLIC_TREASURY_BSC ?? '',
    POLYGON: process.env.EXPO_PUBLIC_TREASURY_POLYGON ?? '',
    ARBITRUM: process.env.EXPO_PUBLIC_TREASURY_ARBITRUM ?? '',
    BASE: process.env.EXPO_PUBLIC_TREASURY_BASE ?? '',
    SOLANA: process.env.EXPO_PUBLIC_TREASURY_SOLANA ?? '',
  },

  // API security — set this to the same value as API_KEY env var on each service
  API_KEY: process.env.EXPO_PUBLIC_API_KEY ?? '',
};

export default CONFIG;
