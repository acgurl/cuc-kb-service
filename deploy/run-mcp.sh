#!/usr/bin/env bash
set -euo pipefail

export KB_API_URL="${KB_API_URL:-http://127.0.0.1:8000}"
export KB_API_ENV_FILE="${KB_API_ENV_FILE:-/home/ubuntu/cuc-kb/.env}"

exec /usr/bin/python3 /home/ubuntu/cuc-kb/kb/mcp_server.py "$@"
