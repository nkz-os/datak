"""Run diagnostic commands on a remote DaTaK host over SSH.

Credentials are read from environment variables — never hardcoded:
    DATAK_REMOTE_HOST       Target hostname or IP
    DATAK_REMOTE_USER       SSH user
    DATAK_REMOTE_PASSWORD   SSH password (prefer SSH keys when possible)

The script refuses to run if any of these is unset.
"""

import os
import sys

import pexpect


def run_remote_command(host: str, user: str, password: str, command: str) -> str:
    ssh_command = f"ssh -o StrictHostKeyChecking=no {user}@{host} '{command}'"
    child = pexpect.spawn(ssh_command, encoding='utf-8')
    child.logfile = sys.stdout
    try:
        i = child.expect(['password:', pexpect.EOF, pexpect.TIMEOUT], timeout=30)
        if i == 0:
            child.sendline(password)
            child.expect(pexpect.EOF)
            return child.before
        if i == 1:
            return child.before
        return "Timeout"
    except Exception as e:
        return str(e)


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        sys.stderr.write(
            f"error: {name} is not set. "
            "Export DATAK_REMOTE_HOST / DATAK_REMOTE_USER / DATAK_REMOTE_PASSWORD before running.\n"
        )
        sys.exit(2)
    return value


if __name__ == "__main__":
    host = _require_env("DATAK_REMOTE_HOST")
    user = _require_env("DATAK_REMOTE_USER")
    password = _require_env("DATAK_REMOTE_PASSWORD")

    print("--- Docker PS ---")
    print(run_remote_command(host, user, password, "docker ps"))

    print("\n--- Backend Logs (last 20 lines) ---")
    print(run_remote_command(host, user, password, "docker logs --tail 20 datak-backend"))

    print("\n--- Mosquitto Logs (last 20 lines) ---")
    print(run_remote_command(host, user, password, "docker logs --tail 20 datak-mosquitto"))
