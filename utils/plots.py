import os
import glob
import random
import pandas as pd
import matplotlib.pyplot as plt
def plot_2x2_continuous_from_dir(parquet_dir, sample_size=1):
    """
    Randomly samples up to sample_size .parquet files from parquet_dir,
    and for each, plots close_option, close_spot, iv_option (or iv), rv_spot (or rv_gk)
    in a 2x2 subplot grid. X-axis is DataFrame index; x-tick labels show timestamps.
    """
    files = glob.glob(os.path.join(parquet_dir, "*.parquet"))
    if not files:
        print(f"No parquet files found in {parquet_dir}")
        return
    sampled_files = random.sample(files, min(sample_size, len(files)))
    for file_path in sampled_files:
        df = pd.read_parquet(file_path)
        df = df.sort_values('timestamp')
        x = range(len(df))

        # Flexible column naming for IV and RV
        iv_col = "iv_option" if "iv_option" in df.columns else ("iv" if "iv" in df.columns else None)
        rv_col = "rv_spot" if "rv_spot" in df.columns else ("rv_gk" if "rv_gk" in df.columns else None)

        fig, axs = plt.subplots(2, 2, figsize=(14, 8), sharex=True)
        fig.suptitle(f"2x2 Feature Plot (Continuous): {os.path.basename(file_path)}", fontsize=16)

        axs[0, 0].plot(x, df['close_option'], label='Option Close', color='orange')
        axs[0, 0].set_ylabel('Option Close')
        axs[0, 0].set_title('Option Close')
        axs[0, 0].legend()

        axs[0, 1].plot(x, df['close_spot'], label='Spot Close', color='blue')
        axs[0, 1].set_ylabel('Spot Close')
        axs[0, 1].set_title('Spot Close')
        axs[0, 1].legend()

        if iv_col:
            axs[1, 0].plot(x, df[iv_col], label='IV Option', color='green')
            axs[1, 0].set_ylabel('IV')
            axs[1, 0].set_title('Implied Volatility')
            axs[1, 0].legend()
        else:
            axs[1, 0].set_title('Implied Volatility (Not Found)')
            axs[1, 0].text(0.5, 0.5, 'IV column missing', ha='center', va='center')

        if rv_col:
            axs[1, 1].plot(x, df[rv_col], label='RV Spot', color='red')
            axs[1, 1].set_ylabel('RV')
            axs[1, 1].set_title('Realized Volatility')
            axs[1, 1].legend()
        else:
            axs[1, 1].set_title('Realized Volatility (Not Found)')
            axs[1, 1].text(0.5, 0.5, 'RV column missing', ha='center', va='center')

        # Custom x-ticks for readability
        step = max(1, len(df) // 10)
        tick_locs = list(range(0, len(df), step))
        tick_labels = [df['timestamp'].iloc[i].strftime('%Y-%m-%d %H:%M') for i in tick_locs]
        for ax in axs.flatten():
            ax.set_xticks(tick_locs)
            ax.set_xticklabels(tick_labels, rotation=45, ha='right')

        plt.tight_layout(rect=[0, 0, 1, 0.95])
        os.makedirs("plots", exist_ok=True)
        fname = os.path.splitext(os.path.basename(file_path))[0] + ".png"
        plt.savefig(os.path.join("plots", f"{fname}"))