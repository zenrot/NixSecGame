import json
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[1] / "repository"


def read_json(path):
    with path.open(encoding="utf-8") as source:
        return json.load(source)


def load_catalog():
    return read_json(REPOSITORY / "catalog.json")


def load_sources():
    return read_json(REPOSITORY / "source-whitelist.json")


def manifest_for(game_id, game):
    return {
        "schema_version": 1,
        "id": game_id,
        "title": game["title"],
        "version": game["version"],
        "source_id": game["source_id"],
        "archive_sha256": game["sha256"],
    }
