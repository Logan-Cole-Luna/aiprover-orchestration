"""Run a command under a Landlock sandbox (Linux 6.7 or later, no root).

    python3 -I sandbox.py [--read DIR]... [--write DIR]... [--cpu-seconds N]
        -- command [argument]...

The command and its children may read the system directories, the Lean
toolchain (`~/.elan`) and the `--read` and `--write` trees, write only in the
`--write` trees, execute only the toolchain, the ELF loader and `git` (which
Lake runs to check package sources), and open no TCP connection (Landlock
ABI 4, Linux 6.7). The environment is replaced by a minimal one, so no
credential of the caller reaches the command. The script uses the standard
library only and fails closed: without Landlock ABI 4 it does not run the
command.

Not covered by Landlock on this kernel: UDP and Unix-domain sockets, and
signals to other processes of the user; the minimal environment names no
socket, and the executable set contains no client for one (`git` has no
shell to start, and its network transports need TCP).
"""

import ctypes
import os
import resource
import sys
from pathlib import Path

# Syscall numbers (x86_64 and aarch64 share the generic table here).
SYS_CREATE_RULESET = 444
SYS_ADD_RULE = 445
SYS_RESTRICT_SELF = 446
CREATE_RULESET_VERSION = 1
RULE_PATH_BENEATH = 1
PR_SET_NO_NEW_PRIVS = 38

# Filesystem rights by ABI version.
EXECUTE = 1 << 0
WRITE_FILE = 1 << 1
READ_FILE = 1 << 2
READ_DIR = 1 << 3
WRITE_TREE = sum(1 << bit for bit in range(4, 13))  # remove and make
REFER = 1 << 13  # ABI 2
TRUNCATE = 1 << 14  # ABI 3
IOCTL_DEV = 1 << 15  # ABI 5
NET_BIND_TCP = 1 << 0  # ABI 4
NET_CONNECT_TCP = 1 << 1

READ = READ_FILE | READ_DIR
SYSTEM_READ = ("/usr", "/lib", "/lib64", "/bin", "/etc")
# Shared libraries are mapped with read rights; only the ELF loader is
# executed.
SYSTEM_EXECUTE = (
    os.path.realpath("/lib64/ld-linux-x86-64.so.2"),
    os.path.realpath("/lib/ld-linux-aarch64.so.1"),
    "/usr/bin/git",
    "/usr/lib/git-core",
)
DEVICES = "/dev"
ELAN_HOME = Path.home() / ".elan"


class RulesetAttr(ctypes.Structure):
    _fields_ = [
        ("handled_access_fs", ctypes.c_uint64),
        ("handled_access_net", ctypes.c_uint64),
    ]


class PathBeneathAttr(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("allowed_access", ctypes.c_uint64),
        ("parent_fd", ctypes.c_int32),
    ]


libc = ctypes.CDLL(None, use_errno=True)
libc.syscall.restype = ctypes.c_long


def abi_version() -> int:
    """Landlock ABI of the running kernel; 0 when unavailable."""
    version = libc.syscall(
        SYS_CREATE_RULESET, None, ctypes.c_size_t(0), CREATE_RULESET_VERSION
    )
    return max(int(version), 0)


def restrict(
    read: list[str], write: list[str], execute: list[str], abi: int
) -> None:
    """Confine this process and its future children to the given trees."""
    handled = EXECUTE | WRITE_FILE | READ | WRITE_TREE
    handled |= REFER if abi >= 2 else 0
    handled |= TRUNCATE if abi >= 3 else 0
    handled |= IOCTL_DEV if abi >= 5 else 0
    attr = RulesetAttr(handled, NET_BIND_TCP | NET_CONNECT_TCP)
    size = ctypes.sizeof(RulesetAttr) if abi >= 4 else 8
    ruleset = libc.syscall(
        SYS_CREATE_RULESET, ctypes.byref(attr), ctypes.c_size_t(size), 0
    )
    if ruleset < 0:
        raise OSError(ctypes.get_errno(), "landlock_create_ruleset")
    writable = handled & ~EXECUTE
    rights = [(path, READ) for path in read]
    rights += [(path, READ | EXECUTE) for path in execute]
    rights += [(path, writable) for path in write]
    rights += [(DEVICES, READ | WRITE_FILE | (IOCTL_DEV if abi >= 5 else 0))]
    for path, access in rights:
        if not os.path.exists(path):
            continue
        fd = os.open(path, os.O_PATH | os.O_CLOEXEC)
        # A rule on a file may carry only file rights.
        if not os.path.isdir(path):
            access &= EXECUTE | WRITE_FILE | READ_FILE | TRUNCATE
        rule = PathBeneathAttr(access & handled, fd)
        status = libc.syscall(
            SYS_ADD_RULE, ruleset, RULE_PATH_BENEATH, ctypes.byref(rule), 0
        )
        os.close(fd)
        if status < 0:
            raise OSError(ctypes.get_errno(), f"landlock_add_rule {path}")
    if libc.prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "prctl(NO_NEW_PRIVS)")
    if libc.syscall(SYS_RESTRICT_SELF, ruleset, 0) < 0:
        raise OSError(ctypes.get_errno(), "landlock_restrict_self")
    os.close(ruleset)


def minimal_environment(home: str) -> dict[str, str]:
    """The environment of the command: tool paths and nothing else."""
    return {
        "PATH": f"{ELAN_HOME / 'bin'}:/usr/bin:/bin",
        "ELAN_HOME": str(ELAN_HOME),
        "HOME": home,
        "TMPDIR": home,
        "LANG": "C.UTF-8",
    }


def main(arguments: list[str]) -> None:
    if "--" not in arguments:
        sys.exit("usage: sandbox.py [--read DIR] [--write DIR] -- command")
    split = arguments.index("--")
    options, command = arguments[:split], arguments[split + 1 :]
    read, write, cpu_seconds = [], [], 0
    for flag, value in zip(options[::2], options[1::2]):
        if flag == "--read":
            read.append(os.path.realpath(value))
        elif flag == "--write":
            write.append(os.path.realpath(value))
        elif flag == "--cpu-seconds":
            cpu_seconds = int(value)
        else:
            sys.exit(f"sandbox.py: unknown option {flag}")
    if not command or not write:
        sys.exit("sandbox.py: a command and a --write tree are required")
    abi = abi_version()
    if abi < 4:
        sys.exit(f"sandbox.py: Landlock ABI {abi} < 4; refusing to run")
    if cpu_seconds:
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    restrict(
        read=[*SYSTEM_READ, *read],
        write=write,
        execute=[*SYSTEM_EXECUTE, str(ELAN_HOME)],
        abi=abi,
    )
    environment = minimal_environment(write[0])
    os.execvpe(command[0], command, environment)


if __name__ == "__main__":
    main(sys.argv[1:])
