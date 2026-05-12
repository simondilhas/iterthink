# Release tag quick guide

The **Desktop build** workflow (`.github/workflows/desktop-build.yml`) runs when you **push a Git tag** whose name starts with **`v`**. A successful run for such a tag also creates a **GitHub Release** (generated notes + uploaded desktop artifacts).

## Tag naming

- Use a leading **`v`**: `v0.1.0`, `v1.2.3`, `v2.0.0-rc.1`.
- Tags that do **not** match `v*` do **not** trigger this workflow.

Keep the tag aligned with the shipped version in [`pyproject.toml`](../pyproject.toml) (`[project].version`) when you intend the installers to match that version string.

## Create and push the tag

From a clean checkout on the commit you want to release (usually `main` after merging):

**Tag the current `HEAD`:**

```bash
git pull origin main   # or your release branch
git tag -a v0.2.0 -m "Release v0.2.0"
git push origin v0.2.0
```

**Tag a specific commit:**

```bash
git tag -a v0.2.0 -m "Release v0.2.0" <commit-sha>
git push origin v0.2.0
```

Use **annotated** tags (`-a`) so the tag carries a message and standard tooling treats it as a release object.

## What happens next

1. **Actions** → **Desktop build** should show a new run for `refs/tags/v0.2.0`.
2. The workflow builds **Linux**, **Windows**, and **macOS** targets for tag pushes (full matrix).
3. When all build jobs finish, the **release** job publishes a GitHub Release (for example **Iterthink v0.2.0** when the tag is `v0.2.0`) with the zip artifacts attached.

## Without a tag

You can still run the workflow manually: **Actions** → **Desktop build** → **Run workflow**. That path does not create a GitHub Release from the workflow’s release job; see [CONTRIBUTING.md](../CONTRIBUTING.md#github-actions-desktop-builds). For SemVer-style **ref** inputs (e.g. `v0.2.0`), the tag must already exist on `origin`—push the tag first, then dispatch if needed.

## Related docs

- [PACKAGING.md](PACKAGING.md) — local Flet builds and distribution notes.
- [CONTRIBUTING.md](../CONTRIBUTING.md#github-actions-desktop-builds) — manual workflow runs and artifact names.
