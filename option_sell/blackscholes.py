"""
Pure-Python Black-Scholes delta computation.
No external library — uses only math.erf from the stdlib.
"""
import math


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs_delta(
    spot: float,
    strike: float,
    iv_annual: float,
    t_years: float,
    risk_free_rate: float,
    option_type: str,
) -> float:
    """
    Black-Scholes delta for a European option.

    Parameters
    ----------
    spot           : current underlying price
    strike         : option strike price
    iv_annual      : implied volatility as a decimal (0.25 = 25%)
    t_years        : time to expiry in years (e.g. 30/365)
    risk_free_rate : annual risk-free rate as a decimal (0.07 = 7%)
    option_type    : 'CE' for call, 'PE' for put

    Returns
    -------
    Delta in range [0, 1] for CE or [-1, 0] for PE.
    Returns float('nan') for degenerate inputs.
    """
    if t_years <= 0 or iv_annual <= 0 or spot <= 0 or strike <= 0:
        return float("nan")

    d1 = (
        math.log(spot / strike)
        + (risk_free_rate + 0.5 * iv_annual ** 2) * t_years
    ) / (iv_annual * math.sqrt(t_years))

    if option_type == "CE":
        return _norm_cdf(d1)
    else:  # PE
        return _norm_cdf(d1) - 1.0
