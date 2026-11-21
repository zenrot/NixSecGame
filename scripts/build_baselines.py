import json
import sys
import tempfile
from pathlib import Path

from gameguard.installer import safe_extract
from gameguard.static import create_baseline

archives = json.loads(Path(sys.argv[1]).read_text())
output = Path(sys.argv[2])
output.mkdir()
for archive in archives.values():
    with tempfile.TemporaryDirectory() as directory:
        files = Path(directory) / "files"
        files.mkdir()
        safe_extract(Path(archive["archive"]), files)
        baseline = output / f"{archive['sha256']}.json"
        if not baseline.exists():
            create_baseline(files, baseline)
