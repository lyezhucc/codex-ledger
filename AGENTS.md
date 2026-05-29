# AGENTS.md

This repository contains **codex-ledger**, a local Codex CLI token usage analyzer.

When working in this repository:

1. **Preserve the delta-based token accounting logic.** Do not sum `total_token_usage` events directly.
2. **Treat `cached_input_tokens` as a subset of `input_tokens`.** They are already included in the total.
3. **Use `total_tokens` as the main total.**
4. **Keep the tool pure Python standard library** unless the user explicitly asks for dependencies.
5. **Add tests for every accounting edge case.** Fixtures go in `tests/fixtures/<scenario>/`.
6. **Never upload local Codex logs externally.**
7. **Prefer accurate full scans over fast path filtering.** `--fast-path-filter` must remain optional and documented as potentially lossy.
8. **For user-facing usage reports**, summarize `grand_total.csv`, `model_total.csv`, and `daily_by_model.csv`.

## Common command

```bash
python3 codex_usage_local.py \
  --since 2026-05-03 \
  --tz Asia/Shanghai \
  --out ~/Desktop/codex-ledger-report \
  --json \
  ~/.codex
```

## Project structure

```
codex-ledger/
├── codex_usage_local.py          # Main CLI tool
├── AGENTS.md                     # This file — for coding agents
├── README.md                     # Human-readable documentation
├── LICENSE                       # MIT
├── .gitignore
├── run_tests.sh                  # One-click test runner
├── skills/
│   └── codex-ledger/
│       ├── SKILL.md              # Skill definition for AI agents
│       ├── scripts/              # Symlinks to main script
│       ├── templates/            # Output templates
│       └── examples/             # Sample commands
└── tests/
    ├── fixtures/                 # Test fixtures by scenario
    │   ├── sample/
    │   ├── payload_type_only/
    │   ├── missing_fields/
    │   ├── negative_delta_no_pop/
    │   ├── suspicious_fork_pop/
    │   ├── suspicious_then_normal/
    │   └── since_boundary/
    ├── test_sample.py
    ├── test_payload_type_only.py
    ├── test_missing_fields.py
    ├── test_negative_delta_no_pop.py
    ├── test_suspicious_fork_pop.py
    ├── test_suspicious_then_normal.py
    └── test_since_boundary.py
```

## Core algorithm

`total_token_usage` in Codex rollout JSONL is **cumulative per rollout file**. The correct accounting:

- Normal: `delta = current_total - previous_total`
- Delta == 0: skip (duplicate broadcast)
- Delta < 0 + previous was suspicious (>5M): pop previous, use current as baseline
- Delta < 0 + previous was normal: keep previous, use current as new segment

## Key data structure

token_count event in rollout JSONL:

```json
{
  "timestamp": "2026-05-03T05:13:25.683Z",
  "type": "event_msg",
  "payload": {
    "type": "token_count",
    "info": {
      "total_token_usage": {
        "input_tokens": 16418,
        "cached_input_tokens": 6016,
        "output_tokens": 358,
        "reasoning_output_tokens": 123,
        "total_tokens": 16776
      }
    }
  }
}
```

Model info comes from recursive search of any JSON line for a `model` field (commonly in `turn_context` or `session_meta` events).
