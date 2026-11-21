# This file is invoked with Python -I from the root-owned Nix package.
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gameguard.network import helper_main

raise SystemExit(helper_main())
