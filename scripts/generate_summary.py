#!/usr/bin/env python3
"""Generate selection summary report from AI hedge fund pipeline output.

Reads the analyst_signals from the pipeline state and formats a
Terminal Pro Dark themed summary report.

Usage:
    python scripts/generate_summary.py --date 2026-03-12 --market us
"""

import argparse
import json
from datetime import datetime
from pathlib import Path


def format_signal_icon(signal: str) -> str:
    if signal in ("bullish", "positive"):
        return "BULLISH"
    elif signal in ("bearish", "negative"):
        return "BEARISH"
    return "NEUTRAL"


def generate_text_summary(analyst_signals: dict, decisions: dict, date: str, market: str, model: str = "Qwen 3.5:9B") -> str:
    """Generate a text summary report."""
    lines = []
    lines.append("=" * 60)
    lines.append(f"  AI HEDGE FUND — Selection Summary ({date})")
    lines.append(f"  Model: {model} | Market: {market.upper()}")
    lines.append("=" * 60)
    lines.append("")

    if not analyst_signals:
        lines.append("No analyst signals available.")
        return "\n".join(lines)

    # Get all tickers from any agent's signals
    all_tickers = set()
    for agent_signals in analyst_signals.values():
        if isinstance(agent_signals, dict):
            all_tickers.update(agent_signals.keys())

    for ticker in sorted(all_tickers):
        # Find decision for this ticker
        decision_info = {}
        if decisions:
            for dec in decisions if isinstance(decisions, list) else [decisions]:
                if isinstance(dec, dict) and dec.get("ticker") == ticker:
                    decision_info = dec
                    break

        action = decision_info.get("action", "HOLD").upper()
        qty = decision_info.get("quantity", 0)
        dec_confidence = decision_info.get("confidence", 0)

        header = f"  {ticker}"
        if qty > 0:
            header += f"  DECISION: {action} (qty: {qty})"
        else:
            header += f"  DECISION: {action}"

        lines.append(f"▶ {header}")
        lines.append(f"  Confidence: {dec_confidence}%")
        lines.append("  " + "─" * 50)

        # Show each agent's signal for this ticker
        for agent_id, agent_data in sorted(analyst_signals.items()):
            if not isinstance(agent_data, dict) or ticker not in agent_data:
                continue

            ticker_signal = agent_data[ticker]
            if isinstance(ticker_signal, dict):
                signal = format_signal_icon(ticker_signal.get("signal", "neutral"))
                conf = ticker_signal.get("confidence", 0)
                reasoning = ticker_signal.get("reasoning", "")

                # Extract short reasoning
                short_reason = ""
                if isinstance(reasoning, dict):
                    for v in reasoning.values():
                        if isinstance(v, dict) and "signal" in v:
                            short_reason = f"— score-based"
                            break
                elif isinstance(reasoning, str):
                    short_reason = f"— {reasoning[:60]}" if reasoning else ""

                agent_name = agent_id.replace("_agent", "").replace("_", " ").title()
                lines.append(f"  {agent_name:25s} {signal:8s} ({conf:3.0f}%)  {short_reason}")

        lines.append("  " + "─" * 50)
        lines.append("")

    return "\n".join(lines)


