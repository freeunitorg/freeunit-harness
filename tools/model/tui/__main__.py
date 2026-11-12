"""Start the model TUI: python3 -m tools.model.tui (needs textual)."""

from tools.model.tui.app import ModelTui


def main() -> None:
    ModelTui().run()


if __name__ == "__main__":
    main()
