"""OptiTrade Pro quant core — derivatives pricing & risk engine.

Pure-computation library (numpy/scipy only, no I/O beyond the journal, no
web framework). The platform layer in ``options_trading`` consumes this
package; the reverse import direction is forbidden (ADR-002).

Subpackages
-----------
pricing      Black-Scholes pricing and implied-vol extraction
vol          Volatility surfaces: cubic-spline interpolation, SABR, no-arbitrage
greeks       Sensitivities: analytic, finite-difference, adjoint AD, scenario grids
hedging      Delta-neutral hedging, gamma scalping, P&L attribution
risk         Fail-closed pre-trade risk controls
journal      Append-only event-sourced run journal (JSONL)
governance   Expert debate panel for trade-decision review
attribution  Shapley-value P&L credit assignment
backtest     GBM simulation and hedging backtests
"""

__version__ = "3.0.0"
