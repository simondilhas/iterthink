# Iterthink help

## Help

- [About Iterthink](#about-iterthink)
- [Setting up Iterthink](#setting-up-iterthink)
  - [File storage](#file-storage)
  - [AI setup](#ai-setup)
- [Markdown](#markdown)
  - [Headings](#headings)
  - [Emphasis and inline code](#emphasis-and-inline-code)
  - [Lists](#lists)
  - [Links and images](#links-and-images)
  - [Code blocks](#code-blocks)
  - [Block quotes](#block-quotes)
  - [Horizontal rule](#horizontal-rule)
  - [Tables (GitHub-style)](#tables-github-style)
  - [Escaping](#escaping)
- [Tips in Iterthink](#tips-in-iterthink)

---

## About Iterthink

I built Iterthink out of frustration with the current state of AI writing tools. I actually built a full SaaS version first — cloud-based, subscription-ready, the whole thing. Then I stepped back and realised the cloud model was part of the problem. When your thinking lives on someone else's server, the privacy question is never fully resolved. So I scrapped it and rebuilt locally.

Three frustrations drove the design:

**First, destructive editing.** Every tool I tried treated my original text as disposable. Hit "improve" and your draft is gone — no diff, no way to see what changed, no way to know if the AI quietly replaced your nuanced point with polite slop.

**Second, git isn't for prose.** Git diff works line by line. But paragraphs move, merge, and split. A sentence shifting three paragraphs down shows as deleted and reinserted — not moved. You lose the thread of your own logic. Prose needs semantic, paragraph-level tracking.

**Third, the judgment gap.** I kept watching people upload documents to ChatGPT or Claude, ask for the "impact on the project," and nod at the answer. When I asked them why — they didn't know. They'd stopped thinking. The AI was replacing judgment, not supporting it.

---

## Setting up Iterthink

### File storage

Iterthink stores your notes as **plain Markdown** (`.md`) files. In **File → Settings** (Paths tab), you set:

- **Documents root** — the folder where your `.md` files live (default in config: `documents_root`, often `~/Documents`).
- **Store directory** — app metadata next to your notes (default: `store_dir`, often `~/Documents/.iterthink`). Keep this on the same machine as the documents root; it is not meant to be hand-edited like your notes.

You can also edit `defaults/config.yaml` (or the merged config the app loads) for bootstrap values; the Settings UI is the usual place to change paths after install.

**Tip:** Point the documents root at a folder you sync (Nextcloud, Dropbox, Syncthing, iCloud Drive folder, etc.). Your Markdown stays ordinary files — you can open the same tree in Obsidian, iA Writer, or any editor on another device while Iterthink remains your home on the desktop you prefer.

### AI setup

Iterthink talks to **Ollama** on your machine by default (local API, typically `http://localhost:11434`). Install Ollama, pull a model (for example `ollama pull llama3`), then in **File → Settings** pick the chat model and optional host override.

Environment variables override YAML when set: **`OLLAMA_HOST`** and **`OLLAMA_MODEL`** take precedence over `ollama_host` and `default_ollama_model` in config. The Models tab in Settings lists what Ollama reports as installed.

If Ollama is unreachable, the app will show connection errors when you use AI features; fix the daemon or host first, then refresh models in Settings.

---

## Markdown

Markdown is a lightweight way to structure text. A blank line separates **paragraphs** (Iterthink’s diff and margin features use paragraph breaks).

### Headings

Use `#` through `######` for six levels:

```markdown
# Title
## Section
### Subsection
```

### Emphasis and inline code

- `**bold**` or `__bold__`
- `*italic*` or `_italic_`
- `` `inline code` `` (backticks around text)

### Lists

**Unordered** — start lines with `-`, `*`, or `+`:

```markdown
- First item
- Second item
  - Nested item
```

**Ordered** — numbers followed by a period:

```markdown
1. First step
2. Second step
```

### Links and images

```markdown
[Link text](https://example.com)
![Alt text for images](path-or-url/to/image.png)
```

### Code blocks

Fence with three backticks (optionally add a language for highlighting):

````markdown
```python
def hello():
    print("Hello")
```
````

### Block quotes

```markdown
> One or more lines quoted together.
```

### Horizontal rule

```markdown
---
```

### Tables (GitHub-style)

```markdown
| Column A | Column B |
|----------|----------|
| Cell     | Cell     |
```

### Escaping

If you need a literal `*`, `` ` ``, or `#` at the start of a line, prefix with `\` or indent with spaces as needed.

---

## Tips in Iterthink

- Use **File → Save** (or your usual shortcut) to persist the current file.
- **Paragraph breaks** (`Enter` twice) define chunks for meaning hints and diffs.
- **File → Settings** configures models, paths, and prompts.

For app-specific features, see **Help → About**.
