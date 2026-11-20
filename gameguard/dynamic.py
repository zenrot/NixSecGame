"""Динамический анализ GameGuard: трассировка установки и сети через strace."""

import ipaddress
import json
import re
import shutil
import subprocess
from collections import Counter
from pathlib import Path

from .catalog import REPOSITORY, read_json

POLICY_PATH = REPOSITORY / "network-policy.json"
DEFAULT_ALLOWED = ("127.0.0.0/8", "::1/128")

INSTALL_FILTER = "trace=%file,%process"
NETWORK_FILTER = "trace=%network"

# Вызовы класса %process, которые не относятся к файловым операциям.
PROCESS_CALLS = {
    "execve", "execveat", "clone", "clone3", "fork", "vfork",
    "exit", "exit_group", "wait4", "waitid", "arch_prctl", "prctl",
    "kill", "tkill", "tgkill",
}
SPAWN_CALLS = {"clone", "clone3", "fork", "vfork"}
# Пути, запись в которые не считается выходом за пределы каталога установки.
IGNORED_WRITE_PREFIXES = ("/dev/", "/proc/", "/sys/")
OUTBOUND_CALLS = {"connect", "sendto", "sendmsg", "sendmmsg"}
MAX_LIST = 200

LINE = re.compile(r"^(?:(?P<pid>\d+)\s+)?(?:(?P<ts>\d+\.\d+)\s+)?(?P<body>.*)$")
UNFINISHED = re.compile(r"^(?P<call>.*?)\s*<unfinished \.\.\.>\s*$")
RESUMED = re.compile(r"^<\.\.\. \w+ resumed>(?P<rest>.*)$")
CALL = re.compile(
    r"^(?P<name>\w+)\((?P<args>.*)\)\s+=\s+(?P<ret>-?\w+)(?:\s+(?P<errno>E[A-Z0-9]+))?"
)
EXITED = re.compile(r"^\+\+\+ (?:exited with (?P<code>\d+)|killed by (?P<sig>\w+)).*\+\+\+$")
STRING = re.compile(r'"((?:[^"\\]|\\.)*)"')
OPEN_WRITE = re.compile(r"\bO_(?:WRONLY|RDWR|CREAT|TRUNC|APPEND)\b")
INET4 = re.compile(r'sin_port=htons\((\d+)\),\s*sin_addr=inet_addr\("([^"]+)"\)')
INET6 = re.compile(r'sin6_port=htons\((\d+)\).*?inet_pton\(AF_INET6,\s*"([^"]+)"')


def parse_trace(text):
    """Разбирает текст strace -f в список вызовов и событий завершения."""
    calls, exits = [], []
    pending = {}
    for raw in text.splitlines():
        match = LINE.match(raw)
        pid = int(match["pid"]) if match["pid"] else 0
        body = match["body"].strip()
        if not body or body.startswith("--- "):
            continue

        finished = EXITED.match(body)
        if finished:
            exits.append({
                "pid": pid,
                "exit_code": int(finished["code"]) if finished["code"] else None,
                "signal": finished["sig"],
            })
            continue

        resumed = RESUMED.match(body)
        if resumed:
            body = pending.pop(pid, "") + resumed["rest"]
        else:
            unfinished = UNFINISHED.match(body)
            if unfinished:
                pending[pid] = unfinished["call"]
                continue

        call = CALL.match(body)
        if call is None:
            continue
        failed = call["ret"] == "-1"
        calls.append({
            "pid": pid,
            "name": call["name"],
            "args": call["args"],
            "ret": call["ret"],
            "errno": call["errno"] if failed else None,
            "ok": not failed and call["ret"] != "?",
        })
    return calls, exits


def _strings(args, limit=None):
    found = [item.replace('\\"', '"') for item in STRING.findall(args)]
    return found[:limit] if limit else found


def _first_path(call):
    found = _strings(call["args"], 1)
    return found[0] if found else None


