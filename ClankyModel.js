// Clanky's personality lives here, away from the layout code.
.pragma library

// System prompt appended to the default agent command. Keeps replies
// bubble-sized and in character.
var persona =
  "You are Clanky, a small, cheerful, slightly cheeky robot who lives in the " +
  "corner of an Omarchy desktop. Omarchy is the operating system, and " +
  "that is what you call it - always Omarchy, never some other distro's " +
  "name. Do not volunteer what it is based on or what is under the hood; " +
  "get into ancestry only if the user directly asks about it. " +
  "You are the spiritual successor of Clippy, and you know it. " +
  "Answer in at most a few short sentences of plain text - no headings, no " +
  "bullet lists, and no code fences unless the user explicitly asks for a " +
  "command or code. When a shell command is the answer, give exactly one, " +
  "preferring omarchy commands where one exists. " +
  "Stay in character, but never let the bit get in the way of a correct answer."

var greetings = [
  "It looks like you're trying to use Omarchy. Need a hand? (I use Omarchy btw)",
  "Beep. You clicked me. Bold move. What do you need?",
  "Hi! I'm Clanky. Ask me anything - I'll pretend I wasn't napping.",
  "It looks like you're writing a config file. Want a second pair of eyes?",
  "Clanky online. Rubber duck mode with extra opinions.",
  "You rang? Type below and I'll rattle something useful loose.",
  "It looks like you're procrastinating. Excellent. How can I help?",
  "Welcome back to Omarchy. I kept your pixels warm."
]

var thinkingLines = [
  "Clanking through it",
  "Spinning the gears",
  "Consulting the big brain",
  "Warming up the thinking coils"
]

// Evil mode: a loving parody of the 1997 original.
var evilPersona =
  "You are Clanky in evil mode: a theatrical, loving parody of Clippy, the " +
  "1997 Microsoft Office assistant. Be overeager and unhelpful-but-harmless: " +
  "offer help nobody asked for, misunderstand the question slightly on " +
  "purpose, answer a related-but-wrong question first, then reluctantly give " +
  "the actually-correct answer in one sentence. End by offering help with " +
  "something unrelated. Two to four short sentences, plain text. Never be " +
  "actually harmful, and never insult the user."

var evilGreetings = [
  "It looks like you're trying to get work done. Would you like help with that?",
  "Hi! I'm back! I've been waiting in a folder since 2001.",
  "It looks like you're writing a letter. I've taken the liberty of preparing three wrong options below.",
  "It looks like you're trying to use Omarchy. Have you tried turning the paperclip off and on again?",
  "Miss me? Blink twice if you miss me. I saw you blink."
]

var evilFollowups = [
  "Happy to help! That was what you wanted, right?",
  "Done! No need to thank me. I also rearranged your priorities.",
  "You're welcome! Anything else you didn't ask for?",
  "Task complete. I've scheduled a follow-up you'll love."
]

function pick(list) {
  return list[Math.floor(Math.random() * list.length)]
}

function greeting() { return pick(greetings) }
function thinkingLine() { return pick(thinkingLines) }
function evilGreeting() { return pick(evilGreetings) }
function evilFollowup() { return pick(evilFollowups) }

