#!/usr/bin/env bash
# Проверка контроля целостности: дописывает метку в конец файла игры и убирает её.
#   scripts/tamper.sh add   [файл]   испортить файл
#   scripts/tamper.sh clean [файл]   вернуть файл в исходное состояние
# По умолчанию используется sky.dnr из Beneath a Steel Sky.
set -eu

file=${2:-"${XDG_DATA_HOME:-$HOME/.local/share}/gameguard/games/beneath-a-steel-sky/files/sky.dnr"}
mark=GAMEGUARD-TAMPER

case ${1:-} in
  add)
    printf %s "$mark" >> "$file"
    echo "Добавлено ${#mark} байт в $file"
    ;;
  clean)
    while [ "$(tail -c ${#mark} "$file")" = "$mark" ]; do
      truncate -s -${#mark} "$file"
    done
    echo "Файл $file очищен"
    ;;
  *)
    echo "Использование: $0 add|clean [файл]" >&2
    exit 2
    ;;
esac
