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

## CI and releases

### Day-to-day on `dev`

Push to **`dev`** runs **pytest** only (fast feedback).

### Ship to `main`

When `dev` is ready:

```bash
git tag v0.0.8
git push origin v0.0.8
```

Pushing a **`v*`** tag starts one pipeline: **pytest → desktop build (all platforms) → GitHub Release → auto-merge `dev` into `main`**.

If any step fails, **`main` is not updated**. Tag a commit on **`dev`** only.

### Manual desktop build

1. Open **Actions** → workflow **Desktop build** → **Run workflow**.
2. Under **Use workflow from**, pick the branch that contains the commit you want.
3. Optionally set **ref** to a SHA, branch, or tag; leave empty for that branch’s HEAD.
4. Optionally set **platforms** to `linux`, `windows`, `macos`, or **all**.
5. Download **Artifacts** when the run finishes.

Tag pushes always build all three platforms and attach installers to a GitHub Release. Details: [docs/PACKAGING.md](docs/PACKAGING.md).

## Pull requests

1. **Fork** the repository and create a **branch** for your change.
2. Keep changes **focused** on one topic when possible.
3. Describe **what** changed and **why** in the PR body (link related issues if any).
4. Match existing **style** and patterns in the codebase.
5. Avoid unrelated refactors or formatting-only sweeps in the same PR as functional changes.

For substantial design or behavior changes, opening an **issue** first helps align expectations.

## Questions

Use [GitHub Issues](https://github.com/iterthink/iterthink/issues) for bug reports and feature discussions.
