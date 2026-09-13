# Quant Engine — SDLC Makefile
# Usage: make <target>

PYTHON ?= python3

.PHONY: test lint typecheck backtest sdlc-check clean ci

## Run the full test suite (all phases)
test:
	$(PYTHON) -m unittest discover -s tests -v

## Lint with ruff (fast, pyflakes + pycodestyle + isort rules)
lint:
	@command -v ruff >/dev/null 2>&1 || { echo "Installing ruff..."; $(PYTHON) -m pip install ruff -q; }
	ruff check quant_engine tests

## Static type checking with mypy
typecheck:
	@command -v mypy >/dev/null 2>&1 || { echo "Installing mypy..."; $(PYTHON) -m pip install mypy -q; }
	mypy quant_engine --ignore-missing-imports --strict-optional

## Run a sample MCX CrudeOil backtest
backtest:
	$(PYTHON) -c 'from quant_engine.backtest import Backtest; from quant_engine.models import Bar, ContractSpec, D; bars = [Bar(i, D(5000+i*10), D(5020+i*10), D(4990+i*10), D(5000+i*10), D(100)) for i in range(50)]; res = Backtest(ContractSpec.mcx_crudeoil("2026-03-31"), D(2), D(20), D("0.01")).run(bars, lambda h, p: "BUY" if len(h) % 10 == 5 else ("SELL" if len(h) % 10 == 0 else None)); print(f"Realized PnL: ₹{res.realized_pnl}, Costs: ₹{res.costs}, Fills: {res.fills}")'

## Run SDLC regression agent (baseline vs current backtest)
sdlc-check:
	$(PYTHON) -m quant_engine.sdlc_agent

## Full CI pipeline (lint → typecheck → test)
ci: lint typecheck test

## Remove Python bytecode caches
clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete
