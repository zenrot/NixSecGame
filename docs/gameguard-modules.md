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

Элементы списка `allowed` это IPv4/IPv6-адреса или сети в формате CIDR. Адреса вида `::ffff:a.b.c.d` сравниваются как IPv4. Если файла нет, разрешён только loopback. Функция `trace_network` принимает необязательный аргумент `policy_path` для другой политики.

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
- `status`: `ok` или `warning`, если есть соединение вне политики.
- `connections`: исходящие вызовы `connect`, `sendto`, `sendmsg`, `sendmmsg` к IP-адресам, сгруппированные по одинаковым параметрам. Поля: `syscall`, `ip`, `port`, `result`, `errno`, `allowed`, `count`.
- `result`: `success`, `failed` или `in_progress`. Итог неблокирующего `connect` берётся из последующего `getsockopt(SO_ERROR)`. Если его нет в трассе, остаётся `in_progress`.
- `violations`: подмножество `connections` с `allowed: false`.
- `listening`: адреса и порты из вызовов `bind`.
- `unix_socket_calls`: число вызовов к unix-сокетам. Они не сверяются с политикой.
- `syscalls`: число сетевых вызовов по именам, `allowed_networks`: действующая политика.

`status: warning` не останавливает запуск игры: `exit_code` в результате остаётся кодом завершения процесса.
