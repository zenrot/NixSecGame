import argparse
import fcntl
import json
import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from shutil import which

from .catalog import load_catalog, load_sources, manifest_for, read_json, write_json
from .checks import apply_check, backend, unavailable
from .installer import install_files
from .pegasus import update_metadata
from .static import scan_integrity, trusted_baseline


def data_home():
    path = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share")
    return path.resolve() / "gameguard"


def record(directory, name, status):
    write_json(directory / f"{name}.json", status)
    return status


def installed_game(destination, manifest):
    path = destination / "gameguard.json"
    if not path.is_file():
        raise ValueError(f"Сначала выполните gameguard install {manifest['id']}")
    if read_json(path) != manifest:
        raise ValueError("Манифест игры расходится с закреплённым каталогом")
    return destination / "files"


def install(root, catalog, game_id, game, reports):
    games = root / "games"
    games.mkdir(parents=True, exist_ok=True)
    destination = games / game_id
    manifest = manifest_for(game_id, game)

    with (root / "install.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if destination.exists():
            files = installed_game(destination, manifest)
            verification = scan_integrity(files, trusted_baseline(game))
            if verification["status"] != "ok":
                raise ValueError("Установка отличается от защищённого эталона. Проверьте gameguard verify")
            metadata = update_metadata(games, catalog)
            return {"status": "ok", "reused": True, "pegasus": str(metadata)}

        with tempfile.TemporaryDirectory(prefix=".install-", dir=games) as temporary:
            stage = Path(temporary)
            stage.chmod(0o700)
            tracer = backend("dynamic")
            if tracer is None:
                install_files(game, load_sources(), stage)
                trace = unavailable("dynamic")
            else:
                reports.mkdir(parents=True, exist_ok=True)
                command = [sys.executable, "-m", "gameguard", "_extract", game_id, str(stage)]
                trace = tracer.trace_install(command, stage / "files", reports)
                if trace["exit_code"] != 0:
                    record(reports, "install-trace", trace)
                    raise RuntimeError("Установщик завершился с ошибкой")

            baseline = scan_integrity(stage / "files", trusted_baseline(game))
            if baseline["status"] in {"warning", "error"}:
                raise ValueError("Не удалось сохранить эталон целостности")
            write_json(stage / "gameguard.json", manifest)
            stage.rename(destination)

        metadata = update_metadata(games, catalog)
        return record(reports, "install", {
            "status": "ok",
            "game": manifest,
            "source": game["url"],
            "files": sorted(
                path.relative_to(destination / "files").as_posix()
                for path in (destination / "files").rglob("*") if path.is_file()
            ),
            "pegasus": str(metadata),
            "baseline": baseline,
            "trace": trace,
        })


def launch(game, files, destination, reports):
    verification = scan_integrity(files, trusted_baseline(game))
    record(reports, "verify", verification)
    if verification["status"] in {"warning", "error"}:
        raise ValueError("GameGuard обнаружил отклонения. Проверьте отчёт verify")

    if game["runner"] != "scummvm":
        raise ValueError(f"Неподдерживаемый runner {game['runner']!r}")
    data_dir = files / game.get("data_dir", ".")
    if any(not (data_dir / name).is_file() for name in game["required_files"]):
        raise ValueError("В установке отсутствуют игровые файлы")

    saves = destination / "saves"
    saves.mkdir(exist_ok=True)
    command = ["scummvm", "--no-fullscreen", f"--path={data_dir}", f"--savepath={saves}", game["target"]]
    tracer = backend("dynamic")
    trace = unavailable("dynamic")
    if tracer is None:
        exit_code = subprocess.run(command, check=False).returncode
    else:
        trace = tracer.trace_network(command, reports)
        exit_code = trace["exit_code"]
    result = record(reports, "run", {
        "status": "ok" if exit_code == 0 else "error",
        "exit_code": exit_code,
        "runner": game["runner"],
        "verification": verification,
        "trace": trace,
    })
    return result, int(exit_code != 0)


def handle(args, catalog, root):
    if args.action == "catalog":
        return catalog, 0

    game_id = args.game
    if args.action == "run" and args.manifest is not None:
        manifest = args.manifest.resolve()
        game_id = manifest.parent.name
        if manifest != root / "games" / game_id / "gameguard.json":
            raise ValueError("Укажите манифест из каталога установок GameGuard")

    if game_id not in catalog:
        raise ValueError(f"Игра {game_id!r} отсутствует в каталоге")
    game = catalog[game_id]
    destination = root / "games" / game_id
    reports = root / "reports" / game_id
    manifest = manifest_for(game_id, game)

    if args.action == "install":
        return install(root, catalog, game_id, game, reports), 0

    files = installed_game(destination, manifest)
    if args.action == "run":
        return launch(game, files, destination, reports)
    if args.action == "verify":
        result = scan_integrity(files, trusted_baseline(game))
        return record(reports, "verify", result), int(result["status"] != "ok")
    if args.action == "sbom":
        runner = which("scummvm")
        if runner is None:
            raise ValueError("Команда scummvm отсутствует в PATH")
        result = apply_check("static", "make_sbom", files, Path(runner), reports / "sbom.cdx.json")
        return record(reports, "sbom", result), int(result["status"] != "ok")

    results = {"game": manifest}
    for name in ("install", "verify", "sbom", "run"):
        path = reports / f"{name}.json"
        results[name] = read_json(path) if path.exists() else {"status": "not_run"}
    return results, 0


def main():
    # Internal entry point for the optional installation tracer.
    if len(sys.argv) == 4 and sys.argv[1] == "_extract":
        try:
            install_files(load_catalog()[sys.argv[2]], load_sources(), Path(sys.argv[3]))
            return 0
        except (KeyError, ValueError, OSError, zipfile.BadZipFile) as error:
            print(error, file=sys.stderr)
            return 1

    parser = argparse.ArgumentParser(prog="gameguard")
    commands = parser.add_subparsers(dest="action", required=True)
    commands.add_parser("catalog", help="показать доступные игры")
    for action in ("install", "verify", "sbom", "report"):
        commands.add_parser(action).add_argument("game")
    run = commands.add_parser("run", help="запустить установленную игру")
    selection = run.add_mutually_exclusive_group(required=True)
    selection.add_argument("game", nargs="?")
    selection.add_argument("--manifest", type=Path)

    args = parser.parse_args()
    os.umask(0o077)
    try:
        result, code = handle(args, load_catalog(), data_home())
    except (ValueError, OSError, RuntimeError, zipfile.BadZipFile) as error:
        print(json.dumps({"status": "error", "message": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
