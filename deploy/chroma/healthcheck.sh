#!/usr/bin/env bash
# The existing Rust Chroma image has bash, but no curl, wget or Python.
set -euo pipefail
exec 3<>/dev/tcp/127.0.0.1/8000
printf 'GET /api/v2/heartbeat HTTP/1.0\r\nHost: localhost\r\n\r\n' >&3
read -r response <&3
[[ "$response" == *' 200 '* ]]
