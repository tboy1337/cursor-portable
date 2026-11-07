#!/bin/sh
set -eu
ready="${CURSOR_SESSION_READY:?}"
stop="${CURSOR_SESSION_STOP:?}"
printf '%s\n' ready > "$ready"
while [ ! -f "$stop" ]; do
    sleep 0.2
done
