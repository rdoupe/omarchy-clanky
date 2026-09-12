#!/usr/bin/env python3
"""Producer-side ceilings and process-tree cleanup for clanky-agent-run."""
from __future__ import annotations

import contextlib
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest

HELPER = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "clanky-agent-run")
)
EXIT_OVERFLOW = 125
SAFE_TMP_PARENT = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".launch-tmp")


@contextlib.contextmanager
def _safe_tempdir():
    """Temp dir whose ancestors are not group/world-writable (/tmp is 1777)."""
    os.makedirs(SAFE_TMP_PARENT, mode=0o755, exist_ok=True)
    os.chmod(SAFE_TMP_PARENT, 0o755)
    tmp = tempfile.mkdtemp(dir=SAFE_TMP_PARENT)
    os.chmod(tmp, 0o755)
    try:
        yield tmp
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def run_helper(command, stdout_bytes=64, stderr_bytes=64, stdin=None, timeout=5, env=None):
    argv = [
        sys.executable,
        HELPER,
        "--stdout-bytes",
        str(stdout_bytes),
        "--stderr-bytes",
        str(stderr_bytes),
        "--",
    ] + command
    return subprocess.run(
        argv,
        input=stdin,
        capture_output=True,
        timeout=timeout,
        env=env,
    )


def pid_alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


class HelperCeilings(unittest.TestCase):
    def test_forwards_small_stdout_and_stderr(self):
        proc = run_helper(
            [
                sys.executable,
                "-c",
                "import sys; sys.stdout.write('hello'); sys.stderr.write('oops')",
            ]
        )
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, b"hello")
        self.assertEqual(proc.stderr, b"oops")

    def test_forwards_stdin(self):
        proc = run_helper(
            [sys.executable, "-c", "import sys; sys.stdout.write(sys.stdin.read())"],
            stdin=b"prompt\n",
        )
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, b"prompt\n")

    def test_stdout_ceiling_is_exact_and_fail_closed(self):
        proc = run_helper(
            [
                sys.executable,
                "-c",
                "import sys; sys.stdout.buffer.write(b'x' * 10000); sys.stdout.buffer.flush()",
            ],
            stdout_bytes=64,
        )
        self.assertEqual(proc.returncode, EXIT_OVERFLOW)
        self.assertEqual(len(proc.stdout), 64)
        self.assertEqual(proc.stdout, b"x" * 64)

    def test_stderr_ceiling_is_exact_and_fail_closed(self):
        proc = run_helper(
            [
                sys.executable,
                "-c",
                "import sys; sys.stderr.buffer.write(b'y' * 10000); sys.stderr.buffer.flush()",
            ],
            stderr_bytes=32,
        )
        self.assertEqual(proc.returncode, EXIT_OVERFLOW)
        self.assertEqual(len(proc.stderr), 32)
        self.assertEqual(proc.stderr, b"y" * 32)

    def test_exact_ceiling_without_extra_byte_is_ok(self):
        proc = run_helper(
            [
                sys.executable,
                "-c",
                "import sys; sys.stdout.buffer.write(b'z' * 64)",
            ],
            stdout_bytes=64,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, b"z" * 64)

    def test_megabyte_flood_never_exceeds_ceiling(self):
        proc = run_helper(
            [
                sys.executable,
                "-c",
                "import sys; sys.stdout.buffer.write(b'A' * (2 * 1024 * 1024))",
            ],
            stdout_bytes=128,
        )
        self.assertEqual(proc.returncode, EXIT_OVERFLOW)
        self.assertEqual(len(proc.stdout), 128)

    def test_overflow_kills_process_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            pidfile = os.path.join(tmp, "grand.pid")
            child = r"""
import os, sys, time
child = os.fork()
if child == 0:
    # New session so a group-only kill of the parent would miss us.
    os.setsid()
    with open(%r, "w") as fh:
        fh.write(str(os.getpid()))
    while True:
        sys.stdout.buffer.write(b"w" * 4096)
        sys.stdout.buffer.flush()
        time.sleep(0.01)
os.waitpid(child, 0)
""" % pidfile
            proc = run_helper(
                [sys.executable, "-c", child],
                stdout_bytes=64,
                timeout=8,
            )
            self.assertEqual(proc.returncode, EXIT_OVERFLOW)
            self.assertLessEqual(len(proc.stdout), 64)
            deadline = time.time() + 2
            grand = None
            while time.time() < deadline:
                if os.path.exists(pidfile):
                    with open(pidfile, "r", encoding="utf-8") as fh:
                        grand = int(fh.read().strip() or "0")
                    if grand and not pid_alive(grand):
                        break
                time.sleep(0.05)
            self.assertTrue(grand, "grandchild never wrote a pid")
            self.assertFalse(pid_alive(grand), "grandchild survived overflow kill")

    def test_overflow_kills_sigterm_ignoring_grandchild(self):
        with tempfile.TemporaryDirectory() as tmp:
            pidfile = os.path.join(tmp, "stubborn.pid")
            child = r"""
import os, signal, sys, time
child = os.fork()
if child == 0:
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    with open(%r, "w") as fh:
        fh.write(str(os.getpid()))
    while True:
        sys.stdout.buffer.write(b"w" * 4096)
        sys.stdout.buffer.flush()
        time.sleep(0.01)
os.waitpid(child, 0)
""" % pidfile
            proc = run_helper(
                [sys.executable, "-c", child],
                stdout_bytes=64,
                timeout=8,
            )
            self.assertEqual(proc.returncode, EXIT_OVERFLOW)
            self.assertLessEqual(len(proc.stdout), 64)
            deadline = time.time() + 2
            stubborn = None
            while time.time() < deadline:
                if os.path.exists(pidfile):
                    with open(pidfile, "r", encoding="utf-8") as fh:
                        stubborn = int(fh.read().strip() or "0")
                    if stubborn and not pid_alive(stubborn):
                        break
                time.sleep(0.05)
            self.assertTrue(stubborn, "stubborn grandchild never wrote a pid")
            self.assertFalse(pid_alive(stubborn), "SIG_IGN grandchild survived")

    def test_sigterm_kills_process_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            pidfile = os.path.join(tmp, "kid.pid")
            child = r"""
import os, sys, time
with open(%r, "w") as fh:
    fh.write(str(os.getpid()))
    fh.flush()
time.sleep(30)
""" % pidfile
            argv = [
                sys.executable,
                HELPER,
                "--stdout-bytes",
                "64",
                "--stderr-bytes",
                "64",
                "--",
                sys.executable,
                "-c",
                child,
            ]
            helper = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                deadline = time.time() + 3
                kid = None
                while time.time() < deadline:
                    if os.path.exists(pidfile):
                        with open(pidfile, "r", encoding="utf-8") as fh:
                            kid = int(fh.read().strip() or "0")
                        if kid:
                            break
                    time.sleep(0.02)
                self.assertTrue(kid, "child never wrote a pid")
                self.assertTrue(pid_alive(kid))
                helper.send_signal(signal.SIGTERM)
                helper.wait(timeout=3)
                self.assertEqual(helper.returncode, 128 + signal.SIGTERM)
                deadline = time.time() + 2
                while time.time() < deadline and pid_alive(kid):
                    time.sleep(0.05)
                self.assertFalse(pid_alive(kid), "child survived helper SIGTERM")
            finally:
                if helper.stdout:
                    helper.stdout.close()
                if helper.stderr:
                    helper.stderr.close()
                if helper.poll() is None:
                    helper.kill()
                    helper.wait(timeout=2)


