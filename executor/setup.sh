#!/usr/bin/env bash
# One-time host setup: fetch network (internal) + allow-list proxy. Safe to re-run.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
IMG="${GATE_IMAGE:-python:3.12-slim}"
docker image inspect "$IMG" >/dev/null 2>&1 || docker pull "$IMG"
# Fixed IP, not a DNS alias: gVisor does not see Docker's embedded resolver (127.0.0.11).
SUBNET=172.30.77.0/24
PROXY_IP=172.30.77.2
docker rm -f af-proxy >/dev/null 2>&1 || true
docker network rm af-fetch >/dev/null 2>&1 || true
docker network create --internal --subnet "$SUBNET" af-fetch
docker run -d --name af-proxy --restart unless-stopped --network bridge \
  --read-only --cap-drop ALL --security-opt no-new-privileges --memory 128m --pids-limit 64 \
  -e AF_ALLOW="${AF_ALLOW:-pypi.org,files.pythonhosted.org}" \
  -v "$HERE/proxy.py:/proxy.py:ro" "$IMG" python /proxy.py
docker network connect --ip "$PROXY_IP" af-fetch af-proxy
echo "proxy + af-fetch ready"
