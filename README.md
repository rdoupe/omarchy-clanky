# Clanky

A Clippy-style desktop clanker for [Omarchy](https://omarchy.org/).

Clanky is a small robot who lives in the bottom-right corner of your screen,
bobbing gently and blinking at you. Click him and a speech bubble opens with a
text field. Whatever you type is forwarded to an AI agent CLI (`claude -p` by
default), and the reply lands in his speech bubble — short, cheerful, and a
little cheeky.

> "It looks like you're trying to use Arch. Need a hand? (btw)"

## How it works

Clanky is an `omarchy-shell` plugin of kind `service`: a single QML file that
mounts a Wayland layer-shell window inside the long-running Quickshell process.
No Electron, no extra daemon. When the bubble is closed, only the robot's few
thousand square pixels accept input — clicks everywhere else pass through to
your windows.

Clanky's brain is **the omarchy-wide default coding agent** — the one you
picked in the installer/firstboot, stored in
`~/.config/omarchy/defaults/agent` and changed with
`omarchy default agent <name>`. He watches that file, so switching the
default re-wires him live. All nine Omarchy agents are mapped to their
headless one-shot form (claude `-p` with a persona system prompt and the
question on stdin; opencode `run`, codex `exec`, gemini/copilot/grok `-p`,
crush `run`, pi/omp positional — those get the persona prepended to the
prompt). Unset or unknown falls back to claude, and a `command` override in
`shell.json` always wins. Each ask is a fresh agent run (no conversation
memory yet).

## Install

```bash
ln -s "$(pwd)" ~/.config/omarchy/plugins/io.github.rdoupe.clanky
```

Then enable the service by adding it to the top-level `plugins` array in
`~/.config/omarchy/shell.json`:

```json
"plugins": [
  { "id": "io.github.rdoupe.clanky" }
]
```

The shell hot-reloads; if Clanky doesn't appear, run
`omarchy-shell shell rescanPlugins`.

## Theme-aware, head to toe

Clanky dresses in the active Omarchy theme. His chassis wears the colors
that dominate the desktop — accent-colored head, surface-background body
with an accent border, muted feet — so he looks shipped with the theme,
while the side palette shows up as small pops: magenta cheeks,
red/yellow/green chest lights, and an orange paperclip antenna (in
memoriam). He reads the full palette from the theme's `colors.toml` and
re-dresses instantly on `omarchy theme set`, no restart; sparse themes fall
back to the shell's role colors. While he's thinking, the chest lights
chase and the paperclip pulses.

## Moving him

Drag the robot anywhere on screen; a plain click still toggles the bubble.
The position is clamped to the screen and persisted into `shell.json`
through the shell's own config writer (the same path `omarchy bar move`
uses), so it survives restarts. Scripts can do the same:

```bash
omarchy-shell clanky move 600 400   # margins from the bottom-right corner
```

## Configuration

All settings live on the `plugins[]` entry in `shell.json`:

| Key       | Default                          | Meaning                                    |
|-----------|----------------------------------|--------------------------------------------|
| `command` | *(omarchy default agent)*        | Override the agent entirely; the prompt is written to its stdin, stdout is the reply |
| `marginX` | `16`                             | Distance from the right screen edge (px); updated automatically by dragging |
| `marginY` | `16`                             | Distance from the bottom screen edge (px); updated automatically by dragging |

Example — use OpenCode instead:

```json
{ "id": "io.github.rdoupe.clanky", "command": ["opencode", "run"] }
```

## IPC

```bash
omarchy-shell clanky toggle          # open/close the bubble
omarchy-shell clanky ask "why is my wifi sad"
omarchy-shell clanky state           # closed | open | thinking
omarchy-shell clanky move 16 16      # reposition (persisted)
```

Bind him to a key in `~/.config/hypr/bindings.lua`:

```lua
o.bind("SUPER + SHIFT + C", "Clanky", "omarchy-shell clanky toggle")
```

He never grabs keyboard focus while closed and takes it only on-demand while
the bubble is open, so Hyprland keybindings keep working either way.

## Prior art

Nobody had made a Clippy for Omarchy, so this is it. Kindred spirits:
[felixrieseberg/clippy](https://github.com/felixrieseberg/clippy) (Electron +
local llama.cpp), [NekoAI](https://nekoai.dev/), and
[qs-vpets](https://github.com/jesperls/qs-vpets) (Quickshell desktop pets,
no LLM). The name is a nod to
[DHH's clankers](https://world.hey.com/dhh/clankers-with-claws-9f86fa71) —
"clanker" itself is already thoroughly squatted, so: Clanky.

## License

MIT
