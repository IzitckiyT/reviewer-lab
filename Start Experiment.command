#!/bin/zsh
cd -- "$(dirname -- "$0")" || exit 1
exec /bin/zsh "./Start Reviewer Lab.command" --live "$@"
