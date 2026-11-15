# Интерфейсы GameGuard

CLI загружает модули `gameguard.static` и `gameguard.dynamic`, если они есть в пакете. Аргументы путей имеют тип `pathlib.Path`. Функции возвращают словари, которые CLI записывает в JSON. Результат проверки содержит поле `status` со значением `ok`, `warning`, `error` или `not_applicable`. Поля результата могут содержать список обнаруженных отклонений и пути к файлам.

## Статический анализ

- `create_baseline(game_dir, baseline_path)` создаёт эталон при установке. CLI переносит проверяемый каталог `files/` и соседний `integrity.json` из временной папки в каталог установки. Сохраняйте в эталоне пути относительно `game_dir`.
- `scan_integrity(game_dir, baseline_path)` сверяет файлы с эталоном. GameGuard останавливает запуск, если модуль возвращает `warning` или `error`. Для игр, установленных до подключения модуля, обработайте отсутствие эталона.
- `make_sbom(game_dir, runner_path, output_path)` сохраняет CycloneDX JSON. Для Beneath a Steel Sky `runner_path` указывает на команду ScummVM из Nixpkgs. При необходимости разберите её обёртку и анализируйте исполняемый ELF движка. Игровые файлы записывайте как ресурсы.

## Динамический анализ

- `trace_install(argv, install_dir, report_dir)` запускает установщик с трассировкой. `argv` уже содержит интерпретатор, модуль и аргументы для загрузки и распаковки в каталог временной установки. `install_dir` указывает на папку с ресурсами, которую создаёт установщик.
- `trace_network(argv, report_dir)` запускает игровой процесс с наблюдением за сетью. Модуль трассировки задаёт расположение и формат своей сетевой политики.
- Обе функции возвращают `exit_code` завершившегося процесса вместе с кратким результатом трассировки. Сырые журналы храните в `report_dir`. Обёртка передаёт `argv` как массив аргументов.

### Проверка на защите

Выполните в NixOS ARM64 из каталога проекта. Для сборки из Git учитываются файлы, добавленные в индекс. Префикс `path:` позволяет собрать и локальную рабочую копию.

```sh
nix --extra-experimental-features "nix-command flakes" build "path:$PWD"
gameguard="$PWD/result/bin/gameguard"
demo=$(mktemp -d)
id=beneath-a-steel-sky

export XDG_DATA_HOME="$demo/clean"
"$gameguard" install "$id"
"$gameguard" verify "$id"
"$gameguard" sbom "$id"

cp -a "$demo/clean" "$demo/tampered"
export XDG_DATA_HOME="$demo/tampered"
printf '\nGameGuard demo tamper\n' >> "$XDG_DATA_HOME/gameguard/games/$id/files/sky.dnr"
"$gameguard" verify "$id"
echo "verify exit: $?"
"$gameguard" run "$id"
echo "run exit: $?"
```

На чистой копии `verify` возвращает `ok`. После подмены `sky.dnr` появляется в списке `changed`, а обе последние команды возвращают код 1. GameGuard выполняет проверку до запуска ScummVM. JSON с результатами остаётся в `reports/beneath-a-steel-sky/` внутри соответствующего каталога данных.

`sbom.cdx.json` содержит ScummVM, транзитивные зависимости, которые `lddtree` разрешил из ELF, и игровые ресурсы как компоненты `file`. Библиотеки, которые движок подгружает во время выполнения, требуют отдельного наблюдения. Перед записью GameGuard проверяет JSON по локальной схеме CycloneDX 1.6.

Для старой установки без `integrity.json` переместите каталог игры в резервную папку и повторите `gameguard install`. Эталон вычисляется из проверенного архива при установке.
