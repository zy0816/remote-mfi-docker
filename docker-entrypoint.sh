#!/bin/sh
set -eu

if [ -n "${RELAY_SSH_HOST:-}" ]; then
    : "${RELAY_SSH_USER:?必须设置 RELAY_SSH_USER}"
    : "${RELAY_REMOTE_PORT:?必须设置 RELAY_REMOTE_PORT}"
    key_path="${RELAY_SSH_KEY_PATH:-/run/secrets/relay_ssh_key}"
    known_hosts_path="${RELAY_KNOWN_HOSTS_PATH:-/run/secrets/relay_known_hosts}"
    if [ ! -r "$key_path" ] || [ ! -r "$known_hosts_path" ]; then
        echo "缺少只读的 SSH 私钥或 known_hosts 文件" >&2
        exit 2
    fi
    autossh -M 0 -N \
        -i "$key_path" \
        -p "${RELAY_SSH_PORT:-22}" \
        -o BatchMode=yes \
        -o ExitOnForwardFailure=yes \
        -o ServerAliveInterval=20 \
        -o ServerAliveCountMax=3 \
        -o StrictHostKeyChecking=yes \
        -o UserKnownHostsFile="$known_hosts_path" \
        -R "127.0.0.1:${RELAY_REMOTE_PORT}:127.0.0.1:${MFI_BIND_PORT:-8787}" \
        "${RELAY_SSH_USER}@${RELAY_SSH_HOST}" &
fi

exec python -m mfi_service --host "${MFI_BIND_HOST:-127.0.0.1}" --port "${MFI_BIND_PORT:-8787}"
