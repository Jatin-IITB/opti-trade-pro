"""Daily P&L explain engine: how much of today's P&L do we understand?

Decomposes realized P&L into theta carry, gamma vs realized variance, vega
against PCA surface factors (level/term/skew) and vanna/volga cross terms,
with "% of P&L explained" as the headline metric; plus expiry-bucketed
exposure reporting. Builds on the Taylor attribution in
:mod:`optitrade.hedging.pnl`.
"""

from optitrade.explain.bucket_report import EXPIRY_BUCKETS, BucketReport, bucket_exposures
from optitrade.explain.factors import (
    SurfaceFactorModel,
    fit_surface_factors,
    project,
    reconstruct,
)
from optitrade.explain.pnl_explain import PnLExplain, explain_pnl

__all__ = [
    "EXPIRY_BUCKETS",
    "BucketReport",
    "PnLExplain",
    "SurfaceFactorModel",
    "bucket_exposures",
    "explain_pnl",
    "fit_surface_factors",
    "project",
    "reconstruct",
]
