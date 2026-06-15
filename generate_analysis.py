"""
Tägliche Chartanalyse-Generator: Charts rendern, GitHub pushen, Notion-Pages anlegen.
Wird vom Cron aufgerufen, erhält analysis_data via JSON-Eingabe.

Aufruf: python3 generate_analysis.py <analysis_json_path>
"""
import json
import os
import sys
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

import yfinance as yf
import mplfinance as mpf
import pandas as pd

REPO_DIR = "/tmp/trading-charts"
GITHUB_USER = "p4mffhv2h4-blip"
GITHUB_REPO = "trading-charts"
GITHUB_BRANCH = "master"

# Asset -> yfinance Ticker Mapping
TICKER_MAP = {
    "DE40": "^GDAXI",
    "US500": "^GSPC",
    "USDJPY": "USDJPY=X",
    "EURUSD": "EURUSD=X",
    "XAUUSD": "GC=F",  # Gold Futures als Proxy
}

ASSET_DISPLAY = {
    "DE40": "DAX / DE40",
    "US500": "S&P 500 / US500",
    "USDJPY": "USD/JPY",
    "EURUSD": "EUR/USD",
    "XAUUSD": "Gold / XAU/USD",
}


def fetch_ohlc(asset: str, days: int = 10, interval: str = "1h") -> pd.DataFrame:
    """OHLC-Daten holen mit Fallback auf längere Intervalle bei Lücken."""
    ticker = TICKER_MAP[asset]
    end = datetime.now()
    start = end - timedelta(days=days)
    df = yf.download(ticker, start=start, end=end, interval=interval,
                     progress=False, auto_adjust=False)
    if df.empty or len(df) < 20:
        # Fallback auf längeres Intervall
        df = yf.download(ticker, start=end - timedelta(days=30), end=end,
                         interval="1h", progress=False, auto_adjust=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] for c in df.columns]
    df = df[['Open', 'High', 'Low', 'Close', 'Volume']].dropna()
    return df


