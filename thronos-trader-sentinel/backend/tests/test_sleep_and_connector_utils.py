from app.brain.connector import _adapt_symbol
from app.brain.sleep_trader import _sl_tp_for_leverage, _position_size


def test_symbol_normalization_spot_vs_futures_suffix():
    assert _adapt_symbol("BTC/USDT", "futures") == "BTC/USDT:USDT"
    assert _adapt_symbol("BTC/USDT:USDT", "spot") == "BTC/USDT"


def test_sl_tp_distance_tightens_with_higher_leverage():
    sl1, tp1 = _sl_tp_for_leverage(5, "buy", 100.0, 2.0, 4.0)
    sl2, tp2 = _sl_tp_for_leverage(50, "buy", 100.0, 2.0, 4.0)

    # Higher leverage -> tighter distances from entry
    dist_sl_1 = 100.0 - sl1
    dist_sl_2 = 100.0 - sl2
    dist_tp_1 = tp1 - 100.0
    dist_tp_2 = tp2 - 100.0

    assert dist_sl_2 <= dist_sl_1
    assert dist_tp_2 <= dist_tp_1


def test_position_size_non_decreasing_with_leverage_for_small_base():
    equity = 200.0
    price = 100.0
    a1 = _position_size(equity, price, risk_pct=5.0, max_position_pct=20.0, leverage=1, entry_margin_pct=0.5)
    a2 = _position_size(equity, price, risk_pct=5.0, max_position_pct=20.0, leverage=2, entry_margin_pct=0.5)
    a3 = _position_size(equity, price, risk_pct=5.0, max_position_pct=20.0, leverage=3, entry_margin_pct=0.5)

    assert a2 >= a1
    assert a3 >= a2