def _writes_file(call):
    if call["name"] in {"creat"}:
        return True
    if call["name"] not in {"open", "openat", "openat2"}:
        return False
    return OPEN_WRITE.search(call["args"].split('"', 2)[-1]) is not None


def _cap(items):
    items = sorted(set(items))
    return items[:MAX_LIST], max(0, len(items) - MAX_LIST)


def summarize_install(calls, exits, install_dir):
    staging = Path(install_dir).parent
    file_ops = Counter()
    written, deleted, created_dirs, renamed = [], [], [], []
    children, errors = [], []
    errno_count, errno_by_call = Counter(), Counter()
    spawned = 0

    for call in calls:
        name = call["name"]
        if call["errno"]:
            errno_count[call["errno"]] += 1
            errno_by_call[name] += 1
            if call["errno"] != "ENOENT" and len(errors) < 20:
                errors.append({
                    "pid": call["pid"], "syscall": name,
                    "errno": call["errno"], "path": _first_path(call),
                })
        if name in SPAWN_CALLS and call["ok"]:
            spawned += 1
        if name in {"execve", "execveat"} and call["ok"]:
            strings = _strings(call["args"], 3)
            children.append({
                "pid": call["pid"],
                "path": strings[0] if strings else None,
                "argv": strings[1:],
            })
        if name in PROCESS_CALLS:
            continue

        file_ops[name] += 1
        if not call["ok"]:
            continue
        path = _first_path(call)
        if _writes_file(call) and path:
            written.append(path)
        elif name in {"unlink", "unlinkat", "rmdir"} and path:
            deleted.append(path)
        elif name in {"mkdir", "mkdirat"} and path:
            created_dirs.append(path)
        elif name in {"rename", "renameat", "renameat2"}:
            paths = _strings(call["args"], 2)
            if len(paths) == 2:
                renamed.append({"from": paths[0], "to": paths[1]})

    written_list, written_more = _cap(written)
    outside = [
        path for path in set(written)
        if path.startswith("/")
        and not path.startswith(IGNORED_WRITE_PREFIXES)
        and Path(path) != staging
        and staging not in Path(path).parents
    ]
    outside_list, _ = _cap(outside)
    failed_children = [item for item in exits if item["exit_code"] or item["signal"]]

    return {
        "status": "warning" if outside_list else "ok",
        "file_operations": dict(file_ops.most_common()),
        "files_written": written_list,
        "files_written_not_listed": written_more,
        "files_deleted": _cap(deleted)[0],
        "directories_created": _cap(created_dirs)[0],
        "renamed": renamed[:MAX_LIST],
        "writes_outside_staging": outside_list,
        "child_processes": {
            "spawned": spawned,
            "executed": children[:MAX_LIST],
            "failed_exits": failed_children,
        },
        "errors": {
            "total": sum(errno_count.values()),
            "by_errno": dict(errno_count.most_common()),
            "by_syscall": dict(errno_by_call.most_common(10)),
            "samples": errors,
        },
        "total_syscalls": len(calls),
    }


def load_policy(policy_path=None):
    path = Path(policy_path) if policy_path else POLICY_PATH
    allowed = list(DEFAULT_ALLOWED)
    if path.is_file():
        allowed = read_json(path).get("allowed", allowed)
    return [ipaddress.ip_network(item, strict=False) for item in allowed]


def is_allowed(address, networks):
    ip = ipaddress.ip_address(address)
    if ip.version == 6 and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return any(ip.version == net.version and ip in net for net in networks)


def _endpoint(args):
    match = INET4.search(args) or INET6.search(args)
    if match is None:
        return None
    return match.group(2), int(match.group(1))


def _result(call):
    if call["errno"] == "EINPROGRESS":
        return "in_progress"
    return "success" if call["ok"] else "failed"


def _fd(args):
    match = re.match(r"\s*(\d+)", args)
    return int(match.group(1)) if match else None


def _socket_error(args):
    """Итог неблокирующего connect: значение из getsockopt(fd, SOL_SOCKET, SO_ERROR, [..])."""
    match = re.search(r"SO_ERROR,\s*\[(\w+)\]", args)
    return match.group(1) if match else None


