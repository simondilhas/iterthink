# Iterthink help

Iterthink stores your work as **plain Markdown** (`.md`) files under your documents folder. This guide explains the Markdown you can use in the editor.

---

## How to use Markdown

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
