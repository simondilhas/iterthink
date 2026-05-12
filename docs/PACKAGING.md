# Packaging and releases

Iterthink is a [Flet](https://flet.dev/) desktop app. Python dependencies and Flet metadata live in [`pyproject.toml`](../pyproject.toml). Install the project in editable mode for development:

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -U pip
pip install -r requirements.txt    # installs the package as -e .
```

Or: `pip install -e .`

## Flet desktop builds

Build on the **same OS family** as the target (Flet’s [platform matrix](https://flet.dev/docs/publish/)):

| Command | Run on | Notes |
|---------|--------|--------|
| `flet build linux` | Linux (or WSL) | Flutter + **Linux desktop toolchain** (CMake, Ninja, Clang, GTK 3 dev, etc.) |
| `flet build windows` | **Windows only** (not cross-compilable from Linux) | [Visual Studio 2022+](https://flet.dev/docs/publish/windows/) with **Desktop development with C++**; enable **Developer Mode** if symlink errors appear |

From the repository root:

```bash
flet build linux --yes
# or
flet build windows --yes
```

Use `--yes` so Flutter SDK bootstrap and other prompts do not block scripts or CI. Omit it when you want to confirm each step interactively.

### Linux toolchain (Fedora example)

`flutter doctor` must show the Linux desktop toolchain as usable. On Fedora 44+ you typically need packages along the lines of:

```bash
sudo dnf install cmake ninja-build clang gcc-c++ gtk3-devel mesa-libGL-devel \
  pkgconf-pkg-config zlib-devel
```

Install any additional packages `flutter doctor -v` lists (for example Mesa/EGL utilities). Debian or Ubuntu use the `apt install` names Flutter prints (`clang`, `cmake`, `ninja-build`, `libgtk-3-dev`, …).

Artifacts are written under **`build/<platform>/`** (the `build/` directory is recreated on each build).

### Project-specific settings

- **Entry:** [`main.py`](../main.py) (`ft.run`) — default Flet module name `main`.
- **App root:** `.` — entire tree is packaged except paths listed under `[tool.flet.app] exclude` in `pyproject.toml` (e.g. `old/`, `docs/`, `build/`).
- **Identity:** `[tool.flet]` `product`, `org`, `company` — adjust `org` before publishing.

## Ollama

The app expects a running Ollama API (default `http://localhost:11434`). Environment variables such as `OLLAMA_HOST` and `OLLAMA_MODEL` are described in [`main.py`](../main.py).

- **Install:** follow [Ollama’s docs](https://docs.ollama.com/) for your OS.

After Ollama is installed, pull at least one model, for example:

```bash
ollama pull llama3.2
```

## Windows build workflow (summary)

1. Install Visual Studio workload and Flutter prerequisites per Flet docs.
2. From repo root: `flet build windows --yes`.
3. Distribute the output under `build/windows/` (layout depends on Flet version and template — inspect that directory after a successful build). Users install Ollama separately per Ollama’s documentation.

## Linux build workflow (summary)

1. From repo root: `flet build linux --yes`.
2. Distribute the contents of `build/linux/` (or the archive/AppImage Flet produces for your template — inspect `build/linux` after a successful build).
3. Point users at Ollama installation (official instructions above).
