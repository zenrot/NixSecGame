# Интерфейсы GameGuard

CLI загружает модули `gameguard.static` и `gameguard.dynamic`, если они есть в пакете. Аргументы путей имеют тип `pathlib.Path`. Функции возвращают словари, которые CLI записывает в JSON. Результат проверки содержит поле `status` со значением `ok`, `warning`, `error` или `not_applicable`. Поля результата могут содержать список обнаруженных отклонений и пути к файлам.

## Статический анализ

- `create_baseline(game_dir, baseline_path)` создаёт эталон из архива при сборке NixOS. Сборщик проверяет SHA-256 архива и помещает результат в Nix Store. Пути в эталоне отсчитываются от `game_dir`.
- `trusted_baseline(game)` возвращает системный путь `/etc/gameguard/baselines/<archive-sha256>.json`. CLI проверяет по нему ресурсы при установке, повторной установке, проверке и запуске. Пользовательские эталоны из прежних установок игнорируются.
- `scan_integrity(game_dir, baseline_path)` сверяет файлы с эталоном. GameGuard останавливает запуск, если модуль возвращает `warning` или `error`. Прежние установки сверяются с системным эталоном из проверенного архива.
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
## Отчёты динамического анализа

Сырые трассы и сводки лежат в `report_dir`: `install.strace` и `install-summary.json` для установки, `network.strace` и `network-summary.json` для сети. Рядом сохраняются `install.stderr` и `network.stderr` с выводом самого `strace` и процесса. Для работы нужен `strace` в `PATH`. Если его нет, обе функции возвращают `status: error` и причину в `reason`.

### Сетевая политика

Файл `repository/network-policy.json` задаёт разрешённые исходящие адреса:

```json
{
  "schema_version": 1,
  "allowed": ["127.0.0.0/8", "::1/128"]
}
```

Элементы списка `allowed` это IPv4/IPv6-адреса или сети в формате CIDR. Адреса вида `::ffff:a.b.c.d` сравниваются как IPv4. Если файла нет, разрешён только loopback. Функция `trace_network` принимает необязательный аргумент `policy_path` для дополнительного анализа. Ядро применяет политику из активной системной сборки. Пользовательский `policy_path` не меняет фильтр ядра.

### Результат `trace_install`

- `exit_code`, `install_dir`, `trace_file`, `summary_file`. При ненулевом коде выхода добавляется `stderr_tail`.
- `status`: `ok`, `warning` (есть запись вне каталога установки) или `error`.
- `file_operations`: число файловых вызовов по именам.
- `files_written`, `files_deleted`, `directories_created`, `renamed`: изменения в файловой системе. Если записей больше 200, лишние не перечисляются, их число в `files_written_not_listed`.
- `writes_outside_staging`: успешные записи вне временного каталога установки (родителя `install_dir`). Пути `/dev`, `/proc`, `/sys` не учитываются.
- `child_processes`: `spawned` (число `clone`/`fork`), `executed` (путь и аргументы каждого `execve`, первый элемент это сам установщик), `failed_exits` (процессы с ненулевым кодом или сигналом).
- `errors`: `total`, `by_errno`, `by_syscall` и `samples` (первые 20 ошибок, кроме `ENOENT`, который даёт много шума при поиске файлов).
- `total_syscalls`: общее число разобранных вызовов.

### Результат `trace_network`

- `exit_code`, `trace_file`, `summary_file`. При ненулевом коде выхода добавляется `stderr_tail`.
- `status`: `ok`, `warning` при попытке соединения вне политики или `error` при ошибке запуска/завершения процесса.
- `enforcement`: `systemd-cgroup-bpf`, `enforced_networks`: разрешённые сети из активной NixOS-конфигурации.
- `connections`: исходящие вызовы `connect`, `sendto`, `sendmsg`, `sendmmsg` к IP-адресам, сгруппированные по одинаковым параметрам. Поля: `syscall`, `ip`, `port`, `result`, `errno`, `allowed`, `count`.
- `result`: `success`, `failed` или `in_progress`. Итог неблокирующего `connect` берётся из последующего `getsockopt(SO_ERROR)`. Если его нет в трассе, остаётся `in_progress`.
- `violations`: подмножество `connections` с `allowed: false`.
- `listening`: адреса и порты из вызовов `bind`.
- `unix_socket_calls`: число вызовов к unix-сокетам. Они не сверяются с политикой.
- `syscalls`: число сетевых вызовов по именам, `allowed_networks`: действующая политика.

GameGuard запускает `strace` и игру через `gameguard-network` в системной cgroup с IP-фильтром. Фильтр блокирует запрещённые IPv4/IPv6-пакеты для TCP/UDP, а трассировщик записывает попытки обращений. `exit_code` содержит код завершения процесса.
