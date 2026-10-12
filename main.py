"""Render entry point; application modules live in telegram_gifts_bot/."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "telegram_gifts_bot"))

from main import main  # noqa: E402


if __name__ == "__main__":
    main()
