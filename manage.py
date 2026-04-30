#!/usr/bin/env python3
"""Corkboard management — run from the dev machine.

Subcommands:
  provision <target>   First-time host setup: docker, /srv/corkboard, build image
  cutover   <target>   Stop systemd corkboard, copy sqlite + config, up Docker
  deploy    <target>   git pull + build + up (idempotent)
  cleanup-systemd <target>  Remove the legacy corkboard.service unit
  status    <target>   Show docker compose ps
  logs      <target>   Tail container logs

Targets: staging, prod
"""
from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

USER = "jonnosan"
LOCAL_DIR = Path(__file__).parent.resolve()
SRV_DIR = "/srv/corkboard"
CADDY_CONF_DIR = "/etc/caddy/conf.d"

TARGETS = {
    "staging": {"host": "linode.callendina.com", "env": "staging"},
    "prod": {"host": "vispay-prod.callendina.com", "env": "production"},
}


def die(msg: str) -> None:
    print(f"\n  [!] {msg}\n", file=sys.stderr)
    sys.exit(1)


def banner(text: str) -> None:
    print()
    print("-" * 60)
    print(f"  {text}")
    print("-" * 60)


def get_target(name: str) -> dict:
    if name not in TARGETS:
        die(f"Unknown target {name!r}; expected one of: {', '.join(TARGETS)}")
    return TARGETS[name]


def ssh_run(target: dict, cmd: str, *, capture: bool = False, check: bool = True):
    print(f"  [{_label(target)}] $ {cmd}")
    return subprocess.run(
        ["ssh", f"{USER}@{target['host']}", cmd],
        capture_output=capture,
        text=True,
        check=check,
    )


def ssh_sudo(target: dict, cmd: str, *, check: bool = True):
    return ssh_run(target, f"sudo {cmd}", check=check)


def _label(target: dict) -> str:
    for name, t in TARGETS.items():
        if t is target:
            return name
    return "?"


def cmd_provision(env: str) -> None:
    target = get_target(env)
    banner(f"Provisioning corkboard on {env} ({target['host']})")

    banner("Step 1/4 - Docker / git checks")
    ssh_run(target, "docker --version && docker compose version | head -1")

    banner(f"Step 2/4 - Set up {SRV_DIR} and clone repository")
    ssh_sudo(target, f"mkdir -p {SRV_DIR} && chown {USER}:{USER} {SRV_DIR}")
    ssh_run(
        target,
        f"if [ -d {SRV_DIR}/.git ]; then "
        f"  cd {SRV_DIR} && git pull --ff-only; "
        f"else "
        f"  git clone https://github.com/Callendina/corkboard.git {SRV_DIR}; "
        f"fi",
    )
    ssh_run(target, f"mkdir -p {SRV_DIR}/data {SRV_DIR}/config.d")

    # Auto-migrate config from the old bare-metal location if it exists
    # there but not yet in /srv/corkboard.
    old_dir = f"/home/{USER}/corkboard"
    ssh_run(
        target,
        f"if [ -f {old_dir}/config.yaml ] && [ ! -f {SRV_DIR}/config.yaml ]; then "
        f"  cp {old_dir}/config.yaml {SRV_DIR}/config.yaml; "
        # Rewrite the relative sqlite path to the absolute /app/data path
        # used inside the container. Idempotent — sed on already-absolute
        # paths is a no-op.
        f"  sed -i 's|sqlite+aiosqlite:///corkboard\\.db|sqlite+aiosqlite:////app/data/corkboard.db|g' {SRV_DIR}/config.yaml; "
        f"  echo 'config.yaml migrated and database_url rewritten'; "
        f"fi",
    )
    ssh_run(
        target,
        f"if [ -d {old_dir}/config.d ]; then "
        f"  cp -n {old_dir}/config.d/*.yaml {SRV_DIR}/config.d/ 2>/dev/null || true; "
        f"  ls {SRV_DIR}/config.d/; "
        f"fi",
    )

    banner("Step 3/4 - Pre-warm image build")
    ssh_run(target, f"cd {SRV_DIR} && IMAGE_TAG=latest docker compose build")

    banner("Step 4/4 - Provision complete")
    print(f"  Host:    {target['host']}")
    print(f"  Path:    {SRV_DIR}")
    print(f"  Next:    create {SRV_DIR}/data/.env (CORKBOARD_ENV=staging|production)")
    print(f"  Then:    python manage.py cutover {env}")


