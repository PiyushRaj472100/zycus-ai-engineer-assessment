"""pytest configuration — adds src/ to sys.path."""
import sys
from pathlib import Path

# Allow `from src.xxx import ...` without installing the package.
sys.path.insert(0, str(Path(__file__).parent))
