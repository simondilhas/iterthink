# iterthink

See every change. Understand the impact. Act on what matters.

iterthink is a local review layer for documents and models. Most tools rewrite everything at once — you get back something clean, but you can't see what changed or whether the meaning held. iterthink shows you exactly what the AI touched, word by word, and tells you whether the intent shifted. When a change matters, you can trigger a workflow directly in [{yourcompany}os](https://yourcompanyos.io).

Nothing leaves your machine.

---

## What it does

- Write or import documents in plain markdown
- Send any paragraph to a local AI (via Ollama — private, no API key)
- See word-level diff: what was added, what was removed
- Get a semantic signal: `STABLE` (intent held) or `NEW` (meaning shifted)
- Accept, reject, or edit — you stay in control
- Every AI action auto-saves a version snapshot
- Compare any two versions side by side
- Trigger workflows in {yourcompany}os when a change needs action

---

## Install

**Prerequisites:** Python **3.11+** and [Ollama](https://ollama.com) installed and running locally.

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

---

## Why local

No cloud. No account. No API key. Your files stay on your machine. The AI runs locally via Ollama — private by default, not by policy.

---

## Version history

Every document keeps a full version timeline. Every AI action creates a snapshot. You can also save manually with a label.

Compare any two versions — your edit vs. the AI's, Tuesday's draft vs. today's. Same diff, same semantic signal.

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

Licensed under the Apache License 2.0. See [LICENSE](LICENSE) for details.

---

## Contributing

iterthink is fully open source. Read the code, trust it, improve it. See [CONTRIBUTING.md](CONTRIBUTING.md) for setup and pull request guidelines.

[GitHub Issues →](https://github.com/iterthink/iterthink/issues)

---

*iterthink — the review layer for documents and models.*