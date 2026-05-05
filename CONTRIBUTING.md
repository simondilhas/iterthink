# Contributing

Thanks for helping improve iterthink. By contributing, you agree that your contributions are licensed under the Apache License 2.0 (see `LICENSE`).

## Before you start

- **Python 3.11+** and a working clone of this repository.
- **[Ollama](https://ollama.com)** installed and running locally if you exercise AI features.
- Do **not** commit secrets, API keys, machine-specific paths, or personal data. Use `.gitignore`d paths for local settings and databases.

## Run from source

```bash
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -U pip
pip install -e .
# or: pip install -r requirements.txt
iterthink
# or: python -m iterthink
```

## Pull requests

1. **Fork** the repository and create a **branch** for your change.
2. Keep changes **focused** on one topic when possible.
3. Describe **what** changed and **why** in the PR body (link related issues if any).
4. Match existing **style** and patterns in the codebase.
5. Avoid unrelated refactors or formatting-only sweeps in the same PR as functional changes.

For substantial design or behavior changes, opening an **issue** first helps align expectations.

## Questions

Use [GitHub Issues](https://github.com/iterthink/iterthink/issues) for bug reports and feature discussions.
