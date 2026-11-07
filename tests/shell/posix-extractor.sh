#!/usr/bin/env bash
set -euo pipefail
EXTRACTOR_OFFSET=000000000000
EXTRACTOR_SIZE=000000000000
ARCHIVE_OFFSET=000000000000
ARCHIVE_SIZE=000000000000
SELF="$0"
TMP="$(mktemp -d)"
cleanup() { rm -rf "$TMP"; }
trap cleanup EXIT
copy_range() {
    local offset="$1"
    local size="$2"
    local destination="$3"
    set +e
    tail -c "+$((10#$offset + 1))" "$SELF" | head -c "$size" > "$destination"
    # PIPESTATUS is expanded before local runs, so this keeps the pipeline status.
    # shellcheck disable=SC2155
    local saved=("${PIPESTATUS[@]}")
    set -e
    local tail_status="${saved[0]}"
    local head_status="${saved[1]}"
    if [ "$head_status" -ne 0 ]; then
        return "$head_status"
    fi
    if [ "$tail_status" -ne 0 ] && [ "$tail_status" -ne 141 ]; then
        return "$tail_status"
    fi
    return 0
}
copy_range "$EXTRACTOR_OFFSET" "$EXTRACTOR_SIZE" "$TMP/7zz"
copy_range "$ARCHIVE_OFFSET" "$ARCHIVE_SIZE" "$TMP/setup.7z"
chmod +x "$TMP/7zz"
"$TMP/7zz" x "-o${TMP}/root" -y "$TMP/setup.7z"
PY="${TMP}/root/python/bin/python3"
exec "$PY" -m cursor_portable "$@"
