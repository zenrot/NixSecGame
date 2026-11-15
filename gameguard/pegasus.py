import os
from pathlib import Path


def config_home():
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config").resolve()


def update_metadata(games_dir, catalog):
    target = config_home() / "pegasus-frontend" / "metafiles" / "gameguard.metadata.pegasus.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    lines = ["collection: GameGuard Retro"]
    for game_id, game in catalog.items():
        manifest = games_dir / game_id / "gameguard.json"
        if manifest.is_file():
            if "\n" in str(manifest) or "\r" in str(manifest):
                raise ValueError("Pegasus требует путь без перевода строки")
            lines.extend((
                "",
                f"game: {game['title']}",
                f"file: {manifest}",
                f"launch: gameguard run {game_id}",
            ))

    temporary = target.with_suffix(".tmp")
    temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
    temporary.replace(target)
    return target