def render_chart(asset: str, data: dict, target_date: str, out_path: str) -> bool:
    """Rendert annotierten Candlestick-Chart mit Zickzack-Projektion."""
    try:
        df = fetch_ohlc(asset)
        if df.empty:
            print(f"WARN {asset}: keine OHLC-Daten")
            return False
        df = df.tail(100)
    except Exception as e:
        print(f"WARN {asset} fetch failed: {e}")
        return False

    setup = data.get('primary_setup')
    bias = data.get('bias', 'neutral').upper()
    supports = data.get('support_levels', [])[:3]
    resistances = data.get('resistance_levels', [])[:3]

    # Wenn kein primary, alternative nehmen
    if not setup:
        setup = data.get('alternative_setup')

    # Zahlen extrahieren
    if setup:
        entry = setup.get('entry')
        sl = setup.get('stop_loss')
        tp1 = setup.get('take_profit_1')
        tp2 = setup.get('take_profit_2')
        rr = setup.get('risk_reward')
        direction = setup.get('direction', '').upper()
    else:
        entry = sl = tp1 = tp2 = rr = None
        direction = "NEUTRAL"

    # Y-Range
    all_levels = [v for v in [entry, sl, tp1, tp2] + supports + resistances if v is not None]
    if all_levels:
        ymin = min(df['Low'].min(), min(all_levels)) * 0.9985
        ymax = max(df['High'].max(), max(all_levels)) * 1.0015
    else:
        ymin = df['Low'].min() * 0.998
        ymax = df['High'].max() * 1.002

    # Future-Bereich anhängen
    n_future = 25
    last_ts = df.index[-1]
    try:
        future_idx = pd.date_range(start=last_ts + pd.Timedelta(hours=1),
                                    periods=n_future, freq='h')
    except Exception:
        future_idx = pd.date_range(start=last_ts + pd.Timedelta(days=1),
                                    periods=n_future, freq='D')
    future_df = pd.DataFrame(index=future_idx, columns=df.columns, dtype=float)
    df_ext = pd.concat([df, future_df])

    # Style
    mc = mpf.make_marketcolors(up='#26a69a', down='#ef5350',
                                edge='inherit', wick='inherit')
    s = mpf.make_mpf_style(marketcolors=mc, base_mpf_style='classic',
                            gridstyle=':', gridcolor='#dddddd',
                            facecolor='#faf7fc', figcolor='#ffffff',
                            edgecolor='#cccccc', rc={'font.size': 10})

    # Horizontale Linien
    hlines_levels, hlines_colors, hlines_styles, hlines_widths = [], [], [], []
    if entry is not None:
        hlines_levels.append(entry); hlines_colors.append('#1565c0')
        hlines_styles.append('-'); hlines_widths.append(2.0)
    if sl is not None:
        hlines_levels.append(sl); hlines_colors.append('#c62828')
        hlines_styles.append('--'); hlines_widths.append(2.0)
    if tp1 is not None:
        hlines_levels.append(tp1); hlines_colors.append('#2e7d32')
        hlines_styles.append('-'); hlines_widths.append(1.6)
    if tp2 is not None:
        hlines_levels.append(tp2); hlines_colors.append('#2e7d32')
        hlines_styles.append('-'); hlines_widths.append(1.6)
    for s_lvl in supports:
        hlines_levels.append(s_lvl); hlines_colors.append('#9e9e9e')
        hlines_styles.append(':'); hlines_widths.append(0.9)
    for r_lvl in resistances:
        hlines_levels.append(r_lvl); hlines_colors.append('#9e9e9e')
        hlines_styles.append(':'); hlines_widths.append(0.9)

    hlines_kw = dict(hlines=hlines_levels, colors=hlines_colors,
                     linestyle=hlines_styles, linewidths=hlines_widths) if hlines_levels else {}

    title = f"\n{ASSET_DISPLAY[asset]} H1  —  Setup {target_date}  —  Bias: {bias}"

    try:
        fig, axes = mpf.plot(df_ext, type='candle', style=s,
                             hlines=hlines_kw if hlines_levels else None,
                             title=title, ylabel='', volume=False, figsize=(15, 9),
                             returnfig=True, tight_layout=True,
                             ylim=(ymin, ymax),
                             datetime_format='%b %d %H:%M', xrotation=20)
    except Exception as e:
        print(f"ERR {asset} plot failed: {e}")
        return False

    ax = axes[0]
    ax.set_ylabel('')
    x_start = len(df) - 1
    x_end = len(df_ext) - 1

    # Zickzack-Projektion vom Entry
    if entry is not None and tp1 is not None and direction in ("LONG", "SHORT"):
        if direction == "LONG":
            mid1 = tp1
            pullback1 = tp1 - (tp1 - entry) * 0.30
            mid2 = tp2 if tp2 else tp1 * 1.005
            pullback2 = mid2 - (mid2 - tp1) * 0.35 if tp2 else mid1
            final = (tp2 + (tp2 - tp1) * 0.4) if tp2 else tp1 * 1.008
        else:  # SHORT
            mid1 = tp1
            pullback1 = tp1 + (entry - tp1) * 0.30
            mid2 = tp2 if tp2 else tp1 * 0.995
            pullback2 = mid2 + (tp1 - mid2) * 0.35 if tp2 else mid1
            final = (tp2 - (tp1 - tp2) * 0.4) if tp2 else tp1 * 0.992

        px = [x_start,
              x_start + (x_end - x_start) * 0.30,
              x_start + (x_end - x_start) * 0.45,
              x_start + (x_end - x_start) * 0.70,
              x_start + (x_end - x_start) * 0.85,
              x_end - 1]
        py = [entry, mid1, pullback1, mid2, pullback2, final]
        ax.plot(px, py, color='#212121', linewidth=1.8, linestyle='-', zorder=5)
        ax.annotate('', xy=(px[-1], py[-1]), xytext=(px[-2], py[-2]),
                    arrowprops=dict(arrowstyle='->', color='#212121', lw=2), zorder=6)

        # Bias-Pfeil
        arrow_color = '#1976d2' if direction == "LONG" else '#d32f2f'
        arrow_style = '-|>' if direction == "LONG" else '-|>'
        if direction == "LONG":
            ax.annotate('', xy=(x_start - 5, entry + (tp1 - entry) * 0.6),
                        xytext=(x_start - 5, entry - abs(entry - (sl or entry)) * 0.3),
                        arrowprops=dict(arrowstyle=arrow_style, color=arrow_color, lw=6, alpha=0.7), zorder=4)
        else:
            ax.annotate('', xy=(x_start - 5, entry - (entry - tp1) * 0.6),
                        xytext=(x_start - 5, entry + abs((sl or entry) - entry) * 0.3),
                        arrowprops=dict(arrowstyle=arrow_style, color=arrow_color, lw=6, alpha=0.7), zorder=4)

    # Labels rechts
    def fmt(v): return f"{v:.4f}" if v < 100 else f"{v:.2f}"
    for label, y, color in [
        ('Entry', entry, '#1565c0'),
        ('SL', sl, '#c62828'),
        ('TP1', tp1, '#2e7d32'),
        ('TP2', tp2, '#2e7d32'),
    ]:
        if y is None: continue
        ax.annotate(f' {label} {fmt(y)} ', xy=(x_end, y), xytext=(8, 0),
                    textcoords='offset points', fontsize=11, fontweight='bold',
                    color='white', va='center', ha='left',
                    bbox=dict(boxstyle='round,pad=0.4', facecolor=color, edgecolor=color),
                    zorder=10)

    # Bias-Box
    if bias in ('LONG', 'SHORT'):
        bias_color = '#2e7d32' if bias == 'LONG' else '#c62828'
        bias_bg = '#e8f5e9' if bias == 'LONG' else '#ffebee'
    else:
        bias_color = '#616161'; bias_bg = '#eeeeee'
    rr_text = f"R/R {rr}" if rr else ""
    ax.text(0.02, 0.97, f'{bias}\n{rr_text}'.strip(), transform=ax.transAxes,
            fontsize=14, fontweight='bold', color=bias_color,
            verticalalignment='top',
            bbox=dict(boxstyle='round,pad=0.6', facecolor=bias_bg,
                      edgecolor=bias_color, linewidth=2))

    # NOW-Trennlinie
    ax.axvline(x=x_start + 0.5, color='#999999', linestyle=':', linewidth=1, alpha=0.6)
    ax.text(x_start + 0.5, ymax - (ymax - ymin) * 0.02, ' NOW',
            fontsize=9, color='#666666', alpha=0.8)

    fig.savefig(out_path, dpi=120, bbox_inches='tight', facecolor='#ffffff')
    import matplotlib.pyplot as plt
    plt.close(fig)
    return True


