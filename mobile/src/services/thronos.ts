// Thronos Integration Service
// Handles wallet connection, payments, rewards, and liquidity

import { ethers } from 'ethers';
import { CONFIG } from '../config';

// Types
export interface PaymentRequest {
  packageId: string;
  chainId: number;
  tokenAddress: string;
  amount: string;
  userAddress: string;
}

export interface PaymentResult {
  success: boolean;
  txHash?: string;
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

// Thronos Gateway ABI (simplified)
const THRONOS_GATEWAY_ABI = [
  'function paySubscription(address token, uint256 amount, string packageId) payable returns (bool)',
  'function claimRewards() external returns (uint256)',
  'function getPendingRewards(address user) view returns (uint256)',
  'function getSubscription(address user) view returns (string, uint256, uint256)',
  'function addLiquidity(address tokenA, address tokenB, uint256 amountA, uint256 amountB) external returns (uint256)',
  'function removeLiquidity(uint256 lpAmount) external returns (uint256, uint256)',
  'function stake(uint256 amount) external',
  'function unstake(uint256 amount) external',
  'function getStakingInfo(address user) view returns (uint256, uint256, uint256)',
];

// ERC20 ABI
const ERC20_ABI = [
  'function approve(address spender, uint256 amount) returns (bool)',
  'function allowance(address owner, address spender) view returns (uint256)',
  'function balanceOf(address account) view returns (uint256)',
  'function decimals() view returns (uint8)',
  'function symbol() view returns (string)',
];

class ThronosService {
  private provider: ethers.BrowserProvider | null = null;
  private signer: ethers.Signer | null = null;
  private gatewayContract: ethers.Contract | null = null;

  // Initialize with wallet provider
  async initialize(walletProvider: any): Promise<void> {
    this.provider = new ethers.BrowserProvider(walletProvider);
    this.signer = await this.provider.getSigner();

    // Initialize gateway contract (address would be from config)
    const gatewayAddress = '0x...'; // Thronos Gateway Contract Address
    this.gatewayContract = new ethers.Contract(
      gatewayAddress,
      THRONOS_GATEWAY_ABI,
      this.signer
    );
  }

  // Get connected wallet address
  async getAddress(): Promise<string> {
    if (!this.signer) throw new Error('Wallet not connected');
    return this.signer.getAddress();
  }

  // Get token balance
  async getTokenBalance(tokenAddress: string, userAddress: string): Promise<string> {
    if (!this.provider) throw new Error('Provider not initialized');

    const tokenContract = new ethers.Contract(
      tokenAddress,
      ERC20_ABI,
      this.provider
    );

    const balance = await tokenContract.balanceOf(userAddress);
    const decimals = await tokenContract.decimals();

    return ethers.formatUnits(balance, decimals);
  }

  // Check and approve token spending
  async approveToken(
    tokenAddress: string,
    spenderAddress: string,
    amount: string
  ): Promise<string> {
    if (!this.signer) throw new Error('Wallet not connected');

    const tokenContract = new ethers.Contract(
      tokenAddress,
      ERC20_ABI,
      this.signer
    );

    const decimals = await tokenContract.decimals();
    const amountWei = ethers.parseUnits(amount, decimals);

    const tx = await tokenContract.approve(spenderAddress, amountWei);
    await tx.wait();

    return tx.hash;
  }

  // Process subscription payment
  async processPayment(request: PaymentRequest): Promise<PaymentResult> {
    if (!this.signer || !this.gatewayContract) {
      return { success: false, error: 'Wallet not connected' };
    }

    try {
      const tokenContract = new ethers.Contract(
        request.tokenAddress,
        ERC20_ABI,
        this.signer
      );

      const decimals = await tokenContract.decimals();
      const amountWei = ethers.parseUnits(request.amount, decimals);

      // Check allowance
      const gatewayAddress = await this.gatewayContract.getAddress();
      const allowance = await tokenContract.allowance(request.userAddress, gatewayAddress);

      if (allowance < amountWei) {
        // Approve first
        const approveTx = await tokenContract.approve(gatewayAddress, amountWei);
        await approveTx.wait();
      }

      // Process payment
      const tx = await this.gatewayContract.paySubscription(
        request.tokenAddress,
        amountWei,
        request.packageId
      );

      const receipt = await tx.wait();

      return {
        success: true,
        txHash: receipt.hash,
      };
    } catch (error: any) {
      return {
        success: false,
        error: error.message || 'Payment failed',
      };
    }
  }

