# Domain docs

This repository uses a single-context domain documentation layout.

## Before exploring

- Read `CONTEXT.md` at the repository root when it exists.
- Read ADRs under `docs/adr/` that affect the area being changed.
- If these files do not exist, proceed without treating their absence as an error.

Use the vocabulary defined by `CONTEXT.md` in issues, tests, diagnostics, and refactoring proposals. If a proposal contradicts an ADR, identify that conflict explicitly.

Expected layout:

```text
/
├── CONTEXT.md
├── docs/adr/
└── agentl/
```
