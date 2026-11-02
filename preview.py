"""Render the GitHub README chart from the local cache; never fetch data."""

from __future__ import annotations

import argparse
import io
import json
import math
import os
import tempfile
from datetime import date, timedelta
from pathlib import Path

# Keep Matplotlib's generated font cache out of the user's global configuration.
os.environ["MPLCONFIGDIR"] = str(Path(__file__).resolve().parent / ".cache" / "matplotlib")

PROFILES = {
    "000985": {"title": "中证全指 · 000985", "english": "CSI All Share 000985",
               "source_cn": "中证指数官网", "source_en": "China Securities Index website",
               "domain": "csindex.com.cn", "filename": "pe-history.png", "data": "data.json"},
    "002415": {"title": "海康威视 · 002415", "english": "Hikvision 002415",
               "source_cn": "东方财富", "source_en": "Eastmoney",
               "domain": "data.eastmoney.com", "filename": "hikvision-pe-history.png",
               "data": "hikvision-data.json"},
}


def _observations(payload: dict) -> tuple[list[date], list[float]]:
    if payload.get("index", {}).get("code") not in PROFILES:
        raise ValueError("Unsupported instrument for the PE preview.")
    dates, values = [], []
    for record in payload.get("records", []):
        try:
            day = date.fromisoformat(record["date"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Each observation needs an ISO date.") from exc
        if dates and day <= dates[-1]:
            raise ValueError("Observation dates must be unique and increasing.")
        raw = record.get("pe")
        if raw is None:
            value = math.nan
        else:
            if isinstance(raw, bool) or not isinstance(raw, (int, float)):
                raise ValueError("P/E must be a positive number or null.")
            value = float(raw)
            if not math.isfinite(value) or value <= 0:
                raise ValueError("P/E must be a positive number or null.")
        dates.append(day)
        values.append(value)
    if not any(math.isfinite(value) for value in values):
        raise ValueError("No valid P/E observations are available to render.")
    return dates, values


def _chart_font(font_manager):
    # Prefer Windows' Chinese font, while remaining usable on macOS and Linux.
    installed = {font.name for font in font_manager.fontManager.ttflist}
    for family in ("Microsoft YaHei", "Microsoft YaHei UI", "PingFang SC",
                   "Noto Sans CJK SC", "Source Han Sans SC", "SimHei"):
        if family in installed:
            return font_manager.FontProperties(family=family), True
    return font_manager.FontProperties(family="DejaVu Sans"), False


def render_preview(root: Path, payload: dict) -> None:
    """Atomically replace this instrument's PNG using all cached observations.

    Invalid input or a rendering/write failure leaves an existing image intact.
    Null P/E observations remain NaN, so Matplotlib breaks the historical line.
    """
    dates, values = _observations(payload)
    code = payload["index"]["code"]
    profile = PROFILES[code]

    import matplotlib
    matplotlib.use("Agg")
    from matplotlib import dates as mdates, font_manager
    from matplotlib.figure import Figure
    import numpy as np

    finite = [(day, value) for day, value in zip(dates, values) if math.isfinite(value)]
    latest_day, latest_pe = finite[-1]
    valid_count = len(finite)
    missing_count = len(values) - valid_count
    quantiles = np.quantile([value for _, value in finite], [0.2, 0.5, 0.8])
    font, chinese = _chart_font(font_manager)
    colors = {"green": "#21654C", "ink": "#243A30", "muted": "#64756B",
              "paper": "#F8FAF7", "grid": "#E4EBE3"}
    figure = Figure(figsize=(15, 8), dpi=160, facecolor=colors["paper"])
    axes = figure.add_axes([0.075, 0.245, 0.815, 0.49], facecolor="#FFFFFF")

    def text(x, y, label, size=11, color=None, **kwargs):
        return figure.text(x, y, label, fontproperties=font, fontsize=size,
                           color=color or colors["ink"], **kwargs)

    text(0.075, 0.935, profile["title"] if chinese else profile["english"], 24,
         weight="bold")
    text(0.075, 0.884, ("滚动市盈率 PE TTM｜源站全部可用日度历史" if code == "002415" else "滚动市盈率｜全历史观察")
         if chinese else "ROLLING P/E · AVAILABLE HISTORY", 13,
         color=colors["muted"])
    text(0.075, 0.810,
         f"{dates[0]:%Y-%m-%d} — {dates[-1]:%Y-%m-%d}   ·   "
         + (f"有效样本 {valid_count:,}   ·   缺失 {missing_count:,}" if chinese
            else f"Valid observations {valid_count:,}   ·   Missing {missing_count:,}"), 11,
         color=colors["muted"])
    text(0.89, 0.929, f"{latest_pe:.2f}" + (" 倍" if chinese else "×"), 30,
         color=colors["green"], weight="bold", ha="right")
    text(0.89, 0.879, ("最新有效值 · " if chinese else "LATEST VALID · ") + latest_day.isoformat(),
         11, ha="right", color=colors["muted"])

    axes.plot(dates, values, color=colors["green"], linewidth=1.45, zorder=3)
    baseline = min(value for _, value in finite) * 0.88
    axes.fill_between(dates, values, baseline, color=colors["green"], alpha=0.065, zorder=1)
    axes.scatter([latest_day], [latest_pe], s=38, color=colors["green"],
                 edgecolor="white", linewidth=1, zorder=5)
    for percentile, value, color in zip((20, 50, 80), quantiles,
                                       ("#95AA91", "#69836D", "#AFA47B")):
        axes.axhline(value, color=color, linewidth=1, linestyle=(0, (4, 4)), zorder=2)
        axes.text(1.012, value, f"P{percentile}  {value:.2f}",
                  transform=axes.get_yaxis_transform(), va="center", fontsize=10,
                  color=color, fontproperties=font)
    axes.set_ylim(baseline, max(value for _, value in finite) * 1.10)
    if len(dates) == 1:
        axes.set_xlim(dates[0] - timedelta(days=1), dates[0] + timedelta(days=1))
    else:
        axes.set_xlim(dates[0], dates[-1])
    axes.set_ylabel("滚动市盈率（倍）" if chinese else "Rolling P/E (×)",
                    fontproperties=font, color=colors["muted"], labelpad=12)
    locator = mdates.AutoDateLocator(minticks=6, maxticks=10)
    axes.xaxis.set_major_locator(locator)
    axes.xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator))
    axes.grid(axis="y", color=colors["grid"], linewidth=0.8)
    axes.tick_params(axis="both", colors=colors["muted"], length=0, pad=10, labelsize=10)
    for side in ("top", "right"):
        axes.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        axes.spines[side].set_color(colors["grid"])

    text(0.075, 0.145,
         "分位线：全部有效样本的 20% / 50% / 80% 分位数。缺失值保留为空，曲线不插值。" if chinese
         else "P20 / P50 / P80: quantiles of all valid observations. Missing values remain gaps; no interpolation.",
         10, color=colors["muted"])
    text(0.075, 0.102,
         f"数据源：{profile['source_cn']}，经 AKShare 获取 · 日度滚动市盈率 · 手动更新" if chinese
         else f"Source: {profile['source_en']} via AKShare · Daily rolling P/E · Manual updates", 10,
         color=colors["muted"])
    text(0.075, 0.061, profile["domain"] + "  /  " + code + (
         "  ·  源站可用日度记录，不代表上市以来完整历史" if chinese and code == "002415" else ""),
         9, color=colors["muted"])

    description = (
        f"{profile['english']} | Rolling P/E (PE TTM) | latest={latest_pe:.2f} on {latest_day} | "
        f"valid={valid_count}; missing={missing_count} | "
        + "; ".join(f"P{percentile}={value:.2f}" for percentile, value in zip((20, 50, 80), quantiles))
        + f" | Source: {profile['domain']} via AKShare; manual updates; missing values are gaps."
    )
    buffer = io.BytesIO()
    figure.savefig(buffer, format="png", dpi=160, facecolor=figure.get_facecolor(),
                   metadata={"Software": "ASockPEView", "Description": description})
    figure.clear()
    destination = Path(root) / "docs" / profile["filename"]
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="wb", prefix=".pe-history-", suffix=".tmp",
                                         dir=destination.parent, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(buffer.getvalue())
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Render a preview from local data without fetching.")
    parser.add_argument("--symbol", choices=list(PROFILES), default="000985")
    args = parser.parse_args()
    project_root = Path(__file__).resolve().parent
    profile = PROFILES[args.symbol]
    cached = json.loads((project_root / "web" / profile["data"]).read_text(encoding="utf-8"))
    render_preview(project_root, cached)
    print(f"Generated docs/{profile['filename']} from local cache.")
