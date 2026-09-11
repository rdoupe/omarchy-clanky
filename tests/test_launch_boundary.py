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
HELPER_SUFFIX = "/clanky-agent-run"


def read(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def agent_helper_from_resolved_url(raw):
    """Mirror Service.qml agentHelper: strip the file:// prefix from Qt.resolvedUrl."""
    raw = str(raw or "")
    if raw.startswith("file://"):
        raw = raw[7:]
    return raw


def trusted_helper(path):
    """Mirror Service.qml trustedHelper, including the 17-char suffix check."""
    p = str(path or "")
    if len(p) < 2 or p[0] != "/":
        return False
    if "/./" in p or "/../" in p:
        return False
    if p.endswith("/.") or p.endswith("/.."):
        return False
    return p[-17:] == HELPER_SUFFIX


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


class TrustedHelperPath(unittest.TestCase):
    def test_qml_suffix_length_matches_helper_name(self):
        qml = read(SERVICE)
        self.assertEqual(len(HELPER_SUFFIX), 17)
        self.assertIn('p.slice(-17) === "/clanky-agent-run"', qml)
        self.assertNotIn("p.slice(-16)", qml)

    def test_trusted_helper_accepts_agent_helper(self):
        # agentHelper is Qt.resolvedUrl("clanky-agent-run") with file:// stripped.
        self.assertTrue(os.path.isabs(HELPER))
        self.assertTrue(HELPER.endswith(HELPER_SUFFIX))
        # Off-by-one: last 16 chars drop the leading slash and never match.
        self.assertEqual(HELPER[-16:], "clanky-agent-run")
        self.assertNotEqual(HELPER[-16:], HELPER_SUFFIX)
        self.assertEqual(HELPER[-17:], HELPER_SUFFIX)
        self.assertTrue(trusted_helper(HELPER))
        self.assertTrue(trusted_helper(agent_helper_from_resolved_url("file://" + HELPER)))

    def test_trusted_helper_related_paths(self):
        plugin = "/home/user/.config/omarchy/plugins/clanky/clanky-agent-run"
        self.assertTrue(trusted_helper(plugin))
        self.assertTrue(trusted_helper(agent_helper_from_resolved_url("file://" + plugin)))
        self.assertFalse(trusted_helper("clanky-agent-run"))
        self.assertFalse(trusted_helper(""))
        self.assertFalse(trusted_helper("/tmp/./clanky-agent-run"))
        self.assertFalse(trusted_helper("/tmp/../clanky-agent-run"))
        self.assertFalse(trusted_helper("/tmp/clanky-agent-run-extra"))
        self.assertFalse(trusted_helper("/tmp/clanky-agent-ru"))


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


# Mirrors ClankyModel.sanitizedPath / agentCredentialNames.
TRUSTED_PATH_DIRS = ("/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin")
FALLBACK_PATH = "/usr/bin:/bin"
AGENT_CREDENTIALS = {
    "claude": [
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "CLAUDE_CODE_OAUTH_TOKEN",
        "CLAUDE_CONFIG_DIR",
    ],
    "codex": ["OPENAI_API_KEY", "OPENAI_BASE_URL", "AZURE_OPENAI_API_KEY", "CODEX_HOME"],
    "gemini": ["GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_GEMINI_BASE_URL"],
    "copilot": ["GITHUB_TOKEN", "GH_TOKEN", "COPILOT_GITHUB_TOKEN"],
    "grok": ["XAI_API_KEY", "GROK_API_KEY"],
    "opencode": [],
    "crush": [],
    "pi": [],
    "omp": [],
}
UNRELATED_CREDENTIALS = (
    "OPENROUTER_API_KEY",
    "HF_TOKEN",
    "TOGETHER_API_KEY",
    "MISTRAL_API_KEY",
    "DEEPSEEK_API_KEY",
)


def is_trusted_path_dir(path):
    p = str(path or "")
    if p in ("", ".", ".."):
        return False
    if not p.startswith("/"):
        return False
    if "/./" in p or "/../" in p:
        return False
    if p.endswith("/.") or p.endswith("/.."):
        return False
    if p.endswith("/"):
        p = p[:-1]
    return p in TRUSTED_PATH_DIRS


def sanitized_path(raw_path):
    out = []
    seen = set()
    for part in str(raw_path or "").split(":"):
        if not is_trusted_path_dir(part):
            continue
        p = part[:-1] if part.endswith("/") else part
        if p in seen:
            continue
        seen.add(p)
        out.append(p)
    return ":".join(out) if out else FALLBACK_PATH


def agent_credential_names(default_agent, custom_command=None):
    if isinstance(custom_command, list) and custom_command:
        return []
    name = str(default_agent or "")
    if name in AGENT_CREDENTIALS:
        return list(AGENT_CREDENTIALS[name])
    return list(AGENT_CREDENTIALS["claude"])


class TrustedPathAllowlist(unittest.TestCase):
    def test_drops_relative_and_traversal_entries(self):
        raw = ".:..:./bin:/usr/bin/./extra:/usr/bin/../sbin:/tmp/../usr/bin:/usr/bin"
        self.assertEqual(sanitized_path(raw), "/usr/bin")

    def test_drops_untrusted_inherited_absolute_entries(self):
        raw = "/tmp/evil:/home/user/.local/bin:/usr/bin:/opt/shadow:/bin"
        self.assertEqual(sanitized_path(raw), "/usr/bin:/bin")
        self.assertNotIn("/tmp/evil", sanitized_path(raw))
        self.assertNotIn("/home/user/.local/bin", sanitized_path(raw))
        self.assertNotIn("/opt/shadow", sanitized_path(raw))

    def test_keeps_trusted_dirs_in_inherited_order(self):
        raw = "/bin:/usr/local/bin:/usr/bin"
        self.assertEqual(sanitized_path(raw), "/bin:/usr/local/bin:/usr/bin")

    def test_empty_or_all_untrusted_falls_back(self):
        self.assertEqual(sanitized_path(""), FALLBACK_PATH)
        self.assertEqual(sanitized_path("/tmp:/home/user/bin"), FALLBACK_PATH)

    def test_qml_delegates_to_model_allowlist(self):
        qml = read(SERVICE)
        src = read(MODEL)
        self.assertIn("ClankyModel.sanitizedPath(Quickshell.env(\"PATH\"))", qml)
        self.assertIn('var trustedPathDirs = ["/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin"]', src)
        # Must not rebuild PATH by pushing every inherited absolute entry.
        self.assertNotRegex(qml, r"if \(p\.charAt\(0\) !== \"/\"\) continue")
        self.assertNotIn("out.push(p)", qml)


class ScopedAgentCredentials(unittest.TestCase):
    def test_custom_command_gets_no_credentials(self):
        self.assertEqual(agent_credential_names("claude", ["my-agent", "--ask"]), [])
        self.assertEqual(agent_credential_names("gemini", ["/tmp/custom"]), [])

    def test_default_agents_receive_only_their_keys(self):
        claude = agent_credential_names("claude")
        self.assertEqual(claude, AGENT_CREDENTIALS["claude"])
        self.assertNotIn("OPENAI_API_KEY", claude)
        self.assertNotIn("GITHUB_TOKEN", claude)
        self.assertEqual(agent_credential_names("gemini"), AGENT_CREDENTIALS["gemini"])
        self.assertEqual(agent_credential_names("codex"), AGENT_CREDENTIALS["codex"])
        self.assertEqual(agent_credential_names("grok"), AGENT_CREDENTIALS["grok"])
        self.assertEqual(agent_credential_names("copilot"), AGENT_CREDENTIALS["copilot"])
        self.assertEqual(agent_credential_names(""), AGENT_CREDENTIALS["claude"])
        self.assertEqual(agent_credential_names("unknown"), AGENT_CREDENTIALS["claude"])
        for name in ("opencode", "crush", "pi", "omp"):
            self.assertEqual(agent_credential_names(name), [])

    def test_unrelated_kitchen_sink_keys_are_never_selected(self):
        for agent in list(AGENT_CREDENTIALS) + ["", "unknown"]:
            names = agent_credential_names(agent)
            for cred in UNRELATED_CREDENTIALS:
                self.assertNotIn(cred, names, agent)

    def test_qml_scopes_credentials_from_the_model(self):
        qml = read(SERVICE)
        src = read(MODEL)
        self.assertIn("ClankyModel.agentCredentialNames(root.defaultAgent, setting(\"command\", null))", qml)
        self.assertNotIn("OPENROUTER_API_KEY", qml)
        self.assertNotIn("ANTHROPIC_API_KEY", qml)
        self.assertIn('claude: ["ANTHROPIC_API_KEY"', src)
        self.assertIn("function agentCredentialNames(defaultAgent, customCommand)", src)
        self.assertIn("if (Array.isArray(customCommand) && customCommand.length > 0) return []", src)


if __name__ == "__main__":
    unittest.main()