def generate_html_summary(analyst_signals: dict, decisions: dict, date: str, market: str, model: str = "Qwen 3.5:9B") -> str:
    """Generate a Terminal Pro Dark themed HTML summary."""
    all_tickers = set()
    for agent_signals in analyst_signals.values():
        if isinstance(agent_signals, dict):
            all_tickers.update(agent_signals.keys())

    ticker_rows = ""
    for ticker in sorted(all_tickers):
        decision_info = {}
        if decisions:
            for dec in decisions if isinstance(decisions, list) else [decisions]:
                if isinstance(dec, dict) and dec.get("ticker") == ticker:
                    decision_info = dec
                    break

        action = decision_info.get("action", "HOLD").upper()
        qty = decision_info.get("quantity", 0)
        dec_confidence = decision_info.get("confidence", 0)

        action_class = "buy" if action == "BUY" else "sell" if action in ("SELL", "SHORT") else "hold"
        qty_str = f" (qty: {qty})" if qty > 0 else ""

        agent_rows = ""
        for agent_id, agent_data in sorted(analyst_signals.items()):
            if not isinstance(agent_data, dict) or ticker not in agent_data:
                continue
            ts = agent_data[ticker]
            if isinstance(ts, dict):
                signal = ts.get("signal", "neutral")
                conf = ts.get("confidence", 0)
                signal_class = "bullish" if signal in ("bullish", "positive") else "bearish" if signal in ("bearish", "negative") else "neutral"
                agent_name = agent_id.replace("_agent", "").replace("_", " ").title()
                agent_rows += f'<tr><td class="agent-name">{agent_name}</td><td class="signal {signal_class}">{signal.upper()}</td><td>{conf:.0f}%</td></tr>\n'

        ticker_rows += f"""
        <div class="ticker-card">
            <div class="ticker-header">
                <span class="ticker-symbol">{ticker}</span>
                <span class="decision {action_class}">{action}{qty_str}</span>
            </div>
            <div class="confidence">Confidence: {dec_confidence}%</div>
            <table class="signals-table">
                <thead><tr><th>Agent</th><th>Signal</th><th>Conf</th></tr></thead>
                <tbody>{agent_rows}</tbody>
            </table>
        </div>
        """

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AI Hedge Fund — {date}</title>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ background: #0a0e14; color: #e0e0e0; font-family: 'JetBrains Mono', 'Fira Code', monospace; padding: 2rem; }}
h1 {{ color: #e6b450; font-size: 1.5rem; margin-bottom: 0.5rem; }}
.meta {{ color: #666; margin-bottom: 2rem; }}
.ticker-card {{ background: #12161c; border: 1px solid #1a1f2a; border-radius: 8px; padding: 1.2rem; margin-bottom: 1rem; }}
.ticker-header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.5rem; }}
.ticker-symbol {{ color: #39bae6; font-size: 1.2rem; font-weight: bold; }}
.decision {{ padding: 0.2rem 0.8rem; border-radius: 4px; font-weight: bold; font-size: 0.85rem; }}
.decision.buy {{ background: #1a3a2a; color: #4ade80; }}
.decision.sell {{ background: #3a1a1a; color: #f87171; }}
.decision.hold {{ background: #2a2a1a; color: #facc15; }}
.confidence {{ color: #888; font-size: 0.85rem; margin-bottom: 0.8rem; }}
.signals-table {{ width: 100%; border-collapse: collapse; font-size: 0.8rem; }}
.signals-table th {{ text-align: left; color: #666; padding: 0.3rem 0; border-bottom: 1px solid #1a1f2a; }}
.signals-table td {{ padding: 0.3rem 0; border-bottom: 1px solid #0d1117; }}
.agent-name {{ color: #aaa; }}
.signal.bullish {{ color: #4ade80; }}
.signal.bearish {{ color: #f87171; }}
.signal.neutral {{ color: #facc15; }}
.watermark {{ text-align: right; color: rgba(255,255,255,0.18); font-size: 0.75rem; margin-top: 2rem; }}
</style>
</head>
<body>
<h1>AI Hedge Fund — Selection Summary</h1>
<div class="meta">{date} | Model: {model} | Market: {market.upper()}</div>
{ticker_rows}
<div class="watermark">AlpharvestPro</div>
</body>
</html>"""


def main():
    parser = argparse.ArgumentParser(description="Generate AI hedge fund summary report")
    parser.add_argument("--date", type=str, default=datetime.now().strftime("%Y-%m-%d"))
    parser.add_argument("--market", type=str, default="us")
    parser.add_argument("--input", type=str, default=None, help="Path to pipeline output JSON")
    parser.add_argument("--model", type=str, default="Qwen 3.5:9B")
    args = parser.parse_args()

    # Try to load pipeline output
    analyst_signals = {}
    decisions = {}

    if args.input and Path(args.input).exists():
        data = json.loads(Path(args.input).read_text())
        analyst_signals = data.get("analyst_signals", {})
        decisions = data.get("decisions", {})
    else:
        print("No pipeline output found. Generate a placeholder report.")
        print("Run the pipeline first, then pass --input with the output JSON.")

    # Generate reports
    output_dir = Path("output")
    output_dir.mkdir(exist_ok=True)

    # Text report
    text = generate_text_summary(analyst_signals, decisions, args.date, args.market, args.model)
    text_path = output_dir / f"summary_{args.date}.txt"
    text_path.write_text(text)
    print(text)
    print(f"\nSaved to {text_path}")

    # HTML report
    html = generate_html_summary(analyst_signals, decisions, args.date, args.market, args.model)
    html_path = output_dir / f"summary_{args.date}.html"
    html_path.write_text(html)
    print(f"HTML saved to {html_path}")


if __name__ == "__main__":
    main()
