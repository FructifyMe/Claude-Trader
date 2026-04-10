"""Generate a self-refreshing HTML dashboard from bot logs and Alpaca data."""

import json
import os
import logging
from datetime import datetime

log = logging.getLogger(__name__)

DASHBOARD_PATH = os.path.join(os.path.dirname(__file__), "..", "dashboard.html")
TRADES_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "trades.json")
BOT_LOG_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "bot.log")


def generate_dashboard(account: dict, positions: list, trades: list = None,
                       bot_status: str = "Running"):
    """Write dashboard.html with current state. Called after each scan cycle."""
    if trades is None:
        trades = _load_trades()

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    equity = account.get("equity", 0)
    cash = account.get("cash", 0)
    starting = 100000.0
    total_pnl = equity - starting
    total_pnl_pct = (total_pnl / starting) * 100 if starting > 0 else 0
    pnl_color = "#22c55e" if total_pnl >= 0 else "#ef4444"

    # Build positions HTML
    pos_rows = ""
    for p in positions:
        pl_color = "#22c55e" if p["unrealized_pl"] >= 0 else "#ef4444"
        pos_rows += f"""
        <tr>
          <td style="font-weight:600">{p['symbol']}</td>
          <td>{p['qty']:.0f}</td>
          <td>${p['avg_entry']:.2f}</td>
          <td>${p['current_price']:.2f}</td>
          <td style="color:{pl_color};font-weight:600">${p['unrealized_pl']:.2f} ({p['unrealized_plpc']*100:.1f}%)</td>
        </tr>"""
    if not positions:
        pos_rows = '<tr><td colspan="5" style="text-align:center;color:#94a3b8;padding:20px">No open positions</td></tr>'

    # Build trade log HTML (most recent first, limit 30)
    trade_entries = [t for t in trades if t.get("action") in ("BUY", "SELL")]
    trade_entries = trade_entries[-30:][::-1]
    trade_rows = ""
    for t in trade_entries:
        action = t.get("action", "")
        action_color = "#3b82f6" if action == "BUY" else "#f59e0b"
        pnl_str = ""
        if "pnl" in t:
            pnl_c = "#22c55e" if t["pnl"] >= 0 else "#ef4444"
            pnl_str = f'<span style="color:{pnl_c}">${t["pnl"]:.2f}</span>'
        ts = t.get("timestamp", "")[:19]
        trade_rows += f"""
        <tr>
          <td style="color:#94a3b8">{ts}</td>
          <td><span style="color:{action_color};font-weight:600">{action}</span></td>
          <td style="font-weight:600">{t.get('symbol','')}</td>
          <td>{t.get('shares','')}</td>
          <td>${t.get('price',0):.2f}</td>
          <td>{pnl_str}</td>
          <td style="color:#94a3b8;font-size:12px">{t.get('reason','')}</td>
        </tr>"""
    if not trade_entries:
        trade_rows = '<tr><td colspan="7" style="text-align:center;color:#94a3b8;padding:20px">No trades yet — bot is scanning for opportunities</td></tr>'

    # Build decisions log (last 15)
    decisions = [t for t in trades if t.get("type") == "decision"][-15:][::-1]
    dec_rows = ""
    for d in decisions:
        ts = d.get("timestamp", "")[:19]
        dec_color = "#22c55e" if d.get("decision") == "BUY" else "#94a3b8"
        dec_rows += f"""
        <tr>
          <td style="color:#94a3b8">{ts}</td>
          <td style="color:{dec_color}">{d.get('decision','')}</td>
          <td style="font-weight:600">{d.get('symbol','')}</td>
          <td>{d.get('reason','')}</td>
        </tr>"""

    # Recent bot log lines
    log_lines = _tail_log(20)
    log_html = "\n".join(f"<div class='log-line'>{_escape(l)}</div>" for l in log_lines)

    # Status indicator
    status_color = "#22c55e" if bot_status == "Running" else "#f59e0b"

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta http-equiv="refresh" content="30">
<title>Auto-Trader Dashboard</title>
<style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{ font-family:'Segoe UI',system-ui,-apple-system,sans-serif; background:#0f172a; color:#e2e8f0; padding:24px; }}
  .header {{ display:flex; justify-content:space-between; align-items:center; margin-bottom:24px; }}
  .header h1 {{ font-size:24px; font-weight:700; }}
  .header h1 span {{ color:#3b82f6; }}
  .status {{ display:flex; align-items:center; gap:8px; font-size:14px; }}
  .status-dot {{ width:10px; height:10px; border-radius:50%; background:{status_color}; }}
  .cards {{ display:grid; grid-template-columns:repeat(4,1fr); gap:16px; margin-bottom:24px; }}
  .card {{ background:#1e293b; border-radius:12px; padding:20px; }}
  .card .label {{ font-size:12px; color:#94a3b8; text-transform:uppercase; letter-spacing:1px; margin-bottom:8px; }}
  .card .value {{ font-size:28px; font-weight:700; }}
  .card .sub {{ font-size:13px; color:#94a3b8; margin-top:4px; }}
  .section {{ background:#1e293b; border-radius:12px; padding:20px; margin-bottom:16px; }}
  .section h2 {{ font-size:16px; font-weight:600; margin-bottom:12px; color:#f8fafc; }}
  table {{ width:100%; border-collapse:collapse; font-size:14px; }}
  th {{ text-align:left; padding:8px 12px; color:#94a3b8; font-weight:500; font-size:12px;
       text-transform:uppercase; letter-spacing:0.5px; border-bottom:1px solid #334155; }}
  td {{ padding:8px 12px; border-bottom:1px solid #1e293b; }}
  .log-box {{ background:#0f172a; border-radius:8px; padding:12px; max-height:250px; overflow-y:auto;
              font-family:'Cascadia Code','Fira Code',monospace; font-size:12px; line-height:1.6; }}
  .log-line {{ color:#94a3b8; }}
  .footer {{ text-align:center; color:#475569; font-size:12px; margin-top:24px; }}
</style>
</head>
<body>
  <div class="header">
    <h1><span>Auto-Trader</span> Dashboard</h1>
    <div class="status">
      <div class="status-dot"></div>
      <span>{bot_status}</span>
      <span style="color:#475569">| Updated {now}</span>
    </div>
  </div>

  <div class="cards">
    <div class="card">
      <div class="label">Portfolio Value</div>
      <div class="value">${equity:,.2f}</div>
      <div class="sub">Paper Trading</div>
    </div>
    <div class="card">
      <div class="label">Total P&L</div>
      <div class="value" style="color:{pnl_color}">${total_pnl:,.2f}</div>
      <div class="sub" style="color:{pnl_color}">{total_pnl_pct:+.2f}%</div>
    </div>
    <div class="card">
      <div class="label">Cash Available</div>
      <div class="value">${cash:,.2f}</div>
      <div class="sub">{len(positions)} open position{'s' if len(positions)!=1 else ''}</div>
    </div>
    <div class="card">
      <div class="label">Trades Today</div>
      <div class="value">{len([t for t in trade_entries if t.get('timestamp','').startswith(datetime.now().strftime('%Y-%m-%d'))])}</div>
      <div class="sub">{len(trade_entries)} total</div>
    </div>
  </div>

  <div class="section">
    <h2>Open Positions</h2>
    <table>
      <tr><th>Symbol</th><th>Shares</th><th>Entry</th><th>Current</th><th>P&L</th></tr>
      {pos_rows}
    </table>
  </div>

  <div class="section">
    <h2>Trade History</h2>
    <table>
      <tr><th>Time</th><th>Action</th><th>Symbol</th><th>Shares</th><th>Price</th><th>P&L</th><th>Reason</th></tr>
      {trade_rows}
    </table>
  </div>

  <div class="section">
    <h2>Scanner Decisions</h2>
    <table>
      <tr><th>Time</th><th>Decision</th><th>Symbol</th><th>Reason</th></tr>
      {dec_rows if dec_rows else '<tr><td colspan="4" style="text-align:center;color:#94a3b8;padding:20px">No decisions logged yet</td></tr>'}
    </table>
  </div>

  <div class="section">
    <h2>Bot Log</h2>
    <div class="log-box">
      {log_html if log_html else '<div class="log-line">Waiting for bot output...</div>'}
    </div>
  </div>

  <div class="footer">
    Auto-refreshes every 30 seconds | Auto-Trader Bot v1.0 | Paper Trading Mode
  </div>
</body>
</html>"""

    with open(DASHBOARD_PATH, "w") as f:
        f.write(html)


def _load_trades() -> list:
    if not os.path.exists(TRADES_PATH):
        return []
    try:
        with open(TRADES_PATH) as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return []


def _tail_log(n: int = 20) -> list[str]:
    if not os.path.exists(BOT_LOG_PATH):
        return []
    try:
        with open(BOT_LOG_PATH) as f:
            lines = f.readlines()
        return [l.rstrip() for l in lines[-n:]]
    except IOError:
        return []


def _escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
