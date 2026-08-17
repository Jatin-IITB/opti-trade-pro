import glob
import logging
import os
import random

import pandas as pd

logger = logging.getLogger(__name__)


def save_final_file(df, path):
    """
    Save DataFrame to parquet file with enhanced error handling.
    """
    try:
        # Ensure directory exists
        os.makedirs(os.path.dirname(path), exist_ok=True)

        # Save with compression
        df.to_parquet(path, compression="snappy")

        # Verify file was created
        if os.path.exists(path):
            file_size = os.path.getsize(path)
            logger.debug(f"File saved successfully: {path} ({file_size} bytes)")
        else:
            logger.error(f"File was not created: {path}")

    except Exception as e:
        logger.error(f"Failed to save file {path}: {e}")
        raise


def sample_and_show_heads(parquet_dir, sample_size=5):
    # Get list of all parquet files in the directory
    parquet_files = glob.glob(f"{parquet_dir}/*.parquet")
    if not parquet_files:
        print("No parquet files found in the directory.")
        return
    # Randomly sample up to sample_size files
    sample_files = random.sample(parquet_files, min(sample_size, len(parquet_files)))
    for file in sample_files:
        df = pd.read_parquet(file)
        print(f"\nFile: {file}")
        print(df.head(5))