def push_to_github(charts: dict, target_date: str) -> dict:
    """Pusht alle Chart-PNGs ins GitHub-Repo, gibt Raw-URLs zurück."""
    Path(REPO_DIR).parent.mkdir(parents=True, exist_ok=True)
    if not Path(REPO_DIR).exists():
        subprocess.run(["git", "clone",
                        f"https://github.com/{GITHUB_USER}/{GITHUB_REPO}.git",
                        REPO_DIR], check=True, capture_output=True)
    else:
        subprocess.run(["git", "-C", REPO_DIR, "pull", "--ff-only"],
                       capture_output=True)

    urls = {}
    for asset, local_path in charts.items():
        if not local_path or not Path(local_path).exists():
            continue
        target_name = f"{target_date}_{asset}.png"
        target_path = Path(REPO_DIR) / target_name
        subprocess.run(["cp", local_path, str(target_path)], check=True)
        urls[asset] = (f"https://raw.githubusercontent.com/{GITHUB_USER}/"
                       f"{GITHUB_REPO}/{GITHUB_BRANCH}/{target_name}")

    subprocess.run(["git", "-C", REPO_DIR, "add", "."], capture_output=True)
    subprocess.run(["git", "-C", REPO_DIR,
                    "-c", "user.email=trading-charts@local",
                    "-c", "user.name=trading-bot",
                    "commit", "-m", f"charts {target_date}"], capture_output=True)
    result = subprocess.run(["git", "-C", REPO_DIR, "push", "origin", "HEAD"],
                            capture_output=True, text=True)
    print(f"Push: {result.returncode}, stderr: {result.stderr[:200]}")
    return urls


if __name__ == "__main__":
    analysis_path = sys.argv[1]
    with open(analysis_path) as f:
        analysis = json.load(f)
    target_date = analysis['target_session']
    out_dir = Path(f"/tmp/charts_{target_date}")
    out_dir.mkdir(exist_ok=True)

    charts = {}
    for asset in ['DE40', 'US500', 'USDJPY', 'EURUSD', 'XAUUSD']:
        if asset not in analysis['assets']:
            continue
        out_path = out_dir / f"{asset}.png"
        ok = render_chart(asset, analysis['assets'][asset], target_date, str(out_path))
        if ok:
            charts[asset] = str(out_path)
            print(f"OK {asset}")
        else:
            print(f"FAIL {asset}")

    urls = push_to_github(charts, target_date)
    # URLs in Analyse-Datei nachtragen für Folge-Script
    for asset, url in urls.items():
        analysis['assets'][asset]['chart_url'] = url
    with open(analysis_path, 'w') as f:
        json.dump(analysis, f, indent=2, ensure_ascii=False)
    print("URLs:", urls)
