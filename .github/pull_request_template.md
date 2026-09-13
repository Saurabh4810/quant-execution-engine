## Description
<!-- Provide a concise summary of the changes introduced in this PR. -->

## Invariant Protection Checklist
<!-- Per CLAUDE.md quant execution protocol, confirm all applicable invariants: -->
- [ ] **Paisa Precision**: All accounting uses `Decimal` and rounds via `paisa()` without float drift.
- [ ] **No Lookahead Bias**: Signals only consume historical completed bars (`history[:-1]` or incremental `update()`).
- [ ] **Idempotency**: Repeated order submissions with the same `client_order_id` yield cached results without duplicate broker calls.
- [ ] **Risk Controls**: Position caps and max daily loss limits are strictly enforced via `RiskGate`.
- [ ] **Back-Pressure & Concurrency**: Market data flows through bounded `asyncio.Queue`; producers block when queue is full.
- [ ] **Graceful Shutdown**: The consumer completely drains queued items before exit upon `stop()`.
- [ ] **No Hardcoded Secrets**: Zero API keys, secrets, or live credentials in source code. Credentials come from environment variables.

## Type of Change
- [ ] `feat`: New feature or trading capability
- [ ] `fix`: Bug fix / regression fix
- [ ] `test`: New tests or test suite expansion
- [ ] `refactor`: Code change that neither fixes a bug nor adds a feature
- [ ] `ci`: CI/CD pipeline or build tool change

## Test Verification
<!-- Attach test results or commands used to verify correctness -->
```bash
make test
make sdlc-check
```
- [ ] Full test suite passes (all unit & integration tests)
- [ ] SDLC regression check passes against locked baseline
