#!/bin/bash
# ============================================================
# Auto-Trader Bot — Project Scaffolding Script
# Run this once to set up the full project structure.
# Usage: bash scaffold.sh
# ============================================================

set -e

echo "🔧 Scaffolding auto-trader project..."

# Create directory structure
mkdir -p config src data/daily_summary tests

# Move config files into place
cp settings.yaml config/settings.yaml
cp env.template config/.env
echo "  ✅ Config files created"

# Create empty __init__.py files
touch src/__init__.py
touch tests/__init__.py

# Create placeholder source files
for f in main.py data_client.py scanner.py sentiment.py risk_manager.py executor.py portfolio.py logger.py; do
  if [ ! -f "src/$f" ]; then
    cat > "src/$f" << 'PYEOF'
"""
Auto-Trader Bot
Module: ${f%.py}
See CLAUDE.md for architecture and PLAN.md for full spec.
"""
PYEOF
    echo "  ✅ src/$f created"
  fi
done

# Create placeholder test files
for f in test_data_client.py test_scanner.py test_sentiment.py test_risk_manager.py test_executor.py; do
  if [ ! -f "tests/$f" ]; then
    cat > "tests/$f" << 'PYEOF'
"""Tests — see CLAUDE.md Phase Tracker for what to test."""
import pytest
PYEOF
    echo "  ✅ tests/$f created"
  fi
done

# Create empty data files
echo "[]" > data/trades.json
echo "[]" > data/watchlist.json
echo "  ✅ Data files initialized"

# Set up Python virtual environment
if [ ! -d ".venv" ]; then
  python3 -m venv .venv
  echo "  ✅ Virtual environment created"
fi

# Install dependencies
source .venv/bin/activate
pip install -r requirements.txt --quiet
echo "  ✅ Dependencies installed"

# Initialize git
if [ ! -d ".git" ]; then
  git init
  git add -A
  git commit -m "Initial scaffold: project structure, configs, and dependencies

Co-Authored-By: Claude <noreply@anthropic.com>"
  echo "  ✅ Git initialized with initial commit"
fi

echo ""
echo "🚀 Project scaffolded! Next steps:"
echo "   1. Fill in API keys: config/.env"
echo "   2. Activate venv: source .venv/bin/activate"
echo "   3. Start building: open in Claude Code"
echo ""
echo "   Claude Code will read CLAUDE.md automatically and"
echo "   pick up from Phase 1 in the Phase Tracker."
