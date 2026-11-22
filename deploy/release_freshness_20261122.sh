#!/usr/bin/env bash
# One-time Phoenix release. Takes the absolute private staging directory.
set -euo pipefail
stage=${1:?usage: release_freshness_20261122.sh STAGE_DIRECTORY}
cd "$HOME/cuc-kb"
backup="$stage/backup"
files=(app.py search_core.py mcp_server.py clause_updates.py clause_updates.json)
exec 9>"$HOME/.cuc-kb-release.lock"
flock -n 9
sha256sum --check <<'SUMS'
99abcf1fa6e232a1b29b21bf859b17a20aebcd6798c9ae1ac5c5c0aeb8d63560  kb/app.py
5aac41e3effa2aeab84f2e9a35b5defd272932197e4d413b14df490af1097fb3  kb/search_core.py
5c49c700e3d756227541d16f0918b24eb0fd7811fa413d4d557d2a2b625d3e66  kb/mcp_server.py
SUMS
bash -n "$0"
for file in "${files[@]}"; do test -s "$stage/$file"; done
mkdir -m 700 "$backup"
cp -p .env "$backup/.env"
for file in "${files[@]}"; do
    if [[ -e kb/$file ]]; then cp -p "kb/$file" "$backup/$file"; fi
done
printf 'BACKUP %s\n' "$backup"

health() {
    for _ in {1..80}; do
        if curl -fsS --max-time 1 "$1" 2>/dev/null; then printf '\n'; return; fi
        sleep 1
    done
    return 1
}
rollback() {
    local code=$?
    trap - ERR
    set +e
    printf 'RELEASE FAILED (%s); restoring the backup\n' "$code" >&2
    cp -p "$backup/.env" .env
    for file in "${files[@]}"; do
        if [[ -e $backup/$file ]]; then cp -p "$backup/$file" "kb/$file"; else rm -f "kb/$file"; fi
    done
    sudo -n systemctl restart cuc-kb
    sudo -n systemctl restart cuc-kb-mcp
    health http://127.0.0.1:8000/healthz
    health http://127.0.0.1:8765/healthz
    exit "$code"
}
trap rollback ERR
for file in "${files[@]}"; do
    install -m 644 "$stage/$file" "kb/$file.release"
    mv -f "kb/$file.release" "kb/$file"
done
python3 - <<'PY'
from pathlib import Path
p = Path('.env')
lines = [line for line in p.read_text().splitlines() if not line.startswith('KB_MCP_PREFER_CURRENT=')]
p.write_text('\n'.join([*lines, 'KB_MCP_PREFER_CURRENT=1', '']))
PY
sudo -n systemctl restart cuc-kb
health http://127.0.0.1:8000/healthz
sudo -n systemctl restart cuc-kb-mcp
health http://127.0.0.1:8765/healthz
set -a
. ./.env
set +a
PYTHONPATH="$stage" python3 - <<'PY'
from freshness_mcp_live import rpc, search
url = 'http://127.0.0.1:8765/mcp'
init = rpc(url, 'initialize', {'protocolVersion':'2025-03-26', 'capabilities':{}, 'clientInfo':{'name':'release-smoke','version':'1'}})
assert '区分版本' in init.get('instructions', '')
checks = (
    ('学校现在本科国家奖学金是8000还是10000？', {'d201cea6dc415', 'd9fa2f4c47fd4'}, {'d929975df0454-0002'}),
    ('按中传财字〔2022〕134号，当时收费票据试行多久？', {'d7f4df86d016d'}, set()),
    ('现在博士论文送匿名评审之前能不能申请回避专家？', {'d13a165c3144b'}, {'d23d27d58ca82-0002'}),
)
for q, expected, excluded in checks:
    result = search(url, q)
    ids = {i['chunk_id'] for i in result['results']}
    print('MCP smoke', result['answer_mode'], result['wall_ms'], sorted(ids), flush=True)
    assert not result.get('degraded') and expected & {i['doc_id'] for i in result['results']} and not ids & excluded
PY
trap - ERR
systemctl is-active cuc-kb cuc-kb-mcp cuc-kb-mcp-gateway
printf 'RELEASE ACTIVE\n'
