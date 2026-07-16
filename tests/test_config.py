"""
SmartTrade AI Bot — Config Unit Tests
"""

import pytest
from app.config import Settings

def test_max_portfolio_exposure_validation():
    s = Settings()
    s.MAX_TRADE_PCT = 0.20
    s.MAX_PORTFOLIO_EXPOSURE_PCT = 0.15
    with pytest.raises(ValueError, match=r"MAX_PORTFOLIO_EXPOSURE_PCT \(0\.15\) must be strictly greater than MAX_TRADE_PCT \(0\.2\)\."):
        s.validate_required_keys()
