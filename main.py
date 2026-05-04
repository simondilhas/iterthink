"""
Iterthink local Markdown writer (Flet + Ollama).

Run: python main.py
Or:  python -m iterthink

Env: OLLAMA_MODEL (default until changed in UI), OLLAMA_HOST (optional).
"""

import flet as ft

from iterthink.app_entry import main

if __name__ == "__main__":
    ft.run(main)
