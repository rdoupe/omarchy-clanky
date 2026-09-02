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

Each question pipes your text to the agent command's stdin and reads stdout as
the reply. The default command is:

```
claude -p --append-system-prompt "<Clanky's persona>"
```

so replies stay bubble-sized and in character. Each ask is a fresh agent run
(no conversation memory yet).

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

## Configuration

All settings live on the `plugins[]` entry in `shell.json`:

| Key       | Default                          | Meaning                                    |
|-----------|----------------------------------|--------------------------------------------|
| `command` | `["claude", "-p", …persona]`     | Agent command; the prompt is written to its stdin, stdout is the reply |
| `marginX` | `16`                             | Distance from the right screen edge (px)   |
| `marginY` | `16`                             | Distance from the bottom screen edge (px)  |

Example — use OpenCode instead:

```json
{ "id": "io.github.rdoupe.clanky", "command": ["opencode", "run"] }
```

## IPC

```bash
omarchy-shell clanky toggle          # open/close the bubble
omarchy-shell clanky ask "why is my wifi sad"
omarchy-shell clanky state           # closed | open | thinking
```

Bind it to a key in `~/.config/hypr/bindings.lua` if clicking a robot feels
like too much mousing.

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
