#!/bin/zsh
cd -- "$(dirname -- "$0")" || exit 1
for prefix in '' /opt/homebrew/bin/ /usr/local/bin/; do
for name in python3.12 python3.13 python3.14 python3.11 python3.10 python3; do
  interpreter="${prefix}${name}"
  if command -v "$interpreter" >/dev/null 2>&1 && "$interpreter" -c 'import sys; sys.exit(sys.version_info < (3, 10))'; then
    "$interpreter" run.py start "$@"
    result=$?
    if (( result != 0 )); then
      printf '\nReviewer Lab не запустился. Причина указана выше.\n'
      read '?Нажмите Enter, чтобы закрыть окно.'
    fi
    exit "$result"
  fi
done
done
printf 'Нужен Python 3.10 или новее. После установки запустите этот файл ещё раз.\n'
read '?Нажмите Enter, чтобы закрыть окно.'
