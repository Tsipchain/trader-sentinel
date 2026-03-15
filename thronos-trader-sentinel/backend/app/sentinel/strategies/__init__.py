"""
Gift Strategies Module — Two bonus trading strategies for enhanced signal accuracy.

Strategy 1: Smart Money Divergence Detection (SMDD)
    Detects hidden divergences between price and oscillators that signal
    institutional accumulation/distribution patterns.

Strategy 2: Multi-Timeframe Confluence (MTC)
    Cross-validates signals across 1h, 4h, and 1d timeframes.
    Only confirms entries when 2+ timeframes agree on direction.

Both strategies integrate into the Sentinel's _evaluate_entry() pipeline
to boost signal confidence and reduce false entries.
"""

from app.sentinel.strategies.divergence import DivergenceResult, detect_divergences
from app.sentinel.strategies.confluence import ConfluenceResult, check_confluence

__all__ = [
    "DivergenceResult",
    "detect_divergences",
    "ConfluenceResult",
    "check_confluence",
]