  // Process fiat payment via Thronos Gateway
  async processFiatPayment(
    packageId: string,
    email: string,
    currency: string = 'USD'
  ): Promise<{ redirectUrl: string }> {
    const response = await fetch(`${CONFIG.THRONOS_GATEWAY_URL}/api/fiat/create-session`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        packageId,
        email,
        currency,
        successUrl: 'tradersentinel://payment-success',
        cancelUrl: 'tradersentinel://payment-cancel',
      }),
    });

    const data = await response.json();
    return { redirectUrl: data.url };
  }

  // Get user rewards
  async getRewards(userAddress: string): Promise<RewardsInfo> {
    // This would call the Thronos API to get rewards info
    const response = await fetch(
      `${CONFIG.THRONOS_GATEWAY_URL}/api/rewards/${userAddress}`
    );
    return response.json();
  }

  // Claim pending rewards
  async claimRewards(): Promise<PaymentResult> {
    if (!this.gatewayContract) {
      return { success: false, error: 'Contract not initialized' };
    }

    try {
      const tx = await this.gatewayContract.claimRewards();
      const receipt = await tx.wait();

      return {
        success: true,
        txHash: receipt.hash,
      };
    } catch (error: any) {
      return {
        success: false,
        error: error.message || 'Claim failed',
      };
    }
  }

  // Get subscription status
  async getSubscriptionStatus(userAddress: string): Promise<{
    tier: string;
    expiresAt: number;
    autoRenew: boolean;
  }> {
    const response = await fetch(
      `${CONFIG.THRONOS_GATEWAY_URL}/api/subscription/${userAddress}`
    );
    return response.json();
  }

  // Add liquidity to Thronos pool
  async addLiquidity(
    tokenA: string,
    tokenB: string,
    amountA: string,
    amountB: string
  ): Promise<PaymentResult> {
    if (!this.signer || !this.gatewayContract) {
      return { success: false, error: 'Wallet not connected' };
    }

    try {
      // Approve both tokens
      const tokenAContract = new ethers.Contract(tokenA, ERC20_ABI, this.signer);
      const tokenBContract = new ethers.Contract(tokenB, ERC20_ABI, this.signer);

      const decimalsA = await tokenAContract.decimals();
      const decimalsB = await tokenBContract.decimals();

      const amountAWei = ethers.parseUnits(amountA, decimalsA);
      const amountBWei = ethers.parseUnits(amountB, decimalsB);

      const gatewayAddress = await this.gatewayContract.getAddress();

      // Approve tokens
      await (await tokenAContract.approve(gatewayAddress, amountAWei)).wait();
      await (await tokenBContract.approve(gatewayAddress, amountBWei)).wait();

      // Add liquidity
      const tx = await this.gatewayContract.addLiquidity(
        tokenA,
        tokenB,
        amountAWei,
        amountBWei
      );

      const receipt = await tx.wait();

      return {
        success: true,
        txHash: receipt.hash,
      };
    } catch (error: any) {
      return {
        success: false,
        error: error.message || 'Failed to add liquidity',
      };
    }
  }

  // Get liquidity positions
  async getLiquidityPositions(userAddress: string): Promise<LiquidityPosition[]> {
    const response = await fetch(
      `${CONFIG.THRONOS_GATEWAY_URL}/api/liquidity/${userAddress}`
    );
    return response.json();
  }

  // Stake THRONOS tokens
  async stake(amount: string): Promise<PaymentResult> {
    if (!this.gatewayContract) {
      return { success: false, error: 'Contract not initialized' };
    }

    try {
      const amountWei = ethers.parseUnits(amount, 18);
      const tx = await this.gatewayContract.stake(amountWei);
      const receipt = await tx.wait();

      return {
        success: true,
        txHash: receipt.hash,
      };
    } catch (error: any) {
      return {
        success: false,
        error: error.message || 'Staking failed',
      };
    }
  }

  // Get staking info
  async getStakingInfo(userAddress: string): Promise<{
    stakedAmount: string;
    pendingRewards: string;
    apr: number;
  }> {
    const response = await fetch(
      `${CONFIG.THRONOS_GATEWAY_URL}/api/staking/${userAddress}`
    );
    return response.json();
  }

  // Generate referral link
  async generateReferralLink(userAddress: string): Promise<string> {
    const response = await fetch(
      `${CONFIG.THRONOS_GATEWAY_URL}/api/referral/generate`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ address: userAddress }),
      }
    );
    const data = await response.json();
    return data.referralLink;
  }
}

export const thronosService = new ThronosService();
export default thronosService;
