import hashlib
import shutil
import stat
import zipfile
from pathlib import PurePosixPath
from urllib.parse import unquote, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, build_opener

LIMIT = 64 * 1024 * 1024


def validate_url(url, source_id, sources):
    source = sources.get(source_id)
    if source is None:
        raise ValueError(f"Неизвестный источник {source_id!r}")

    parsed = urlsplit(url)
    path = unquote(parsed.path)
    if (
        parsed.scheme != "https"
        or parsed.hostname != source["host"]
        or parsed.port not in (None, 443)
        or parsed.username is not None
        or parsed.password is not None
        or not path.startswith(source["path_prefix"])
        or any(part in {".", ".."} for part in path.split("/"))
        or "\\" in path
        or "%" in path
    ):
        raise ValueError(f"URL вне разрешённого источника {source_id!r}: {url}")


class CheckedRedirects(HTTPRedirectHandler):
    def __init__(self, source_id, sources):
        self.source_id = source_id
        self.sources = sources

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_url(newurl, self.source_id, self.sources)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download_archive(game, sources, archive):
    url, source_id = game["url"], game["source_id"]
    validate_url(url, source_id, sources)
    opener = build_opener(ProxyHandler({}), CheckedRedirects(source_id, sources))
    digest = hashlib.sha256()
    size = 0
    with opener.open(url, timeout=30) as response, archive.open("xb") as target:
        validate_url(response.geturl(), source_id, sources)
        for block in iter(lambda: response.read(1024 * 1024), b""):
            size += len(block)
            if size > LIMIT:
                raise ValueError("Архив превышает 64 МиБ")
            digest.update(block)
            target.write(block)

    if digest.hexdigest() != game["sha256"]:
        raise ValueError("SHA-256 архива отличается от значения в каталоге")


def safe_extract(archive, destination):
    seen = set()
    with zipfile.ZipFile(archive) as package:
        members = package.infolist()
        if len(members) > 4096 or sum(entry.file_size for entry in members) > LIMIT:
            raise ValueError("Превышен предел размера или числа файлов в ZIP")

        for entry in members:
            name = entry.orig_filename.removesuffix("/")
            parts = name.split("/")
            kind = stat.S_IFMT(entry.external_attr >> 16)
            allowed_kind = stat.S_IFDIR if entry.is_dir() else stat.S_IFREG
            if (
                any(part in {"", ".", ".."} for part in parts)
                or "\\" in name
                or ":" in name
                or "\x00" in name
                or kind not in (0, allowed_kind)
                or name in seen
            ):
                raise ValueError(f"Недопустимая запись ZIP: {name!r}")

            seen.add(name)
            target = destination / PurePosixPath(name)
            if entry.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with package.open(entry) as source, target.open("xb") as output:
                    shutil.copyfileobj(source, output, length=1024 * 1024)


def install_files(game, sources, stage):
    archive = stage / "game.zip"
    files = stage / "files"
    files.mkdir()
    download_archive(game, sources, archive)
    safe_extract(archive, files)
    for name in game["required_files"]:
        if not (files / name).is_file():
            raise ValueError(f"В архиве отсутствует файл {name}")
    archive.unlink()
