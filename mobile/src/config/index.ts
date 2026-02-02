// Trader Sentinel Configuration
// Thronos Integration & API Settings

export const CONFIG = {
  // Backend API
  API_URL: 'https://trader-sentinel.onrender.com',

  // Thronos Gateway - Payment & Rewards
  THRONOS_GATEWAY_URL: 'https://gateway.thronos.io',
  THRONOS_REWARDS_CONTRACT: '0x...', // Thronos Rewards Contract

  // Supported Networks for Crosschain Payments
  SUPPORTED_CHAINS: {
    ETHEREUM: {
      chainId: 1,
      name: 'Ethereum',
      symbol: 'ETH',
      rpcUrl: 'https://eth.llamarpc.com',
      explorerUrl: 'https://etherscan.io',
    },
    BSC: {
      chainId: 56,
      name: 'BNB Smart Chain',
      symbol: 'BNB',
      rpcUrl: 'https://bsc-dataseed.binance.org',
      explorerUrl: 'https://bscscan.com',
    },
    POLYGON: {
      chainId: 137,
      name: 'Polygon',
      symbol: 'MATIC',
      rpcUrl: 'https://polygon-rpc.com',
      explorerUrl: 'https://polygonscan.com',
    },
    ARBITRUM: {
      chainId: 42161,
      name: 'Arbitrum',
      symbol: 'ETH',
      rpcUrl: 'https://arb1.arbitrum.io/rpc',
      explorerUrl: 'https://arbiscan.io',
    },
    AVALANCHE: {
      chainId: 43114,
      name: 'Avalanche',
      symbol: 'AVAX',
      rpcUrl: 'https://api.avax.network/ext/bc/C/rpc',
      explorerUrl: 'https://snowtrace.io',
    },
    BASE: {
      chainId: 8453,
      name: 'Base',
      symbol: 'ETH',
      rpcUrl: 'https://mainnet.base.org',
      explorerUrl: 'https://basescan.org',
    },
    SOLANA: {
      chainId: 'solana',
      name: 'Solana',
      symbol: 'SOL',
      rpcUrl: 'https://api.mainnet-beta.solana.com',
      explorerUrl: 'https://solscan.io',
    },
  },

  // Supported Payment Tokens per Chain
  PAYMENT_TOKENS: {
    1: [ // Ethereum
      { symbol: 'USDT', address: '0xdAC17F958D2ee523a2206206994597C13D831ec7', decimals: 6 },
      { symbol: 'USDC', address: '0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48', decimals: 6 },
      { symbol: 'DAI', address: '0x6B175474E89094C44Da98b954EesDAFAE6Aceb', decimals: 18 },
      { symbol: 'THRONOS', address: '0x...', decimals: 18 },
    ],
    56: [ // BSC
      { symbol: 'USDT', address: '0x55d398326f99059fF775485246999027B3197955', decimals: 18 },
      { symbol: 'USDC', address: '0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d', decimals: 18 },
      { symbol: 'BUSD', address: '0xe9e7CEA3DedcA5984780Bafc599bD69ADd087D56', decimals: 18 },
      { symbol: 'THRONOS', address: '0x...', decimals: 18 },
    ],
    137: [ // Polygon
      { symbol: 'USDT', address: '0xc2132D05D31c914a87C6611C10748AEb04B58e8F', decimals: 6 },
      { symbol: 'USDC', address: '0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174', decimals: 6 },
      { symbol: 'THRONOS', address: '0x...', decimals: 18 },
    ],
    42161: [ // Arbitrum
      { symbol: 'USDT', address: '0xFd086bC7CD5C481DCC9C85ebE478A1C0b69FCbb9', decimals: 6 },
      { symbol: 'USDC', address: '0xFF970A61A04b1cA14834A43f5dE4533eBDDB5CC8', decimals: 6 },
      { symbol: 'THRONOS', address: '0x...', decimals: 18 },
    ],
  },

  // Subscription Packages
  PACKAGES: {
    STARTER: {
      id: 'starter',
      name: 'Starter',
      priceUSD: 29,
      priceThronos: 25, // Discount with THRONOS token
      features: [
        'Real-time market signals',
        'Basic arbitrage alerts',
        '5 trading pairs',
        'Email notifications',
      ],
      rewardsMultiplier: 1.0,
    },
    PRO: {
      id: 'pro',
      name: 'Pro',
      priceUSD: 99,
      priceThronos: 79,
      features: [
        'All Starter features',
        'Advanced arbitrage detection',
        'Unlimited trading pairs',
        'Push notifications',
        'Priority support',
        'API access',
      ],
      rewardsMultiplier: 1.5,
    },
    ELITE: {
      id: 'elite',
      name: 'Elite',
      priceUSD: 299,
      priceThronos: 229,
      features: [
        'All Pro features',
        'Custom alerts',
        'Trading bot integration',
        'Exclusive signals',
        '24/7 support',
        'Early access to features',
        'Liquidity pool rewards',
      ],
      rewardsMultiplier: 2.5,
    },
    WHALE: {
      id: 'whale',
      name: 'Whale',
      priceUSD: 999,
      priceThronos: 749,
      features: [
        'All Elite features',
        'Personal trading assistant',
        'Custom strategy development',
        'Direct line to developers',
        'Governance voting rights',
        'Maximum liquidity rewards',
        'Revenue sharing',
      ],
      rewardsMultiplier: 5.0,
    },
  },

  // Rewards Configuration
  REWARDS: {
    // Earn rewards for activity
    REFERRAL_BONUS: 50, // THRONOS tokens
    DAILY_LOGIN_BONUS: 1,
    TRADE_SIGNAL_USAGE: 0.5,
    LIQUIDITY_PROVISION_APY: 0.12, // 12% APY
    STAKING_APY: 0.08, // 8% APY
  },

  // WalletConnect Configuration
  WALLETCONNECT_PROJECT_ID: 'YOUR_WALLETCONNECT_PROJECT_ID',

  // App Settings
  APP_NAME: 'Trader Sentinel',
  APP_VERSION: '1.0.0',
  SUPPORT_EMAIL: 'support@thronos.io',
};

export default CONFIG;
