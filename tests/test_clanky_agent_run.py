#!/usr/bin/env python3
"""Producer-side ceilings and process-tree cleanup for clanky-agent-run."""
from __future__ import annotations

import os
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


def run_helper(command, stdout_bytes=64, stderr_bytes=64, stdin=None, timeout=5):
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


if __name__ == "__main__":
    unittest.main()
