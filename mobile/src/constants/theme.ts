// Trader Sentinel Theme Constants

export const COLORS = {
  // Primary
  primary: '#6366F1',
  primaryDark: '#4F46E5',
  primaryLight: '#818CF8',

  // Accent
  accent: '#22D3EE',
  accentDark: '#06B6D4',

  // Background
  background: '#0A0E27',
  backgroundLight: '#131836',
  backgroundCard: '#1A1F42',
  backgroundModal: '#252B52',

  // Surface
  surface: '#1E2344',
  surfaceLight: '#2A3158',

  // Text
  text: '#FFFFFF',
  textSecondary: '#9CA3AF',
  textMuted: '#6B7280',

  // Status
  success: '#10B981',
  successDark: '#059669',
  warning: '#F59E0B',
  warningDark: '#D97706',
  error: '#EF4444',
  errorDark: '#DC2626',
  info: '#3B82F6',

  // Chart
  chartGreen: '#00F5A0',
  chartRed: '#FF6B6B',
  chartBlue: '#00D4FF',
  chartPurple: '#A855F7',
  chartOrange: '#FB923C',

  // Gradients (for LinearGradient)
  gradientPrimary: ['#6366F1', '#4F46E5'],
  gradientAccent: ['#22D3EE', '#06B6D4'],
  gradientSuccess: ['#10B981', '#059669'],
  gradientCard: ['#1E2344', '#131836'],

  // Thronos Brand
  thronosGold: '#FFD700',
  thronosPurple: '#8B5CF6',

  // Borders
  border: '#2A3158',
  borderLight: '#374168',

  // Shadow
  shadow: '#000000',

  // Transparent
  transparent: 'transparent',
  overlay: 'rgba(0, 0, 0, 0.5)',
};

export const SPACING = {
  xs: 4,
  sm: 8,
  md: 16,
  lg: 24,
  xl: 32,
  xxl: 48,
};

export const FONT_SIZES = {
  xs: 10,
  sm: 12,
  md: 14,
  lg: 16,
  xl: 18,
  xxl: 24,
  xxxl: 32,
  display: 48,
};

export const FONT_WEIGHTS = {
  regular: '400' as const,
  medium: '500' as const,
  semibold: '600' as const,
  bold: '700' as const,
};

export const BORDER_RADIUS = {
  sm: 4,
  md: 8,
  lg: 12,
  xl: 16,
  xxl: 24,
  full: 9999,
};

export const SHADOWS = {
  sm: {
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 1 },
    shadowOpacity: 0.2,
    shadowRadius: 2,
    elevation: 2,
  },
  md: {
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.25,
    shadowRadius: 4,
    elevation: 4,
  },
  lg: {
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.3,
    shadowRadius: 8,
    elevation: 8,
  },
  glow: {
    shadowColor: '#6366F1',
    shadowOffset: { width: 0, height: 0 },
    shadowOpacity: 0.5,
    shadowRadius: 12,
    elevation: 12,
  },
};

export default {
  COLORS,
  SPACING,
  FONT_SIZES,
  FONT_WEIGHTS,
  BORDER_RADIUS,
  SHADOWS,
};
