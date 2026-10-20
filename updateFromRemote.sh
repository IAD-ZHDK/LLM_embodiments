#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

force=false
if [[ $# -gt 1 || ( $# -eq 1 && "$1" != "--force" ) ]]; then
    printf 'Usage: %s [--force]\n' "$0" >&2
    exit 2
fi
if [[ $# -eq 1 ]]; then
    force=true
fi

if [[ "$force" != true ]]; then
    printf 'This overwrites all local changes to tracked files with origin/main.\n'
    printf 'Untracked and ignored files are left untouched.\n'
    read -r -p 'Continue? (y/N) ' answer
    if [[ ! "$answer" =~ ^[yY] ]]; then
        printf 'Aborted.\n'
        exit 1
    fi
fi

git fetch origin main
git checkout main
git reset --hard origin/main

printf 'Updated to origin/main. Untracked and ignored files were left untouched.\n'