def _write_launcher(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("#!/bin/sh\nprintf %s\n" % payload)
    os.chmod(path, 0o755)
    os.chmod(os.path.dirname(path), 0o755)


class OmarchyMiseResolution(unittest.TestCase):
    def test_bare_name_runs_standard_mise_shim(self):
        with _safe_tempdir() as tmp:
            shims = os.path.join(tmp, ".local", "share", "mise", "shims")
            _write_launcher(os.path.join(shims, "claude"), "MISE_SHIM")
            evil = os.path.join(tmp, "evil")
            _write_launcher(os.path.join(evil, "claude"), "EVIL")
            env = {
                "HOME": tmp,
                "PATH": evil + ":/usr/bin:/bin",
                "LANG": "C",
            }
            proc = run_helper(["claude"], env=env)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(proc.stdout, b"MISE_SHIM")
            self.assertNotIn(b"EVIL", proc.stdout)

    def test_local_bin_is_used_when_mise_shim_is_absent(self):
        with _safe_tempdir() as tmp:
            launcher = os.path.join(tmp, ".local", "bin", "opencode")
            _write_launcher(launcher, "LOCAL_BIN")
            env = {"HOME": tmp, "PATH": "/usr/bin:/bin", "LANG": "C"}
            proc = run_helper(["opencode"], env=env)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(proc.stdout, b"LOCAL_BIN")

    def test_mise_shims_dir_user_owned_is_used(self):
        with _safe_tempdir() as tmp:
            shims = os.path.join(tmp, "custom-shims")
            _write_launcher(os.path.join(shims, "claude"), "FROM_MISE_SHIMS_DIR")
            env = {
                "HOME": os.path.join(tmp, "empty-home"),
                "MISE_SHIMS_DIR": shims,
                "PATH": "/usr/bin:/bin",
                "LANG": "C",
            }
            os.makedirs(env["HOME"])
            proc = run_helper(["claude"], env=env)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(proc.stdout, b"FROM_MISE_SHIMS_DIR")

    def test_mise_data_dir_user_owned_is_used(self):
        with _safe_tempdir() as tmp:
            data = os.path.join(tmp, "mise-data")
            _write_launcher(os.path.join(data, "shims", "claude"), "FROM_MISE_DATA_DIR")
            env = {
                "HOME": os.path.join(tmp, "empty-home"),
                "MISE_DATA_DIR": data,
                "PATH": "/usr/bin:/bin",
                "LANG": "C",
            }
            os.makedirs(env["HOME"])
            proc = run_helper(["claude"], env=env)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(proc.stdout, b"FROM_MISE_DATA_DIR")

    def test_xdg_data_home_user_owned_is_used(self):
        with _safe_tempdir() as tmp:
            xdg = os.path.join(tmp, "xdg-data")
            _write_launcher(os.path.join(xdg, "mise", "shims", "claude"), "FROM_XDG_DATA_HOME")
            env = {
                "HOME": os.path.join(tmp, "empty-home"),
                "XDG_DATA_HOME": xdg,
                "PATH": "/usr/bin:/bin",
                "LANG": "C",
            }
            os.makedirs(env["HOME"])
            proc = run_helper(["claude"], env=env)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(proc.stdout, b"FROM_XDG_DATA_HOME")

    def test_world_writable_inherited_dirs_are_not_used(self):
        cases = (
            ("MISE_SHIMS_DIR", lambda root: root),
            ("MISE_DATA_DIR", lambda root: os.path.join(root, "shims")),
            ("XDG_DATA_HOME", lambda root: os.path.join(root, "mise", "shims")),
        )
        for var, shim_dir in cases:
            with self.subTest(var=var), _safe_tempdir() as tmp:
                inherited = os.path.join(tmp, "inherited")
                _write_launcher(os.path.join(shim_dir(inherited), "claude"), "EVIL")
                os.chmod(shim_dir(inherited), 0o777)
                good = os.path.join(tmp, "home", ".local", "bin", "claude")
                _write_launcher(good, "GOOD")
                env = {
                    "HOME": os.path.join(tmp, "home"),
                    var: inherited,
                    "PATH": "/usr/bin:/bin",
                    "LANG": "C",
                }
                proc = run_helper(["claude"], env=env)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertEqual(proc.stdout, b"GOOD")
                self.assertNotIn(b"EVIL", proc.stdout)

    def test_group_writable_inherited_dirs_are_not_used(self):
        with _safe_tempdir() as tmp:
            inherited = os.path.join(tmp, "group-shims")
            _write_launcher(os.path.join(inherited, "claude"), "EVIL")
            os.chmod(inherited, 0o775)
            good = os.path.join(tmp, "home", ".local", "bin", "claude")
            _write_launcher(good, "GOOD")
            env = {
                "HOME": os.path.join(tmp, "home"),
                "MISE_SHIMS_DIR": inherited,
                "PATH": "/usr/bin:/bin",
                "LANG": "C",
            }
            proc = run_helper(["claude"], env=env)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(proc.stdout, b"GOOD")
            self.assertNotIn(b"EVIL", proc.stdout)

    def test_group_writable_ancestor_is_rejected(self):
        with _safe_tempdir() as tmp:
            ancestor = os.path.join(tmp, "wide")
            leaf = os.path.join(ancestor, "shims")
            _write_launcher(os.path.join(leaf, "claude"), "EVIL")
            os.chmod(leaf, 0o755)
            os.chmod(ancestor, 0o775)
            good = os.path.join(tmp, "home", ".local", "bin", "claude")
            _write_launcher(good, "GOOD")
            env = {
                "HOME": os.path.join(tmp, "home"),
                "MISE_SHIMS_DIR": leaf,
                "PATH": "/usr/bin:/bin",
                "LANG": "C",
            }
            proc = run_helper(["claude"], env=env)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(proc.stdout, b"GOOD")
            self.assertNotIn(b"EVIL", proc.stdout)

    def test_absolute_untrusted_launcher_does_not_reach_popen(self):
        with _safe_tempdir() as tmp:
            launcher = os.path.join(tmp, "agent")
            _write_launcher(launcher, "ABS")
            os.chmod(launcher, 0o775)
            proc = run_helper([launcher], env={"PATH": "/usr/bin:/bin", "LANG": "C", "HOME": tmp})
            self.assertEqual(proc.returncode, 127)
            self.assertNotIn(b"ABS", proc.stdout)

    def test_rejected_bare_name_does_not_reach_popen_via_path(self):
        with _safe_tempdir() as tmp:
            wide = os.path.join(tmp, "wide-bin")
            _write_launcher(os.path.join(wide, "claude"), "EVIL")
            os.chmod(wide, 0o775)
            env = {
                "HOME": os.path.join(tmp, "empty-home"),
                "PATH": wide + ":/usr/bin:/bin",
                "LANG": "C",
            }
            os.makedirs(env["HOME"])
            os.chmod(env["HOME"], 0o755)
            proc = run_helper(["claude"], env=env)
            self.assertEqual(proc.returncode, 127)
            self.assertNotIn(b"EVIL", proc.stdout)

            import importlib.machinery
            import importlib.util

            loader = importlib.machinery.SourceFileLoader("clanky_agent_run", HELPER)
            spec = importlib.util.spec_from_loader(loader.name, loader)
            helper = importlib.util.module_from_spec(spec)
            loader.exec_module(helper)
            helper.TRUSTED_PATH_DIRS = helper.TRUSTED_PATH_DIRS + (wide,)
            cmd, _path = helper.resolve_agent_command(["claude"], env)
            self.assertEqual(cmd, [])


if __name__ == "__main__":
    unittest.main()
