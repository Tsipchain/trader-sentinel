// Thronos Integration Service
// Routes ALL subscription payments through the Thronos blockchain
// Supports: THR native payments, EVM crosschain via treasury, Fiat via Stripe

import { ethers } from 'ethers';
import { CONFIG } from '../config';

// ── Types ─────────────────────────────────────────────────────────────────────

export interface PaymentRequest {
  packageId: string;
  chainId: number | string;
  tokenSymbol: string;
  tokenAddress: string;
  amount: string;
  userAddress: string;
}

export interface PaymentResult {
  success: boolean;
  txHash?: string;
  blockchainRef?: string; // Thronos chain reference for verification
  error?: string;
}

export interface RewardsInfo {
  totalEarned: number;
  pendingRewards: number;
  claimableRewards: number;
  stakingRewards: number;
  liquidityRewards: number;
  referralRewards: number;
}

export interface LiquidityPosition {
  poolId: string;
  tokenA: string;
  tokenB: string;
  amountA: string;
  amountB: string;
  lpTokens: string;
  pendingRewards: string;
  apr: number;
}

export interface SubscriptionStatus {
  tier: string;
  expiresAt: number;
  autoRenew: boolean;
  blockchainRef: string;
  paymentChain: string;
  lastPaymentTx: string;
}

// ── EVM ABIs ──────────────────────────────────────────────────────────────────

const ERC20_ABI = [
  'function approve(address spender, uint256 amount) returns (bool)',
  'function allowance(address owner, address spender) view returns (uint256)',
  'function balanceOf(address account) view returns (uint256)',
  'function transfer(address to, uint256 amount) returns (bool)',
  'function decimals() view returns (uint8)',
  'function symbol() view returns (string)',
];

// ── Thronos Service ───────────────────────────────────────────────────────────

class ThronosService {
  private provider: ethers.BrowserProvider | null = null;
  private signer: ethers.Signer | null = null;

  // Initialize with wallet provider (EVM chains)
  async initialize(walletProvider: any): Promise<void> {
    this.provider = new ethers.BrowserProvider(walletProvider);
    this.signer = await this.provider.getSigner();
  }

  async getAddress(): Promise<string> {
    if (!this.signer) throw new Error('Wallet not connected');
    return this.signer.getAddress();
  }

  async getTokenBalance(tokenAddress: string, userAddress: string): Promise<string> {
    if (!this.provider) throw new Error('Provider not initialized');
    const tokenContract = new ethers.Contract(tokenAddress, ERC20_ABI, this.provider);
    const balance = await tokenContract.balanceOf(userAddress);
    const decimals = await tokenContract.decimals();
    return ethers.formatUnits(balance, decimals);
  }

  // ─── BLOCKCHAIN-VERIFIED SUBSCRIPTION PAYMENT ──────────────────────────────
  // All payments route through the Thronos blockchain for verification.
  // Flow: User pays → recorded on payment chain → Thronos chain records subscription → verified

  /**
   * Process subscription payment through Thronos blockchain.
   * 1. If THR native: direct transfer to sentinel treasury on Thronos chain
   * 2. If EVM token: transfer to chain-specific treasury, then register on Thronos chain
   */
  async processPayment(request: PaymentRequest): Promise<PaymentResult> {
    const { chainId, tokenSymbol, packageId, amount, userAddress } = request;

    // THR native payment on Thronos chain
    if (chainId === 'thronos' || tokenSymbol === 'THR' && request.tokenAddress === 'native') {
      return this.processTHRPayment(packageId, amount, userAddress);
    }

    // EVM crosschain payment — send to treasury, then register on Thronos chain
    return this.processEVMPayment(request);
  }

