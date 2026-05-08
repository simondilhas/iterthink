#!/usr/bin/env python3
"""Download embedding weights into iterthink/embedded_models/ for CI / Flet package-data."""

from __future__ import annotations

from iterthink.ai.local_embedding import ensure_bundle_model_downloaded


def main() -> None:
    ensure_bundle_model_downloaded()
    print("Embedding model prefetch done.")


if __name__ == "__main__":
    main()
