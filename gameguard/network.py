"""Run traced games as their caller inside a system cgroup with an IP filter."""

import ipaddress
import json
import os
import pwd
import subprocess
import sys
import uuid
from pathlib import Path

from .catalog import REPOSITORY, read_json

HELPER = "/run/current-system/sw/bin/gameguard-network"
SYSTEMD_RUN = "@systemd_run@"
SYSTEMCTL = "@systemctl@"
DBUS_RUN_SESSION = "@dbus_run_session@"
BPFTOOL = "@bpftool@"
PACKAGE_HELPER = "@package_helper@"
ENVIRONMENT_KEYS = {
    "DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY", "XDG_RUNTIME_DIR",
    "XDG_DATA_HOME", "XDG_CONFIG_HOME", "SDL_AUDIODRIVER",
    "SDL_VIDEODRIVER", "QT_QPA_PLATFORM", "QT_QUICK_BACKEND",
    "LANG", "LC_ALL",
}


def enforced_command(argv):
    environment = {key: os.environ[key] for key in ENVIRONMENT_KEYS if key in os.environ}
    return ["sudo", "-n", HELPER, json.dumps(environment), *map(str, argv)]


def check_filter():
    cgroup = Path("/proc/self/cgroup").read_text().strip()
    if not cgroup.startswith("0::/"):
        raise RuntimeError("GameGuard требует cgroup v2")
    unit = Path(cgroup.removeprefix("0::")).name
    if not unit.startswith("gameguard-net-") or not unit.endswith(".service"):
        raise RuntimeError("IP-фильтр требует отдельной службы GameGuard")
    directory = "/sys/fs/cgroup" + cgroup.removeprefix("0::")
    result = subprocess.run(
        [BPFTOOL, "-j", "cgroup", "show", directory],
        capture_output=True, text=True, check=True,
    )
    attached = {entry["attach_type"] for entry in json.loads(result.stdout)}
    if not {"cgroup_inet_ingress", "cgroup_inet_egress"} <= attached:
        raise RuntimeError(f"IP-фильтр не подключён: {sorted(attached)}")
    properties = subprocess.run(
        [SYSTEMCTL, "show", unit, "-p", "IPAddressDeny", "-p", "IPAddressAllow"],
        capture_output=True, text=True, check=True,
    )
    values = dict(line.split("=", 1) for line in properties.stdout.splitlines() if "=" in line)
    denied = set(values.get("IPAddressDeny", "").split())
    allowed = set(values.get("IPAddressAllow", "").split())
    expected = {
        str(ipaddress.ip_network(value, strict=False))
        for value in read_json(REPOSITORY / "network-policy.json")["allowed"]
    }
    if denied != {"0.0.0.0/0", "::/0"} or allowed != expected:
        raise RuntimeError("IP-фильтр службы отличается от системной политики")


def service_command(uid, environment, argv):
    if uid < 1000:
        raise ValueError("Разрешён запуск только от обычного пользователя")
    account = pwd.getpwuid(uid)
    if not isinstance(environment, dict) or environment.keys() - ENVIRONMENT_KEYS:
        raise ValueError("Недопустимые переменные окружения")
    if any(not isinstance(value, str) or "\x00" in value for value in environment.values()):
        raise ValueError("Недопустимое значение окружения")
    if not argv or not Path(argv[0]).is_absolute():
        raise ValueError("Требуется абсолютный путь к запускаемой программе")

    policy = read_json(REPOSITORY / "network-policy.json")
    allowed = [str(ipaddress.ip_network(value, strict=False)) for value in policy["allowed"]]
    command = [
        SYSTEMD_RUN, "--quiet", "--wait", "--pipe", "--collect",
        f"--unit=gameguard-net-{uuid.uuid4().hex}.service",
        "--service-type=exec", "--expand-environment=no",
        f"--uid={uid}", f"--gid={account.pw_gid}",
        f"--working-directory={account.pw_dir}",
        "--property=IPAddressDeny=any",
        "--property=IPAccounting=yes",
        "--property=NoNewPrivileges=yes",
        "--property=ProtectControlGroups=yes",
        "--property=RestrictNamespaces=yes",
        "--property=RestrictSUIDSGID=yes",
        "--property=CapabilityBoundingSet=",
        "--property=RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6",
        f"--property=ExecStartPre=+{PACKAGE_HELPER} --check",
        f"--property=InaccessiblePaths=-/run/user/{uid}/bus -/run/user/{uid}/systemd/private -/run/dbus/system_bus_socket",
        f"--setenv=HOME={account.pw_dir}",
        f"--setenv=USER={account.pw_name}",
        "--setenv=PATH=/run/current-system/sw/bin:/run/wrappers/bin",
    ]
    command.append("--property=IPAddressAllow=" + " ".join(allowed))
    command.extend(f"--setenv={key}={value}" for key, value in sorted(environment.items()))
    command.extend(["--", DBUS_RUN_SESSION, "--", *argv])
    return command


def helper_main():
    try:
        if os.geteuid() != 0:
            raise ValueError("Этот помощник запускается через sudo")
        if sys.argv[1:] == ["--check"]:
            check_filter()
            return 0
        uid = int(os.environ["SUDO_UID"])
        environment = json.loads(sys.argv[1])
        command = service_command(uid, environment, sys.argv[2:])
        # Never pass the caller's environment to a privileged systemd client.
        return subprocess.run(
            command, env={"PATH": "/run/current-system/sw/bin", "LANG": "C.UTF-8"},
            check=False,
        ).returncode
    except (ValueError, KeyError, IndexError, OSError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"GameGuard network: {error}", file=sys.stderr)
        return 125
