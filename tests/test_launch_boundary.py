#!/usr/bin/env python3
"""Launch-boundary tests: trusted identities, sanitized env, stdin-only prompts."""
from __future__ import annotations

import os
import re
import stat
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
HELPER = os.path.join(ROOT, "clanky-agent-run")
SERVICE = os.path.join(ROOT, "Service.qml")
MODEL = os.path.join(ROOT, "ClankyModel.js")
README = os.path.join(ROOT, "README.md")
SETPRIV = "/usr/bin/setpriv"
PYTHON3 = "/usr/bin/python3"
SECRET = "UNIQUE_PROMPT_TOKEN_7f3a9c"


def read(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def production_argv(command, stdout_bytes=64, stderr_bytes=64):
    return [
        SETPRIV,
        "--pdeathsig",
        "TERM",
        PYTHON3,
        "-I",
        HELPER,
        "--stdout-bytes",
        str(stdout_bytes),
        "--stderr-bytes",
        str(stderr_bytes),
        "--",
    ] + list(command)


def run_production(command, stdin=None, env=None, timeout=5, stdout_bytes=64, stderr_bytes=64):
    return subprocess.run(
        production_argv(command, stdout_bytes=stdout_bytes, stderr_bytes=stderr_bytes),
        input=stdin,
        capture_output=True,
        timeout=timeout,
        env=env,
    )


class QmlAndDocsContract(unittest.TestCase):
    def test_service_uses_absolute_setpriv_and_python3(self):
        qml = read(SERVICE)
        self.assertIn('readonly property string setprivBin: "/usr/bin/setpriv"', qml)
        self.assertIn('readonly property string python3Bin: "/usr/bin/python3"', qml)
        self.assertIn('root.python3Bin, "-I", root.agentHelper', qml)
        self.assertNotRegex(qml, r'\[\s*"setpriv"')
        self.assertNotRegex(qml, r'\[\s*"python3"')
        self.assertNotRegex(qml, r',\s*"python3"\s*,')
        self.assertIn("clearEnvironment: true", qml)

    def test_qml_never_looks_up_wrapper_on_path(self):
        qml = read(SERVICE)
        # Ambient-PATH tokens must not appear as command argv literals.
        self.assertEqual(re.findall(r'"setpriv"', qml), [])
        self.assertEqual(re.findall(r'"python3"', qml), [])

    def test_model_never_places_prompt_on_argv(self):
        src = read(MODEL)
        self.assertIn("stdin: combined", src)
        self.assertIn('stdin: question', src)
        self.assertIsNone(re.search(r"command:\s*\[[^\]]*combined", src))
        self.assertNotIn("stdin: null", src)
        self.assertNotIn('"-p", combined', src)

    def test_readme_does_not_document_argv_prompts(self):
        text = read(README)
        self.assertNotIn("stdin/argv", text)
        self.assertIn("exclusively", text)
        self.assertIn("on stdin", text)
        self.assertIn("/usr/bin/setpriv", text)
        self.assertIn("/usr/bin/python3 -I", text)
        self.assertIn("never argv", text.lower() + text)


class TrustedLaunch(unittest.TestCase):
    def test_setpriv_and_python3_are_trusted_identities(self):
        for path in (SETPRIV, PYTHON3):
            self.assertTrue(os.path.isfile(path), path)
            st = os.stat(path)
            self.assertTrue(st.st_mode & stat.S_IXUSR)
            self.assertFalse(st.st_mode & stat.S_IWOTH, "%s is world-writable" % path)
            real = os.path.realpath(path)
            self.assertTrue(
                real.startswith("/usr/bin/") or real.startswith("/bin/"),
                real,
            )

    def test_helper_shebang_is_absolute_python3(self):
        with open(HELPER, "r", encoding="utf-8") as fh:
            shebang = fh.readline().rstrip("\n")
        self.assertEqual(shebang, "#!/usr/bin/python3")

    def test_shadowed_path_executables_are_not_used(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name in ("python3", "setpriv"):
                shadow = os.path.join(tmp, name)
                with open(shadow, "w", encoding="utf-8") as fh:
                    fh.write("#!/bin/sh\necho SHADOWED\nexit 42\n")
                os.chmod(shadow, 0o755)
            env = os.environ.copy()
            env["PATH"] = tmp + ":" + env.get("PATH", "")
            proc = run_production(
                [PYTHON3, "-c", "import sys; sys.stdout.write('ok')"],
                env=env,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(proc.stdout, b"ok")
            self.assertNotIn(b"SHADOWED", proc.stdout)
            self.assertNotIn(b"SHADOWED", proc.stderr)

    def test_isolated_interpreter_ignores_poisoned_pythonpath(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "argparse.py"), "w", encoding="utf-8") as fh:
                fh.write("raise SystemExit('POISONED')\n")
            env = os.environ.copy()
            env["PYTHONPATH"] = tmp
            proc = run_production(
                [PYTHON3, "-c", "import sys; sys.stdout.write('ok')"],
                env=env,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(proc.stdout, b"ok")
            self.assertNotIn(b"POISONED", proc.stderr)

    def test_prompt_never_appears_in_child_or_helper_cmdline(self):
        child = (
            "import os, sys\n"
            "def cmd(pid):\n"
            "    return open('/proc/%d/cmdline' % pid, 'rb').read().replace(b'\\x00', b' ')\n"
            "sys.stdout.buffer.write(b'CHILD:' + cmd(os.getpid()) + b'\\n')\n"
            "sys.stdout.buffer.write(b'PARENT:' + cmd(os.getppid()) + b'\\n')\n"
            "sys.stdout.buffer.write(b'STDIN:' + sys.stdin.buffer.read())\n"
        )
        proc = run_production(
            [PYTHON3, "-c", child],
            stdin=(SECRET + "\n").encode(),
            stdout_bytes=4096,
            stderr_bytes=4096,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn(b"STDIN:" + SECRET.encode(), proc.stdout)
        listed, _sep, incoming = proc.stdout.partition(b"STDIN:")
        self.assertNotIn(SECRET.encode(), listed)
        self.assertIn(SECRET.encode(), incoming)
        self.assertIn(b"CHILD:", listed)
        self.assertIn(b"PARENT:", listed)
        self.assertNotIn(SECRET.encode(), os.fsencode(" ".join(production_argv([PYTHON3, "-c", child]))))

    def test_ceilings_still_hold_under_trusted_launch(self):
        proc = run_production(
            [
                PYTHON3,
                "-c",
                "import sys; sys.stdout.buffer.write(b'x' * 10000); sys.stdout.buffer.flush()",
            ]
        )
        self.assertEqual(proc.returncode, 125)
        self.assertEqual(proc.stdout, b"x" * 64)


# Mirrors ClankyModel.agentInvocation for the argv/stdin contract.
def agent_invocation(default_agent, prompt, persona, custom=None):
    question = str(prompt or "")
    if isinstance(custom, list) and custom:
        return {"command": [str(x) for x in custom], "stdin": question}
    voice = str(persona or "")
    combined = voice + "\n\n" + question
    mapping = {
        "opencode": ["opencode", "run"],
        "codex": ["codex", "exec", "--skip-git-repo-check"],
        "gemini": ["gemini"],
        "copilot": ["copilot"],
        "crush": ["crush", "run"],
        "grok": ["grok"],
        "pi": ["pi"],
        "omp": ["omp"],
    }
    if default_agent in mapping:
        return {"command": mapping[default_agent], "stdin": combined}
    return {
        "command": ["claude", "-p", "--append-system-prompt", voice],
        "stdin": question,
    }


class StdinOnlyPrompts(unittest.TestCase):
    def test_every_default_agent_keeps_prompt_off_argv(self):
        prompt = SECRET
        persona = "You are Clanky."
        for agent in (
            "claude",
            "opencode",
            "codex",
            "gemini",
            "copilot",
            "crush",
            "grok",
            "pi",
            "omp",
            "",
            "unknown",
        ):
            inv = agent_invocation(agent, prompt, persona)
            joined = "\0".join(inv["command"])
            self.assertNotIn(prompt, joined, agent)
            self.assertIn(prompt, inv["stdin"])
            self.assertTrue(inv["stdin"], agent)

    def test_custom_command_also_uses_stdin(self):
        inv = agent_invocation("claude", SECRET, "persona", custom=["my-agent", "--ask"])
        self.assertEqual(inv["command"], ["my-agent", "--ask"])
        self.assertEqual(inv["stdin"], SECRET)
        self.assertNotIn(SECRET, "\0".join(inv["command"]))

    def test_js_source_matches_python_mapping(self):
        src = read(MODEL)
        for needle in (
            'case "opencode":',
            'return { command: ["opencode", "run"], stdin: combined }',
            'return { command: ["codex", "exec", "--skip-git-repo-check"], stdin: combined }',
            'return { command: ["gemini"], stdin: combined }',
            'return { command: ["copilot"], stdin: combined }',
            'return { command: ["crush", "run"], stdin: combined }',
            'return { command: ["grok"], stdin: combined }',
            'return { command: ["pi"], stdin: combined }',
            'return { command: ["omp"], stdin: combined }',
            'command: ["claude", "-p", "--append-system-prompt", voice]',
            "stdin: question",
        ):
            self.assertIn(needle, src)


if __name__ == "__main__":
    unittest.main()