  /**
   * THR native payment — goes directly through the Thronos blockchain.
   * Calls /api/sentinel/subscribe on the Thronos chain node.
   */
  private async processTHRPayment(
    packageId: string,
    amount: string,
    userAddress: string,
  ): Promise<PaymentResult> {
    try {
      const response = await fetch(`${CONFIG.THRONOS_CHAIN_URL}/api/sentinel/subscribe`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          subscriber: userAddress,
          package_id: packageId,
          amount: parseFloat(amount),
          token: 'THR',
          payment_chain: 'thronos',
        }),
      });

      if (!response.ok) {
        const err = await response.json().catch(() => ({}));
        return { success: false, error: err.message || `Payment failed (${response.status})` };
      }

      const data = await response.json();
      return {
        success: true,
        txHash: data.tx_hash,
        blockchainRef: data.blockchain_ref,
      };
    } catch (error: any) {
      return { success: false, error: error.message || 'THR payment failed' };
    }
  }

  /**
   * EVM payment — transfer token to treasury address for that chain,
   * then register the subscription on the Thronos blockchain.
   */
  private async processEVMPayment(request: PaymentRequest): Promise<PaymentResult> {
    if (!this.signer) {
      return { success: false, error: 'EVM wallet not connected' };
    }

    try {
      const { tokenAddress, amount, userAddress, chainId, packageId, tokenSymbol } = request;

      // Find treasury address for this chain
      const chainKey = Object.entries(CONFIG.SUPPORTED_CHAINS).find(
        ([, v]) => v.chainId === chainId,
      )?.[0] as keyof typeof CONFIG.TREASURY_ADDRESSES | undefined;

      const treasuryAddress = chainKey
        ? CONFIG.TREASURY_ADDRESSES[chainKey]
        : '';

      if (!treasuryAddress) {
        return { success: false, error: 'Treasury address not configured for this network' };
      }

      // ERC-20 transfer to treasury
      const tokenContract = new ethers.Contract(tokenAddress, ERC20_ABI, this.signer);
      const decimals = await tokenContract.decimals();
      const amountWei = ethers.parseUnits(amount, decimals);

      // Send tokens to chain-specific treasury
      const tx = await tokenContract.transfer(treasuryAddress, amountWei);
      const receipt = await tx.wait();

      // Register subscription on Thronos blockchain for verification
      const regResult = await this.registerSubscriptionOnChain({
        subscriber: userAddress,
        packageId,
        amount: parseFloat(amount),
        token: tokenSymbol,
        paymentChain: chainKey || 'unknown',
        paymentTxHash: receipt.hash,
        paymentChainId: chainId,
        treasuryAddress,
      });

      return {
        success: true,
        txHash: receipt.hash,
        blockchainRef: regResult.blockchainRef,
      };
    } catch (error: any) {
      return { success: false, error: error.message || 'EVM payment failed' };
    }
  }

  /**
   * Register a crosschain subscription payment on the Thronos blockchain.
   * This creates a verifiable record that the user paid.
   */
  private async registerSubscriptionOnChain(params: {
    subscriber: string;
    packageId: string;
    amount: number;
    token: string;
    paymentChain: string;
    paymentTxHash: string;
    paymentChainId: number | string;
    treasuryAddress: string;
  }): Promise<{ blockchainRef: string }> {
    try {
      const response = await fetch(`${CONFIG.THRONOS_CHAIN_URL}/api/sentinel/subscribe`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          subscriber: params.subscriber,
          package_id: params.packageId,
          amount: params.amount,
          token: params.token,
          payment_chain: params.paymentChain,
          payment_tx_hash: params.paymentTxHash,
          payment_chain_id: params.paymentChainId,
          treasury_address: params.treasuryAddress,
        }),
      });

      const data = await response.json();
      return { blockchainRef: data.blockchain_ref || data.tx_hash || '' };
    } catch {
      // Payment already sent — blockchain registration failed but user paid
      return { blockchainRef: 'pending_verification' };
    }
  }

  // ─── FIAT PAYMENT ──────────────────────────────────────────────────────────

  async processFiatPayment(
    packageId: string,
    email: string,
    currency: string = 'USD',
  ): Promise<{ redirectUrl: string }> {
    const response = await fetch(`${CONFIG.THRONOS_GATEWAY_URL}/api/sentinel/fiat/create-session`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        packageId,
        email,
        currency,
        product: 'sentinel',
        successUrl: 'tradersentinel://payment-success',
        cancelUrl: 'tradersentinel://payment-cancel',
      }),
    });

    const data = await response.json();
    return { redirectUrl: data.url };
  }

  // ─── SUBSCRIPTION STATUS (blockchain-verified) ─────────────────────────────

  async getSubscriptionStatus(userAddress: string): Promise<SubscriptionStatus> {
    const response = await fetch(
      `${CONFIG.THRONOS_CHAIN_URL}/api/sentinel/subscription/${userAddress}`,
    );
    return response.json();
  }

  async verifySubscription(userAddress: string): Promise<boolean> {
    try {
      const status = await this.getSubscriptionStatus(userAddress);
      return status.tier !== 'free' && status.expiresAt > Date.now() / 1000;
    } catch {
      return false;
    }
  }

  // ─── REWARDS ───────────────────────────────────────────────────────────────

  async getRewards(userAddress: string): Promise<RewardsInfo> {
    const response = await fetch(
      `${CONFIG.THRONOS_CHAIN_URL}/api/sentinel/rewards/${userAddress}`,
    );
    return response.json();
  }

  async claimRewards(userAddress: string): Promise<PaymentResult> {
    try {
      const response = await fetch(`${CONFIG.THRONOS_CHAIN_URL}/api/sentinel/rewards/claim`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ address: userAddress }),
      });

      const data = await response.json();
      if (!response.ok) {
        return { success: false, error: data.message || 'Claim failed' };
      }
      return { success: true, txHash: data.tx_hash, blockchainRef: data.blockchain_ref };
    } catch (error: any) {
      return { success: false, error: error.message || 'Claim failed' };
    }
  }

  // ─── LIQUIDITY (USDT/THR and other pools) ──────────────────────────────────

  async addLiquidity(
    tokenA: string,
    tokenB: string,
    amountA: string,
    amountB: string,
    userAddress: string,
  ): Promise<PaymentResult> {
    try {
      const response = await fetch(`${CONFIG.THRONOS_CHAIN_URL}/api/v1/pools/add_liquidity`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          provider: userAddress,
          token_a: tokenA,
          token_b: tokenB,
          amount_a: parseFloat(amountA),
          amount_b: parseFloat(amountB),
        }),
      });

      const data = await response.json();
      if (!response.ok) {
        return { success: false, error: data.message || 'Failed to add liquidity' };
      }
      return { success: true, txHash: data.tx_hash, blockchainRef: data.pool_id };
    } catch (error: any) {
      return { success: false, error: error.message || 'Failed to add liquidity' };
    }
  }

  async removeLiquidity(
    poolId: string,
    shares: string,
    userAddress: string,
  ): Promise<PaymentResult> {
    try {
      const response = await fetch(`${CONFIG.THRONOS_CHAIN_URL}/api/v1/pools/remove_liquidity`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          provider: userAddress,
          pool_id: poolId,
          shares: parseFloat(shares),
        }),
      });

      const data = await response.json();
      if (!response.ok) {
        return { success: false, error: data.message || 'Failed to remove liquidity' };
      }
      return { success: true, txHash: data.tx_hash };
    } catch (error: any) {
      return { success: false, error: error.message || 'Failed to remove liquidity' };
    }
  }

  async getLiquidityPositions(userAddress: string): Promise<LiquidityPosition[]> {
    try {
      const response = await fetch(
        `${CONFIG.THRONOS_CHAIN_URL}/api/v1/pools/positions/${userAddress}`,
      );
      const data = await response.json();
      return data.positions || [];
    } catch {
      return [];
    }
  }

  // ─── STAKING ───────────────────────────────────────────────────────────────

  async stake(amount: string, userAddress: string): Promise<PaymentResult> {
    try {
      const response = await fetch(`${CONFIG.THRONOS_CHAIN_URL}/api/sentinel/stake`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ address: userAddress, amount: parseFloat(amount) }),
      });

      const data = await response.json();
      if (!response.ok) {
        return { success: false, error: data.message || 'Staking failed' };
      }
      return { success: true, txHash: data.tx_hash };
    } catch (error: any) {
      return { success: false, error: error.message || 'Staking failed' };
    }
  }

  async unstake(amount: string, userAddress: string): Promise<PaymentResult> {
    try {
      const response = await fetch(`${CONFIG.THRONOS_CHAIN_URL}/api/sentinel/unstake`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ address: userAddress, amount: parseFloat(amount) }),
      });

      const data = await response.json();
      if (!response.ok) {
        return { success: false, error: data.message || 'Unstaking failed' };
      }
      return { success: true, txHash: data.tx_hash };
    } catch (error: any) {
      return { success: false, error: error.message || 'Unstaking failed' };
    }
  }

  async getStakingInfo(userAddress: string): Promise<{
    stakedAmount: string;
    pendingRewards: string;
    apr: number;
  }> {
    try {
      const response = await fetch(
        `${CONFIG.THRONOS_CHAIN_URL}/api/sentinel/staking/${userAddress}`,
      );
      return await response.json();
    } catch {
      return { stakedAmount: '0', pendingRewards: '0', apr: CONFIG.REWARDS.STAKING_APY * 100 };
    }
  }

  // ─── TREASURY INFO ─────────────────────────────────────────────────────────

  async getTreasuryBalances(): Promise<Record<string, { address: string; balance: number }>> {
    try {
      const response = await fetch(`${CONFIG.THRONOS_CHAIN_URL}/api/sentinel/treasury/balances`);
      return await response.json();
    } catch {
      return {};
    }
  }

  // ─── REFERRAL ──────────────────────────────────────────────────────────────

  async generateReferralLink(userAddress: string): Promise<string> {
    const response = await fetch(
      `${CONFIG.THRONOS_CHAIN_URL}/api/sentinel/referral/generate`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ address: userAddress }),
      },
    );
    const data = await response.json();
    return data.referralLink;
  }
}

export const thronosService = new ThronosService();
export default thronosService;
