"""Allow: python -m iterthink and the ``iterthink`` console script."""

import flet as ft

from iterthink.app_entry import main


def cli() -> None:
    ft.run(main)


if __name__ == "__main__":
    cli()
