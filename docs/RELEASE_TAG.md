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

## Signed release builds (tag pushes)

Tag pushes (`v*`) sign **Windows** installers before publishing the GitHub Release. **macOS** is built only when `MACOS_RELEASE_ENABLED: "true"` in `.github/workflows/desktop-build.yml` (currently `false`; use workflow_dispatch **platforms: macos** to test locally). Configure these repository secrets under **Settings → Secrets and variables → Actions**:

| Secret | Purpose |
|--------|---------|
| `MACOS_CERTIFICATE` | Base64 of a **Developer ID Application** `.p12` (certificate + private key) |
| `MACOS_CERTIFICATE_PWD` | Password used when exporting the `.p12` |
| `MACOS_CERTIFICATE_NAME` | Codesign identity string, e.g. `Developer ID Application: Your Name (TEAMID)` |
| `APPLE_ID` | Apple ID email used for notarization |
| `APPLE_APP_SPECIFIC_PASSWORD` | App-specific password from [appleid.apple.com](https://appleid.apple.com) |
| `APPLE_TEAM_ID` | 10-character Team ID |
| `ES_USERNAME`, `ES_PASSWORD`, `ES_CREDENTIAL_ID`, `ES_TOTP_SECRET` | SSL.com eSigner (Windows Authenticode) |

### macOS certificate export (on a Mac)

1. In **Keychain Access**, export the **Developer ID Application** cert as `.p12` and note the export password.
2. Encode without line breaks:

   ```bash
   base64 -i DeveloperID.p12 | tr -d '\n' | pbcopy
   ```

3. Paste into `MACOS_CERTIFICATE`. Set `MACOS_CERTIFICATE_PWD` to the **same** export password (no trailing newline).
4. Confirm locally before updating secrets:

   ```bash
   openssl pkcs12 -in DeveloperID.p12 -noout -passin pass:'YOUR_EXPORT_PASSWORD'
   ```

If CI reports `MAC verification failed during PKCS12 import`, the `.p12` base64 or `MACOS_CERTIFICATE_PWD` does not match the exported file—re-export and update both secrets.

## Related docs

- [PACKAGING.md](PACKAGING.md) — local Flet builds and distribution notes.
- [CONTRIBUTING.md](../CONTRIBUTING.md#github-actions-desktop-builds) — manual workflow runs and artifact names.
