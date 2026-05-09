# iterthink

**See every change. Understand the impact. Act on what matters.**

Most AI tools rewrite your documents silently — you get something clean, but you can't tell what changed or whether the meaning held. iterthink shows you exactly what was touched, word by word, and flags when the intent shifted.

---

## What it does

- Import or write documents in plain text (Markdown, PDF, Word)
- See what changed at a glance — word-level highlights, not just line diffs
- Edit manually or with AI assistance, then accept changes in a controlled way
- Automatically evaluate whether a change shifts the meaning
- Trigger follow-up actions through [{yourcompany}os](https://yourcompanyos.io)

---

## Get started

**You'll need:** [Python 3.11+](https://www.python.org/downloads/) and an AI backend (see below).

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -U pip
pip install "git+https://github.com/iterthink/iterthink.git"
iterthink
```

**First launch:**
1. Go to **File → Settings → Paths** — point it to your documents folder (default store is `Documents/.iterthink`)
2. Go to **Settings → Models** — choose your AI backend and configure it

**AI backend options:**

- **Ollama** (local, private) — install [Ollama](https://ollama.com), run eg: `ollama pull llama3.2`, then set the host in Settings → Models
- **OpenAI / ChatGPT** — paste your API key in Settings → Models
- **Anthropic / Claude** — paste your API key in Settings → Models
- **Google / Gemini** — paste your API key in Settings → Models

---

## Who it's for

- Writers who want to track what AI actually changed in their drafts
- Researchers and journalists who need a clear record of document edits
- AEC/spec teams tracking changes in technical documents
- Anyone who's ever dealt with `final_final_v7.docx`


---

## Status

This is early software. Defaults and behavior may change between releases.

- **Platform:** Primarily developed and tested on **Fedora Linux**. Windows and macOS may work but are not regularly QA'd — [open an issue](https://github.com/iterthink/iterthink/issues) if something breaks.
- **AI backend:** Ollama is the most tested backend. OpenAI, Claude, and Gemini work but get less QA.
- **Privacy:** Local-first by default — no account, no cloud sync, files stay on your disk. Using a cloud API key sends your text to that provider.

---

## License

Source available under [BUSL-1.1](https://mariadb.com/bsl11/).

- **Free for individual, non-commercial use** — see [LICENSE](LICENSE)
- **Commercial use** → contact [Abstract AG](https://abstract.build) at [info@abstract.build](mailto:info@abstract.build)
- Converts to **Apache 2.0** on 2030-05-05

---

**Contributing:** see [CONTRIBUTING.md](CONTRIBUTING.md) · **Issues:** [github.com/iterthink/iterthink/issues](https://github.com/iterthink/iterthink/issues)
