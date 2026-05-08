# iterthink

See every change. Understand the impact. Act on what matters.

iterthink is a local review layer for documents. Most AI tools rewrite everything at once — you get back something clean, but you can't see what changed or whether the meaning held. iterthink shows you exactly what the AI touched, word by word, and tells you whether the intent shifted. When a change matters, you can trigger a workflow directly in [{yourcompany}os](https://yourcompanyos.io).


---

## What it does

- Write or import documents in plain Markdown
- Run **margin actions** on any paragraph (Discuss / Edit / Evaluate)—**Ollama** by default, no API key; optional cloud backends
- See paragraph and word-level diff: what was added, what was removed
- See **per-paragraph change kinds** (coloured pills in History / Review): alignment and word-level diff, then embeddings (and an LLM in the ambiguous band) to split heavy edits:
  - **`unchanged`** — shown as `—` when the matched paragraph is effectively the same
  - **`minor`** — matched paragraph, lighter edit
  - **`major`** — matched paragraph, same core intent but heavily rephrased or reordered
  - **`rewritten`** — matched paragraph where the main point, facts, recommendation, or stance materially changed
  - **`new`** / **`deleted`** — paragraph exists only in the newer or only in the older version
- Where evaluation runs, an **intent judge** labels pairs **`STABLE`** (core meaning and intent held) or **`NEW`** (main message, recommendation, or stance changed)
- Accept, reject, or edit — you stay in control
- Every AI action auto-saves a version snapshot
- Compare any two versions side by side
- Start a [{yourcompany}os](https://yourcompanyos.io) process from the editor when a change needs action

---

## Getting started (quick reference)

This is the shortest path from zero to a useful session—no architecture, just what to click.

1. **Install** — You need **Python 3.11+**. Create a virtual environment if you like, then `pip install iterthink` and start the app with `iterthink`. For **local** AI, install [Ollama](https://ollama.com), run it (`ollama serve`), and pull at least one **chat** model (for example `ollama pull llama3.2`). Exact copy-paste commands are in [Install](#install) below.

2. **Set up your working folder** — Open **File → Settings…**, then the **Paths** section in the sidebar. Point **documents** to where you want your Markdown files (for example your notes or project folder). The app also uses a small **store** folder next to it (by default under `Documents/.iterthink`) for settings, prompts, and version history—change it here if you need a different disk or layout.

3. **Connect Ollama or cloud models** — Still in **Settings**, open **Models**. Use the **Home · Office · Cloud** strip: **Home** is Ollama on your machine (pick chat and embedding models, and set **Ollama host** if it is not the default). **Cloud** is for remote providers when you use that tier; configure the vendor and models there. The same tier choice appears next to the AI panel in the main window so you can switch context without reopening Settings.

4. **Write your first text in Focus Area** — The center column is the **Focus Area** tab (between **History** and **Review**). Pick or create a `.md` file in the tree on the left, then type in the editor. That buffer is what the app compares to the last saved file and to each version snapshot.

5. **Use LLM support** — On the right, use the **topic** tabs and **action** pills to run paragraph-level actions (discuss, edit, evaluate—labels depend on your prompts). Use the **chat** area for free-form help. Actions use the tier you selected (**Home** / **Office** / **Cloud**). You can customize margin prompts under **Settings → Prompts** and the chat tone under **Settings → App**.

6. **Review and history in the tabs** — **History** shows an older snapshot next to your current draft, with diffs and semantic hints so you see what changed. **Focus Area** is where you compose. **Review** is where you work through pending AI-suggested edits (accept or decline) before they become your new baseline. Save when you are happy; AI actions also create snapshots automatically.

When something does not connect (especially Ollama), check that the service is running and that the model names in **Settings → Models** match what you pulled.

---

## Install

**Prerequisites:** Python **3.11+**. For **local** models (Settings → Models → **Home**), install [Ollama](https://ollama.com) and keep it running; cloud-only setups can skip Ollama.

Use a virtual environment (recommended):

```bash
python3 -m venv .venv
source .venv/bin/activate
```

On Windows (Command Prompt or PowerShell):

```bat
py -3.11 -m venv .venv
.\.venv\Scripts\activate
```

Then install and run:

```bash
pip install -U pip
pip install iterthink
ollama pull llama3.2
iterthink
```

Optional one-liner (macOS / Linux), after Ollama is running:

```bash
python3 -m venv .venv && source .venv/bin/activate && pip install -U pip iterthink && iterthink
```

**Where data lives:** settings under your OS config path (e.g. `~/.config/iterthink` on Linux, `~/Library/Application Support/iterthink` on macOS, `%APPDATA%\iterthink` on Windows); documents and the local database under `Documents/.iterthink`.

**From a clone (developers):** `pip install -e .` (or `pip install -r requirements.txt`), then `iterthink` or `python -m iterthink`.

## Status

- **Early:** The app is still evolving; defaults and behavior may change between releases.
- **Platforms:** Primary development and testing are on **Fedora Linux**. Windows and macOS steps above are documented for convenience but are **not** run through regular QA—if something fails there, [open an issue](https://github.com/iterthink/iterthink/issues).
- **AI backend:** **Ollama** is what we exercise most. Cloud models may work but are not covered in depth.

### Prompts (margin / KI actions)

There is no separate “skills” layer in iterthink—per-paragraph AI actions are **margin actions** defined in `prompts.yaml`.

| | |
|---|---|
| **Runtime file** | `<store_dir>/prompts.yaml` — default store is under `Documents/.iterthink` (see **File → Settings → Paths** for your actual `store_dir`). |
| **First install** | That file is created by copying the package default; changing files under `iterthink/defaults/` in a git clone does **not** update an existing store copy. Delete `prompts.yaml` in the store folder to re-seed from defaults, or edit the store file directly. |
| **In the app** | **File → Settings** → **Prompts** (margin / KI actions) — add rows, edit **system prompt** and **user template** (`{text}` is replaced with the paragraph). **Save prompts** writes `prompts.yaml`. |
| **By hand** | Valid YAML with a top-level `margin_actions:` list. Each entry needs `id`, `label`, `topic` (`discuss`, `change`, or `evaluate`), `system_prompt`, and `user_template` (must contain `{text}`). |

The chat sidebar uses the **Chat system prompt** on the **App** tab in Settings, separate from margin actions.

---

## Why local

No cloud. No account. No API key. Your files stay on your machine. The AI runs locally via Ollama — private by default, not by policy.

---

## Version history

Every document keeps a full version timeline. Every AI action creates a snapshot. You can also save manually with a label.

Compare any two versions — your edit vs. the AI's, Tuesday's draft vs. today's. Same diff, same paragraph pills (**unchanged** through **deleted**), and the same **`STABLE`** / **`NEW`** intent judge when that path is used.

---

## Who it's for

- Project teams in AEC and construction tracking changes across specifications and plans
- Non-native writers working in a second language who need to stay in control of their voice
- Journalists, essayists, and researchers with a point of view worth protecting
- Anyone who has opened `final_final_v7.docx` and wondered what happened

---

## Roadmap

- IFC model comparison — see what changed between two BIM models
- Sync and version history across devices
- Team review features for collaborative workflows
- Deeper {yourcompany}os integration — from change detection to closed decisions

---

## Part of the Abstract AG platform

iterthink is the review layer. It sits between your documents and your decisions.

| | |
|---|---|
| [Abstract BIM](https://abstractbim.com) | Normalize raw IFC data |
| [Pragmatic BIM](https://pragmaticbim.com) | Define BIM requirements |
| **iterthink** | Detect change, evaluate impact |
| [{yourcompany}os](https://yourcompanyos.io) | Act on change |

Raw data means nothing until it's clean, defined, reviewed, and acted on.

---

## License

Licensed under the [Business Source License 1.1](https://mariadb.com/bsl11/) (BUSL-1.1). Source is available; **individual, non-commercial** use is permitted under the Additional Use Grant in [LICENSE](LICENSE). After the **Change Date** (**2030-05-05**, four calendar years from **2026-05-05**), or the fourth anniversary of the first public distribution of that version under BUSL-1.1—**whichever comes first** per the license text—that version is available under the **Apache License 2.0**. Other production use requires a commercial license; contact **[Abstract AG](https://abstract.build)** at [info@abstract.build](mailto:info@abstract.build).

The BUSL-1.1 text governs trademarks and logos: there is **no trademark grant** beyond what that license expressly allows.

---

## Contributing

The source is public and reviewable. See [CONTRIBUTING.md](CONTRIBUTING.md) for setup and pull request guidelines (contributions are licensed under the same terms as the project).

[GitHub Issues →](https://github.com/iterthink/iterthink/issues)

---

*iterthink — the review layer for documents and models.*