# iterthink

You write. AI suggests. You decide.

Most AI writing tools rewrite everything at once. You get back something fluent and clean — but you can't see what changed. You can't tell if it's still yours.

iterthink shows you exactly what the AI touched, word by word. Then it tells you whether the meaning held or shifted.

---

## What it does

- Write in plain markdown
- Send any paragraph to a local AI (via Ollama — private, no API key)
- See word-level diff: what was added, what was removed
- Get a signal: `STABLE` (same intent) or `NEW` (meaning shifted)
- Accept, reject, or edit — you stay in control
- Every AI action auto-saves a version snapshot
- Compare any two versions side by side

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

No cloud. No account. No API key. Your files stay on your machine.

---

## Version history

Every document keeps a full version timeline. Every AI action creates a snapshot. You can also save manually with a label.

Compare any two versions — your edit vs. the AI's, Tuesday's draft vs. today's. Same diff, same semantic signal.

---

## Who it's for

- Non-native writers working in a second language
- Journalists, essayists, researchers with a point of view worth protecting
- Anyone who has opened `final_final_v7.docx` and wondered what happened

---

## Roadmap

- Pro version: sync and version history across devices
- Team features for collaborative writing
- Workflow integration via [yourcompanyos.io](http://yourcompanyos.io)

---

## Contributing

iterthink is fully open source. Read the code, trust it, improve it.

[GitHub Issues →](https://github.com/iterthink/iterthink/issues)

---

*iterthink — write with AI, stay yourself.*
