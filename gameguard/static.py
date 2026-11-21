import hashlib
import json
import os
import re
import stat
import subprocess
from pathlib import Path

from .catalog import write_json


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def snapshot(game_dir):
    if not stat.S_ISDIR(game_dir.lstat().st_mode):
        raise ValueError("Каталог игровых ресурсов должен быть обычным каталогом")

    def raise_walk_error(error):
        raise error

    files = {}
    for root, dirs, names in os.walk(game_dir, onerror=raise_walk_error):
        parent = Path(root)
        for name in dirs[:]:
            path = parent / name
            if path.is_symlink():
                dirs.remove(name)
                names.append(name)
        for name in names:
            path = parent / name
            key = path.relative_to(game_dir).as_posix()
            files[key] = sha256(path) if stat.S_ISREG(path.lstat().st_mode) else None
    return files


def create_baseline(game_dir, baseline_path):
    files = snapshot(game_dir)
    if None in files.values():
        raise ValueError("В игровых ресурсах обнаружен специальный файл или ссылка")
    with baseline_path.open("x", encoding="utf-8") as stream:
        json.dump({"algorithm": "SHA-256", "files": files}, stream, indent=2, sort_keys=True)
        stream.write("\n")
    return {"status": "ok", "algorithm": "SHA-256", "files_checked": len(files)}


def scan_integrity(game_dir, baseline_path):
    try:
        if baseline_path.is_symlink():
            raise ValueError("Ссылка вместо эталона целостности")
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
        if not isinstance(baseline, dict) or baseline.get("algorithm") != "SHA-256":
            raise ValueError("Неверный формат эталона целостности")
        expected = baseline.get("files")
        if not isinstance(expected, dict) or not expected or any(
            not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)
            for digest in expected.values()
        ):
            raise ValueError("Некорректные SHA-256 в эталоне целостности")
        try:
            actual = snapshot(game_dir)
        except FileNotFoundError:
            if game_dir.exists():
                raise
            actual = {}
    except FileNotFoundError:
        return {"status": "error", "reason": "Нет эталона целостности. Переместите старую установку и установите игру заново."}
    except (ValueError, OSError) as error:
        return {"status": "error", "reason": str(error)}

    added = sorted(actual.keys() - expected.keys())
    removed = sorted(expected.keys() - actual.keys())
    changed = sorted(name for name in expected.keys() & actual.keys() if expected[name] != actual[name])
    return {
        "status": "warning" if added or removed or changed else "ok",
        "algorithm": "SHA-256",
        "added": added,
        "removed": removed,
        "changed": changed,
    }


def elf_engine(runner_path):
    path = runner_path.resolve(strict=True)
    with path.open("rb") as stream:
        if stream.read(4) == b"\x7fELF":
            return path
        wrapper = stream.read(65536).decode("utf-8", errors="replace")
    candidates = [path.with_name(f".{path.name}-wrapped")]
    candidates.extend(Path(match) for match in re.findall(r'/nix/store/[^\s\'";]+/bin/\.?scummvm(?:-wrapped)?\b', wrapper))
    for target in candidates:
        if target.is_file():
            with target.open("rb") as stream:
                if stream.read(4) == b"\x7fELF":
                    return target.resolve()
    raise ValueError(f"Не удалось найти ELF ScummVM за командой {runner_path}")


def elf_dependencies(engine):
    result = subprocess.run(["lddtree", "-l", str(engine)], capture_output=True, text=True)
    if result.returncode or result.stderr.strip() or not result.stdout.strip():
        raise ValueError(f"Ошибка lddtree: {result.stderr.strip() or result.returncode}")
    libraries = set()
    for line in result.stdout.splitlines():
        path = Path(line)
        if not path.is_absolute() or not path.is_file():
            raise ValueError(f"Неразрешённая ELF-зависимость: {line}")
        target = path.resolve(strict=True)
        if target != engine:
            libraries.add(target)
    return sorted(libraries)


def component(kind, name, ref, digest):
    return {
        "type": kind,
        "name": name,
        "bom-ref": ref,
        "hashes": [{"alg": "SHA-256", "content": digest}],
    }


def make_sbom(game_dir, runner_path, output_path):
    from cyclonedx.schema import SchemaVersion
    from cyclonedx.validation.json import JsonStrictValidator

    engine = elf_engine(runner_path)
    dependencies = elf_dependencies(engine)
    resources = snapshot(game_dir)
    if None in resources.values():
        raise ValueError("В игровых ресурсах обнаружен специальный файл или ссылка")

    engine_component = component("application", "ScummVM", f"elf:{engine}", sha256(engine))
    libraries = [component("library", path.name, f"elf:{path}", sha256(path)) for path in dependencies]
    files = [component("file", name, f"resource:{name}", digest) for name, digest in sorted(resources.items())]
    bom = {
        "$schema": "http://cyclonedx.org/schema/bom-1.6.schema.json",
        "bomFormat": "CycloneDX",
        "specVersion": "1.6",
        "version": 1,
        "metadata": {"component": engine_component},
        "components": libraries + files,
    }
    document = json.dumps(bom, ensure_ascii=False)
    error = JsonStrictValidator(SchemaVersion.V1_6).validate_str(document)
    if error is not None:
        raise ValueError(f"CycloneDX 1.6: {error.message}")

    write_json(output_path, bom)
    return {
        "status": "ok",
        "path": str(output_path),
        "schema": "CycloneDX 1.6",
        "schema_valid": True,
        "elf_dependencies": len(libraries),
        "game_resources": len(files),
    }
