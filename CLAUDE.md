# Quant execution development protocol

Use Claude Code (or an equivalent coding agent) as an implementation assistant, not as a trading decision maker.

For every change:

1. Read the affected module and its tests first.
2. State the invariant being protected: tick/lot validity, idempotency, no lookahead, position cap, kill switch, or paisa reconciliation.
3. Make the smallest change that satisfies the requirement.
4. Add a regression test reproducing the previous failure or risk.
5. Run the complete test suite before proposing the change.
6. Do not add broker credentials, live-order switches, or production endpoints to source control.

All live-market connectivity must begin in paper mode and have an explicit operator-controlled kill switch.