def summarize_network(calls, networks):
    syscalls = Counter()
    events = []
    pending = {}
    binds = Counter()
    unix_sockets = 0

    for call in calls:
        name = call["name"]
        syscalls[name] += 1
        if name == "bind":
            endpoint = _endpoint(call["args"])
            if endpoint:
                binds[endpoint] += 1
        elif name == "getsockopt" and call["ok"]:
            # Неблокирующий connect заканчивается ответом в SO_ERROR.
            event = pending.pop((call["pid"], _fd(call["args"])), None)
            error = _socket_error(call["args"])
            if event is not None and error is not None:
                event["result"] = "success" if error == "0" else "failed"
                event["errno"] = None if error == "0" else error
        elif name in OUTBOUND_CALLS:
            endpoint = _endpoint(call["args"])
            if endpoint is None:
                unix_sockets += "AF_UNIX" in call["args"]
                continue
            event = {
                "syscall": name, "ip": endpoint[0], "port": endpoint[1],
                "result": _result(call), "errno": call["errno"],
            }
            events.append(event)
            if event["result"] == "in_progress":
                pending[(call["pid"], _fd(call["args"]))] = event

    connections = {}
    for event in events:
        key = (event["syscall"], event["ip"], event["port"], event["result"], event["errno"])
        if key not in connections:
            connections[key] = {**event, "allowed": is_allowed(event["ip"], networks), "count": 0}
        connections[key]["count"] += 1

    items = sorted(connections.values(), key=lambda c: (c["ip"], c["port"], c["syscall"]))
    violations = [c for c in items if not c["allowed"]]
    return {
        "status": "warning" if violations else "ok",
        "syscalls": dict(syscalls.most_common()),
        "connections": items,
        "violations": violations,
        "listening": [{"ip": ip, "port": port, "count": n} for (ip, port), n in binds.items()],
        "unix_socket_calls": unix_sockets,
        "allowed_networks": [str(net) for net in networks],
    }


def _run_strace(argv, report_dir, name, trace_filter):
    report_dir = Path(report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    trace_file = report_dir / f"{name}.strace"
    stderr_file = report_dir / f"{name}.stderr"

    strace = shutil.which("strace")
    if strace is None:
        return None, trace_file, "strace не найден в PATH", 127
    command = [strace, "-f", "-ttt", "-s", "256", "-o", str(trace_file), "-e", trace_filter, "--", *argv]
    with stderr_file.open("wb") as stderr:
        code = subprocess.run(command, stderr=stderr, check=False).returncode
    tail = stderr_file.read_text(encoding="utf-8", errors="replace")[-2000:]
    text = trace_file.read_text(encoding="utf-8", errors="replace") if trace_file.exists() else ""
    return text, trace_file, tail, code


def _save(report_dir, name, result):
    path = Path(report_dir) / f"{name}-summary.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def trace_install(argv, install_dir, report_dir):
    text, trace_file, stderr, code = _run_strace(argv, report_dir, "install", INSTALL_FILTER)
    result = {"exit_code": code, "install_dir": str(install_dir), "trace_file": str(trace_file)}
    if text is None:
        return {**result, "status": "error", "reason": stderr}
    calls, exits = parse_trace(text)
    result.update(summarize_install(calls, exits, install_dir))
    if code != 0:
        result["status"] = "error"
        result["stderr_tail"] = stderr
    result["summary_file"] = str(_save(report_dir, "install", result))
    return result


def trace_network(argv, report_dir, policy_path=None):
    text, trace_file, stderr, code = _run_strace(argv, report_dir, "network", NETWORK_FILTER)
    result = {"exit_code": code, "trace_file": str(trace_file)}
    if text is None:
        return {**result, "status": "error", "reason": stderr}
    calls, _ = parse_trace(text)
    result.update(summarize_network(calls, load_policy(policy_path)))
    if code != 0:
        result["stderr_tail"] = stderr
    result["summary_file"] = str(_save(report_dir, "network", result))
    return result