def cmd_cutover(env: str) -> None:
    target = get_target(env)
    old_dir = f"/home/{USER}/corkboard"

    banner(f"Cutover: systemd → Docker on {env} ({target['host']})")

    # Sanity checks
    env_check = ssh_run(
        target,
        f"test -f {SRV_DIR}/data/.env && echo found || echo missing",
        capture=True, check=False,
    )
    if env_check.stdout.strip() != "found":
        die(f"{SRV_DIR}/data/.env not found on {target['host']}.\n"
            f"  Create it (mode 600, owner {USER}) with at minimum:\n"
            f"    CORKBOARD_ENV={target['env']}\n"
            f"  Then re-run cutover.")

    cfg_check = ssh_run(
        target,
        f"test -f {SRV_DIR}/config.yaml && echo found || echo missing",
        capture=True, check=False,
    )
    if cfg_check.stdout.strip() != "found":
        die(f"{SRV_DIR}/config.yaml not found.\n"
            f"  Copy your config.yaml + config.d/*.yaml from {old_dir}/ to {SRV_DIR}/.\n"
            f"  Update database_url in config.yaml to:\n"
            f"    sqlite+aiosqlite:////app/data/corkboard.db")

    banner("Step 1/3 - Stop systemd corkboard")
    ssh_sudo(target, "systemctl stop corkboard", check=False)

    banner("Step 2/3 - Copy sqlite state")
    ssh_run(
        target,
        f"if [ -f {old_dir}/corkboard.db ] && [ ! -f {SRV_DIR}/data/corkboard.db ]; then "
        f"  cp -p {old_dir}/corkboard.db* {SRV_DIR}/data/ 2>/dev/null || true; "
        f"  echo 'sqlite copied'; "
        f"else "
        f"  echo 'sqlite state already present in /srv/corkboard/data/ or no db to copy'; "
        f"fi",
    )

    banner("Step 3/3 - docker compose up")
    ssh_run(target, f"cd {SRV_DIR} && IMAGE_TAG=latest docker compose up -d --remove-orphans")
    ssh_run(target, f"cd {SRV_DIR} && docker compose ps")
    health = ssh_run(
        target,
        "curl -sf http://localhost:9200/health -o /dev/null && echo responding || echo not_responding",
        capture=True, check=False,
    )
    print(f"  Healthcheck: {health.stdout.strip()}")

    banner("Cutover complete")
    print(f"  Host:    {target['host']}")
    print()
    print(f"  ROLLBACK:")
    print(f"    ssh {USER}@{target['host']}")
    print(f"    cd {SRV_DIR} && docker compose down")
    print(f"    sudo systemctl start corkboard")
    print()
    print(f"  Once stable: manage.py cleanup-systemd {env}")


def cmd_deploy(env: str) -> None:
    target = get_target(env)
    banner(f"Deploying corkboard to {env} ({target['host']})")
    ssh_run(target, f"cd {SRV_DIR} && git pull && IMAGE_TAG=latest docker compose build && IMAGE_TAG=latest docker compose up -d --remove-orphans")
    ssh_run(target, f"cd {SRV_DIR} && docker compose ps")


def cmd_cleanup_systemd(env: str) -> None:
    target = get_target(env)
    banner(f"Removing legacy systemd corkboard.service on {env}")

    docker_up = ssh_run(
        target,
        f"cd {SRV_DIR} && docker compose ps -q corkboard | wc -l",
        capture=True, check=False,
    )
    if docker_up.stdout.strip() == "0":
        die("Docker corkboard is not running — refusing to remove systemd unit. "
            "Start the Docker stack first or run cutover.")

    ssh_sudo(target, "systemctl disable --now corkboard 2>/dev/null || true")
    ssh_sudo(target, "rm -f /etc/systemd/system/corkboard.service")
    ssh_sudo(target, "systemctl daemon-reload")
    print("  systemd corkboard.service removed")


def cmd_status(env: str) -> None:
    target = get_target(env)
    ssh_run(target, f"cd {SRV_DIR} && docker compose ps")


def cmd_logs(env: str) -> None:
    target = get_target(env)
    ssh_run(target, f"cd {SRV_DIR} && docker compose logs --tail=100 corkboard")


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    cmd = sys.argv[1].replace("-", "_")
    arg = sys.argv[2] if len(sys.argv) > 2 else None
    fn = globals().get(f"cmd_{cmd}")
    if not fn:
        print(__doc__)
        die(f"Unknown command: {cmd}")
    if arg is None:
        die(f"Usage: python manage.py {sys.argv[1]} <staging|prod>")
    fn(arg)


if __name__ == "__main__":
    main()