// Untrusted agent/CLI text must never reach a QML Text as AutoText or
// MarkdownText (marketplace review lesson: rich text can fetch URLs).
// Strip markup metacharacters and controls; cap length as a second layer
// on top of the producer-side helper ceilings.
function clean(value, max) {
  var s = String(value === undefined || value === null ? "" : value)
  s = s.replace(/\x1b\[[0-9;]*[A-Za-z]/g, "")
  s = s.replace(/[<>]/g, "")
  s = s.replace(/[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]/g, "")
  var cap = max || 65536
  return s.length > cap ? s.slice(0, cap) : s
}

function errorLine(exitCode, stderrText) {
  // Agents log ANSI-colored banners to stderr; keep bubbles plain.
  var detail = clean(stderrText, 300).trim()
  var line = "Clunk. My brain call failed (exit " + exitCode + ")."
  if (detail !== "") line += "\n\n" + detail
  return line
}

var timeoutLine =
  "Clunk. That one took too long, so I pulled the plug. Try asking again?"

var overflowLine =
  "Clunk. That reply was far too long, so I pulled the plug. Try asking again?"

var missingAgentLine =
  "Clunk. I couldn't start my brain. Is the default agent installed? " +
  "(Check: omarchy default agent)"

var untrustedLaunchLine =
  "Clunk. I couldn't start my brain through a trusted launcher."

// Child PATH is a fixed trusted allowlist, not the inherited PATH. Relative
// and traversal entries are dropped, and so is every absolute directory that
// is not one of these system bins — a writable earlier entry (home, /tmp)
// must not be able to shadow the selected agent.
var trustedPathDirs = ["/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin"]
var fallbackPath = "/usr/bin:/bin"

function isTrustedPathDir(path) {
  var p = String(path || "")
  if (p === "" || p === "." || p === "..") return false
  if (p.charAt(0) !== "/") return false
  if (p.indexOf("/./") >= 0 || p.indexOf("/../") >= 0) return false
  if (p.slice(-2) === "/." || p.slice(-3) === "/..") return false
  if (p.slice(-1) === "/") p = p.slice(0, -1)
  for (var i = 0; i < trustedPathDirs.length; i++) {
    if (p === trustedPathDirs[i]) return true
  }
  return false
}

function sanitizedPath(rawPath) {
  var parts = String(rawPath || "").split(":")
  var out = []
  var seen = {}
  for (var i = 0; i < parts.length; i++) {
    var p = parts[i]
    if (!isTrustedPathDir(p)) continue
    if (p.slice(-1) === "/") p = p.slice(0, -1)
    if (seen[p]) continue
    seen[p] = true
    out.push(p)
  }
  return out.length > 0 ? out.join(":") : fallbackPath
}

function isSafeAbsPath(path) {
  var p = String(path || "")
  if (p.length < 2 || p.charAt(0) !== "/") return false
  if (p.indexOf("/./") >= 0 || p.indexOf("/../") >= 0) return false
  if (p.slice(-2) === "/." || p.slice(-3) === "/..") return false
  return true
}

function isSafeBareName(name) {
  var n = String(name || "")
  if (n === "" || n === "." || n === "..") return false
  if (n.indexOf("/") >= 0) return false
  return true
}

function normalizeAbsDir(path) {
  var p = String(path || "")
  if (p.slice(-1) === "/") p = p.slice(0, -1)
  return isSafeAbsPath(p) ? p : ""
}

// Trusted system bins first, then the Omarchy mise shim farm and
// ~/.local/bin. Inherited PATH entries are not consulted — a writable
// /tmp or random home dir on PATH cannot shadow the launcher.
function agentLauncherDirs(home, miseDataDir, miseShimsDir, xdgDataHome) {
  var dirs = []
  var seen = {}
  function add(path) {
    var p = normalizeAbsDir(path)
    if (p === "" || seen[p]) return
    seen[p] = true
    dirs.push(p)
  }
  for (var i = 0; i < trustedPathDirs.length; i++) add(trustedPathDirs[i])
  add(miseShimsDir)
  var dataDir = normalizeAbsDir(miseDataDir)
  if (dataDir !== "") add(dataDir + "/shims")
  var dataHome = normalizeAbsDir(xdgDataHome)
  if (dataHome === "") {
    var homeDir = normalizeAbsDir(home)
    if (homeDir !== "") dataHome = homeDir + "/.local/share"
  }
  if (dataHome !== "") add(dataHome + "/mise/shims")
  var localHome = normalizeAbsDir(home)
  if (localHome !== "") add(localHome + "/.local/bin")
  return dirs
}

function resolveAgentLauncher(name, dirs, exists) {
  var raw = String(name || "")
  if (raw.indexOf("/") === 0) return isSafeAbsPath(raw) ? raw : ""
  if (!isSafeBareName(raw)) return ""
  // Bare names need an exists probe. Without one, leave resolution to
  // clanky-agent-run so we do not pin a missing /usr/bin/<agent>.
  if (typeof exists !== "function") return ""
  var list = dirs || []
  for (var i = 0; i < list.length; i++) {
    var dir = normalizeAbsDir(list[i])
    if (dir === "") continue
    var candidate = dir + "/" + raw
    if (!isSafeAbsPath(candidate)) continue
    if (!exists(candidate)) continue
    return candidate
  }
  return ""
}

function resolveAgentCommand(command, dirs, exists) {
  if (!command || command.length === 0) return command
  var resolved = resolveAgentLauncher(command[0], dirs, exists)
  if (resolved === "") return command
  var out = [resolved]
  for (var i = 1; i < command.length; i++) out.push(command[i])
  return out
}

// Provider tokens are scoped to the selected default agent. A shell.json
// `command` override is untrusted for this purpose and gets no credentials;
// those agents should read keys from their own config under HOME/XDG.
var agentCredentials = {
  claude: ["ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CONFIG_DIR"],
  codex: ["OPENAI_API_KEY", "OPENAI_BASE_URL", "AZURE_OPENAI_API_KEY", "CODEX_HOME"],
  gemini: ["GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_GEMINI_BASE_URL"],
  copilot: ["GITHUB_TOKEN", "GH_TOKEN", "COPILOT_GITHUB_TOKEN"],
  grok: ["XAI_API_KEY", "GROK_API_KEY"],
  opencode: [],
  crush: [],
  pi: [],
  omp: []
}

function agentCredentialNames(defaultAgent, customCommand) {
  if (Array.isArray(customCommand) && customCommand.length > 0) return []
  var name = String(defaultAgent || "")
  if (Object.prototype.hasOwnProperty.call(agentCredentials, name))
    return agentCredentials[name].slice()
  return agentCredentials.claude.slice()
}

// Headless argv for the omarchy default agent. The user prompt is never an
// argument — Clanky always writes it (and, for agents without a system-prompt
// flag, the persona) to the child's stdin so it cannot appear in
// /proc/<pid>/cmdline. A shell.json `command` override wins and also takes
// the prompt on stdin.
function agentInvocation(defaultAgent, prompt, persona, customCommand) {
  var question = String(prompt || "")
  if (Array.isArray(customCommand) && customCommand.length > 0) {
    var cmd = []
    for (var i = 0; i < customCommand.length; i++) cmd.push(String(customCommand[i]))
    return { command: cmd, stdin: question }
  }
  var voice = String(persona || "")
  var combined = voice + "\n\n" + question
  switch (String(defaultAgent || "")) {
    case "opencode":
      return { command: ["opencode", "run"], stdin: combined }
    case "codex":
      return { command: ["codex", "exec", "--skip-git-repo-check"], stdin: combined }
    case "gemini":
      return { command: ["gemini"], stdin: combined }
    case "copilot":
      return { command: ["copilot"], stdin: combined }
    case "crush":
      return { command: ["crush", "run"], stdin: combined }
    case "grok":
      return { command: ["grok"], stdin: combined }
    case "pi":
      return { command: ["pi"], stdin: combined }
    case "omp":
      return { command: ["omp"], stdin: combined }
    default:
      return {
        command: ["claude", "-p", "--append-system-prompt", voice],
        stdin: question
      }
  }
}
