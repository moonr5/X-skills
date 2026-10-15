import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from uaskills.cli import main_search

if __name__ == "__main__":
    raise SystemExit(main_search())
