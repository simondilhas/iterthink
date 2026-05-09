# Contributing

Thanks for helping improve iterthink. By contributing, you agree that your contributions are licensed under the Business Source License 1.1 with the same Parameters as `LICENSE` (Additional Use Grant, Change Date, Change License).

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

## Tests

Install the optional dev extra (includes pytest), then run the suite from the repository root:

```bash
pip install -e ".[dev]"
pytest tests/
```

## GitHub Actions desktop builds

Maintainers can produce **Linux**, **Windows**, and **macOS** desktop bundles (Flet) from CI.

1. Open **Actions** → workflow **Desktop build** → **Run workflow**.
2. Under **Use workflow from**, pick the **branch** (or default branch) that contains the workflow and the commit you want; that branch’s **HEAD** is checked out unless you override the next field.
3. Optionally set **ref** to a specific commit SHA, branch name, or tag to check out instead of that HEAD.
4. Optionally set **platforms** to build only one OS (`linux`, `windows`, or `macos`) or **all**. Pushes of version tags (`v*`) always build all three platforms for the release job.
5. When the run finishes, download the **Artifacts** (`desktop-linux`, `desktop-windows`, `desktop-macos`; only the jobs that ran appear for partial builds).

Manual runs use zip names like `iterthink-0.0.0+<run>-<os>.zip`. Tag pushes attach the same zips to a GitHub Release. Details: [docs/PACKAGING.md](docs/PACKAGING.md).

## Pull requests

1. **Fork** the repository and create a **branch** for your change.
2. Keep changes **focused** on one topic when possible.
3. Describe **what** changed and **why** in the PR body (link related issues if any).
4. Match existing **style** and patterns in the codebase.
5. Avoid unrelated refactors or formatting-only sweeps in the same PR as functional changes.

For substantial design or behavior changes, opening an **issue** first helps align expectations.

## Questions

Use [GitHub Issues](https://github.com/iterthink/iterthink/issues) for bug reports and feature discussions.
