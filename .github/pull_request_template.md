## What and why

<!-- One paragraph. Link the issue: Closes #123 -->

Closes #

## Test evidence

<!-- Paste the output, don't just claim it. TDD: the test came first. -->

```
uv run pytest
cd frontend && npm test
```

- [ ] New behaviour has a failing-first test
- [ ] `uv run ruff check . && uv run mypy` clean
- [ ] `npm run lint && npm run typecheck` clean (if the frontend changed)
- [ ] e2e smoke still passes (if the API or shell changed)

## Notes for review

<!-- Trade-offs, follow-ups, anything deliberately left out. -->
