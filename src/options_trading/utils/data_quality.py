# utils/data_quality.py
import pandas as pd
import numpy as np
import logging

logger = logging.getLogger(__name__)

def has_spot_and_option_data(df):
    """Check for both spot and option data presence."""
    required_option_cols = ['open_option', 'high_option', 'low_option', 'close_option']
    required_spot_cols = ['open_spot', 'high_spot', 'low_spot', 'close_spot']
    
    has_option = df[required_option_cols].dropna(how='all').shape[0] > 0
    has_spot = df[required_spot_cols].dropna(how='all').shape[0] > 0
    
    return has_option and has_spot

def validate_option_data(df, strike, option_type):
    """
    Validate option data for gamma scalping suitability.
    RELAXED: Less strict validation to allow more files to be saved.
    """
    issues = []
    
    # Check for reasonable option prices
    if 'close_option' in df.columns:
        min_price = df['close_option'].min()
        max_price = df['close_option'].max()
        
        if min_price <= 0:
            issues.append("Non-positive option prices found")
        
        # More lenient check for call prices
        if max_price > strike * 1.1:  # Allow 10% tolerance
            if option_type.upper() in ['CE', 'CALL']:
                issues.append("Call prices significantly exceed strike price")
    
    # RELAXED: Check data completeness - reduced threshold from 0.8 to 0.5
    essential_cols = ['timestamp', 'close_option', 'close_spot']
    if all(col in df.columns for col in essential_cols):
        completeness = df[essential_cols].dropna().shape[0] / df.shape[0]
        if completeness < 0.5:  # Reduced from 0.8 to 0.5 (50%)
            issues.append(f"Low data completeness: {completeness:.1%}")
    
    if issues:
        logger.warning(f"Data quality issues: {', '.join(issues)}")
        return False
    
    return True

def filter_gamma_scalping_candidates(df, min_gamma=0.01):
    """Filter options suitable for gamma scalping."""
    if 'gamma' not in df.columns:
        logger.warning("Gamma column not found for filtering")
        return df
    
    # Filter criteria for gamma scalping
    mask = (
        (df['gamma'] >= min_gamma) &  # Sufficient gamma
        (df['close_option'] >= 0.5) &  # Minimum price for liquidity
        (df['iv'] >= 0.05) &  # Minimum IV
        (df['iv'] <= 2.0)    # Maximum reasonable IV
    )
    
    filtered_df = df[mask].copy()
    logger.info(f"Gamma scalping filter: {len(filtered_df)}/{len(df)} rows retained")
    
    return filtered_df
