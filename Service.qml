import QtQuick
import QtQuick.Shapes
import Quickshell
import Quickshell.Io
import Quickshell.Wayland
import qs.Commons
import qs.Ui
import "ClankyModel.js" as ClankyModel

// Clanky: a Clippy-style desktop clanker. A small robot sits in the
// bottom-right corner on the Top layer. Clicking him opens a speech bubble
// with a text field; the text is piped to an AI agent CLI (claude -p by
// default) and the reply is rendered in the bubble.
//
// shell.json settings (top-level plugins[] entry):
//   { "id": "io.github.rdoupe.clanky",
//     "command": ["claude", "-p"],     // optional agent override, reads stdin
//     "marginX": 16, "marginY": 16 }   // optional corner offsets
Item {
  id: root

  property var shell: null
  property var manifest: null
  property string omarchyPath: Quickshell.env("OMARCHY_PATH")

  readonly property string pluginId: "io.github.rdoupe.clanky"

  // Full theme palette, read straight from the current theme's colors.toml.
  // The shell's Color singleton keeps only five roles (foreground,
  // background, accent, urgent, muted); Clanky wants the hues too, so he
  // wears red, yellow, green, cyan, blue, magenta, orange, and brown from
  // whatever theme is active. Sparse themes fall back to the shell roles.
  property var themeColors: ({})

  function tc(name, fallback) {
    var v = themeColors[name]
    return (typeof v === "string" && v.length > 0) ? v : fallback
  }

  // Chassis wears the colors that dominate the theme on screen — the accent
  // and the surface backgrounds — so Clanky looks like he was shipped with
  // the theme, not painted from its side palette. The hues stay as small
  // pops: cheeks, chest lights, antenna tip.
  // The head is derived from the body fill rather than painted raw accent —
  // a full-saturation accent slab clashes on some themes. Washing ~45%
  // accent over the body color keeps the two panels in the same material
  // family with the accent as a tint.
  readonly property color cBody: tc("lighter_background", Util.alpha(Color.foreground, 0.15))
  readonly property color cHead: Qt.tint(cBody, Util.alpha(Color.accent, 0.45))
  readonly property color cBodyBorder: Util.alpha(Color.accent, 0.6)
  readonly property color cOutline: tc("darker_background", Util.alpha(Color.background, 0.8))
  readonly property color cEye: tc("bright_foreground", Color.foreground)
  readonly property color cPupil: tc("dark_background", Color.background)
  readonly property color cCheek: tc("bright_magenta", tc("magenta", Color.accent))
  readonly property color cStem: tc("muted", Color.muted)
  readonly property color cTip: tc("orange", tc("yellow", Color.accent))
  readonly property color cLightRed: tc("red", Color.urgent)
  readonly property color cLightYellow: tc("yellow", Color.accent)
  readonly property color cLightGreen: tc("green", Color.accent)
  readonly property color cFeet: tc("muted", tc("dark_foreground", Color.muted))

  function loadThemeColors(raw) {
    var out = {}
    var lines = String(raw || "").split("\n")
    for (var i = 0; i < lines.length; i++) {
      var match = lines[i].match(/^\s*([A-Za-z0-9_-]+)\s*=\s*["']?(#[0-9A-Fa-f]{6,8})/)
      if (match) out[match[1]] = match[2]
    }
    themeColors = out
  }

  FileView {
    id: colorsFile
    path: Quickshell.env("HOME") + "/.local/state/omarchy/current/theme/colors.toml"
    watchChanges: true
    printErrors: false
    onLoaded: root.loadThemeColors(text())
    onFileChanged: reload()
    onLoadFailed: root.loadThemeColors("")
  }

  // Theme switches swap the current/theme symlink under the same path and
  // push new role colors into the Color singleton over IPC, which the file
  // watch may not see — so re-read the palette whenever the roles move.
  Connections {
    target: Color
    function onAccentChanged() { colorsFile.reload() }
    function onBackgroundChanged() { colorsFile.reload() }
    function onForegroundChanged() { colorsFile.reload() }
  }

  // Which body Clanky wears: "clanky" (the classic robot) or "tux" (a
  // robotic penguin with an animated Omarchy chest). Set on the shell.json
  // plugins[] entry as "skin".
  readonly property string skin: String(setting("skin", "clanky")).toLowerCase()

  // Evil mode: right-click the character. He morphs into a certain 1997
  // office assistant and starts offering the kind of help that assistant
  // was famous for. Session-only; right-click again to exorcise.
  property bool evil: false

  function toggleEvil() {
    evil = !evil
    reply = ""
    errorText = ""
    greeting = evil ? ClankyModel.evilGreeting() : ClankyModel.greeting()
    if (evil) opened = true
  }

  // Right-click context menu, and quit/summon. Quit hides Clanky for this
  // shell session; the Clanky launcher entry (or `omarchy-shell clanky
  // summon`) brings him back.
  property bool menuOpen: false
  property bool quitHidden: false

  function quitClanky() {
    menuOpen = false
    opened = false
    quitHidden = true
  }

  function summon() {
    quitHidden = false
    open()
  }

  property bool opened: false
  property bool thinking: false
  property string reply: ""
  property string errorText: ""
  property string greeting: ""
  property string pendingPrompt: ""
  property string lastPrompt: ""
  property int thinkingDots: 0
  property string thinkingLine: ""

  readonly property string bubbleText: {
    if (errorText !== "") return errorText
    if (thinking) return thinkingLine + Array(thinkingDots + 1).join(".")
    if (reply !== "") return reply
    return greeting
  }
  readonly property bool bubbleMarkdown: !thinking && errorText === "" && reply !== ""

  function setting(key, fallback) {
    var cfg = shell ? shell.shellConfig : null
    var list = cfg && Array.isArray(cfg.plugins) ? cfg.plugins : []
    for (var i = 0; i < list.length; i++) {
      var entry = list[i]
      if (entry && String(entry.id || "") === root.pluginId && entry[key] !== undefined)
        return entry[key]
    }
    return fallback
  }

  // The omarchy-wide default coding agent, as picked in the installer /
  // firstboot or via `omarchy default agent <name>`. Watched, so switching
  // the default re-wires Clanky's brain live.
  property string defaultAgent: ""
  FileView {
    id: agentFile
    path: Quickshell.env("HOME") + "/.config/omarchy/defaults/agent"
    watchChanges: true
    printErrors: false
    onLoaded: root.defaultAgent = String(text() || "").trim()
    onFileChanged: reload()
    onLoadFailed: root.defaultAgent = ""
  }

  // One-shot invocation for the active agent. A `command` override in
  // shell.json always wins (prompt on stdin). Otherwise the omarchy default
  // agent is used with its own headless syntax: claude takes the persona as
  // a system prompt and reads stdin; the others get persona + prompt as one
  // argument. Unset or unknown falls back to claude.
  function agentInvocation(prompt) {
    var custom = setting("command", null)
    if (Array.isArray(custom) && custom.length > 0)
      return { command: custom.map(String), stdin: prompt }
    var persona = root.evil ? ClankyModel.evilPersona : ClankyModel.persona
    var combined = persona + "\n\n" + prompt
    switch (root.defaultAgent) {
    case "opencode": return { command: ["opencode", "run", combined], stdin: null }
    case "codex": return { command: ["codex", "exec", "--skip-git-repo-check", combined], stdin: null }
    case "gemini": return { command: ["gemini", "-p", combined], stdin: null }
    case "copilot": return { command: ["copilot", "-p", combined], stdin: null }
    case "crush": return { command: ["crush", "run", combined], stdin: null }
    case "grok": return { command: ["grok", "-p", combined], stdin: null }
    case "pi": return { command: ["pi", combined], stdin: null }
    case "omp": return { command: ["omp", combined], stdin: null }
    default:
      return { command: ["claude", "-p", "--append-system-prompt", persona], stdin: prompt }
    }
  }

  function open() {
    if (opened) return
    if (reply === "" && errorText === "")
      greeting = evil ? ClankyModel.evilGreeting() : ClankyModel.greeting()
    opened = true
  }

  function close() { opened = false }
  function toggle() { opened ? close() : open() }

  function ask(text) {
    var prompt = String(text || "").trim()
    if (prompt === "" || agentProc.running) return
    open()
    thinking = true
    thinkingLine = ClankyModel.thinkingLine()
    thinkingDots = 0
    errorText = ""
    reply = ""
    lastPrompt = prompt
    var inv = agentInvocation(prompt)
    pendingPrompt = inv.stdin === null ? "" : inv.stdin
    agentProc.command = inv.command
    // Always open stdin and close it right after start: agents that take the
    // prompt as an argument still wait for EOF on a dangling pipe (opencode
    // does), and the close is what delivers it.
    agentProc.stdinEnabled = true
    agentProc.running = true
    timeoutTimer.restart()
  }

  function cancelAsk(message) {
    timeoutTimer.stop()
    if (agentProc.running) agentProc.running = false
    thinking = false
    if (message !== undefined) errorText = message
  }

  Process {
    id: agentProc
    stdout: StdioCollector { id: agentOut; waitForEnd: true }
    stderr: StdioCollector { id: agentErr; waitForEnd: true }
    onStarted: {
      if (root.pendingPrompt !== "") agentProc.write(root.pendingPrompt + "\n")
      agentProc.stdinEnabled = false
    }
    onExited: (exitCode, exitStatus) => {
      timeoutTimer.stop()
      if (!root.thinking) return // cancelled or timed out; message already set
      root.thinking = false
      var out = String(agentOut.text || "").trim()
      if (exitCode === 0 && out !== "") root.reply = out
      else if (exitCode === 0) root.reply = "…that's all I've got. (Empty reply.)"
      else root.errorText = ClankyModel.errorLine(exitCode, agentErr.text)
    }
  }

  Timer {
    id: timeoutTimer
    interval: 180000
    onTriggered: root.cancelAsk(ClankyModel.timeoutLine)
  }

  Timer {
    interval: 400
    running: root.thinking
    repeat: true
    onTriggered: root.thinkingDots = (root.thinkingDots + 1) % 4
  }

  IpcHandler {
    target: "clanky"
    function open(): string { root.open(); return "ok" }
    function close(): string { root.close(); return "ok" }
    function toggle(): string { root.toggle(); return "ok" }
    function ask(prompt: string): string { root.ask(prompt); return "ok" }
    function state(): string {
      return root.thinking ? "thinking" : (root.opened ? "open" : "closed")
    }
    function evil(): string {
      root.toggleEvil()
      return root.evil ? "evil" : "good"
    }
    function quit(): string { root.quitClanky(); return "ok" }
    function summon(): string { root.summon(); return "ok" }
    // Margins from the bottom-right corner; clamped to the screen and
    // persisted to shell.json like a mouse drag.
    function move(marginX: string, marginY: string): string {
      panel.posRight = parseInt(marginX) || 0
      panel.posBottom = parseInt(marginY) || 0
      panel.clampPosition()
      root.persistPosition()
      return "ok"
    }
  }

  // -------------------------------------------------------------- skins
  //
  // The paperclip: Clanky's antenna in every skin, and in evil mode the
  // whole body. Outer left side, loop over the top, down the right, U-turn
  // at the bottom, up the middle. Pulses while thinking.
  component PaperclipClip: Shape {
    id: clip
    property real s: Style.space(1)
    property color stroke: root.cTip
    property real strokeW: 1.8
    width: 10 * clip.s
    height: 20 * clip.s
    preferredRendererType: Shape.CurveRenderer

    ShapePath {
      strokeWidth: clip.strokeW * clip.s
      strokeColor: clip.stroke
      fillColor: "transparent"
      capStyle: ShapePath.RoundCap

      startX: 1 * clip.s; startY: 15 * clip.s
      PathLine { x: 1 * clip.s; y: 4 * clip.s }
      PathArc {
        x: 7 * clip.s; y: 4 * clip.s
        radiusX: 3 * clip.s; radiusY: 3 * clip.s
        direction: PathArc.Clockwise
      }
      PathLine { x: 7 * clip.s; y: 17 * clip.s }
      PathArc {
        x: 4 * clip.s; y: 17 * clip.s
        radiusX: 1.5 * clip.s; radiusY: 1.5 * clip.s
        direction: PathArc.Clockwise
      }
      PathLine { x: 4 * clip.s; y: 7 * clip.s }
    }

    SequentialAnimation on opacity {
      running: root.thinking
      loops: Animation.Infinite
      NumberAnimation { from: 1; to: 0.25; duration: 350 }
      NumberAnimation { from: 0.25; to: 1; duration: 350 }
    }
    onOpacityChanged: if (!root.thinking && opacity !== 1) opacity = 1
  }

  // One blinking robot eye; blink state comes from charArea.
  component RobotEye: Rectangle {
    id: eye
    property real w: Style.space(14)
    width: eye.w
    height: charArea.eyesClosed ? Style.space(3) : eye.w * 1.15
    radius: width / 2
    color: root.cEye
    Rectangle {
      visible: !charArea.eyesClosed
      width: eye.w * 0.42
      height: width
      radius: width / 2
      anchors.centerIn: parent
      anchors.verticalCenterOffset: root.opened ? -Style.space(2) : 0
      color: root.cPupil
    }
  }

  // The classic Clanky robot.
  Component {
    id: clankySkin
    Item {
      PaperclipClip {
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.bottom: head.top
        anchors.bottomMargin: -Style.space(2)
      }

      Rectangle {
        id: head
        width: Style.space(60)
        height: Style.space(38)
        radius: Style.space(12)
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.bottom: body.top
        anchors.bottomMargin: Style.space(3)
        color: root.cHead
        border.width: 1
        border.color: root.cOutline

        Row {
          anchors.centerIn: parent
          height: Style.space(16)
          spacing: Style.space(10)
          Repeater {
            model: 2
            RobotEye { anchors.verticalCenter: parent.verticalCenter }
          }
        }

        // Cheeks
        Repeater {
          model: 2
          Rectangle {
            width: Style.space(7)
            height: width
            radius: width / 2
            anchors.bottom: parent.bottom
            anchors.bottomMargin: Style.space(6)
            x: index === 0 ? Style.space(5) : head.width - width - Style.space(5)
            color: Util.alpha(root.cCheek, 0.75)
          }
        }
      }

      Rectangle {
        id: body
        width: Style.space(68)
        height: Style.space(40)
        radius: Style.space(14)
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.bottom: feet.top
        color: root.cBody
        border.width: 1
        border.color: root.cBodyBorder

        // Chest meter: three little EQ bars in the theme's hues (circles in
        // a row read as macOS traffic lights). Uneven and dim at rest,
        // bouncing while Clanky is thinking.
        property int chaseIndex: 0
        Timer {
          running: root.thinking
          repeat: true
          interval: 260
          onTriggered: body.chaseIndex = (body.chaseIndex + 1) % 3
        }
        Row {
          anchors.centerIn: parent
          height: Style.space(16)
          spacing: Style.space(6)
          Repeater {
            model: [root.cLightRed, root.cLightYellow, root.cLightGreen]
            Rectangle {
              readonly property real restHeight:
                [Style.space(8), Style.space(13), Style.space(6)][index]
              width: Style.space(5)
              height: root.thinking
                ? (body.chaseIndex === index ? Style.space(15) : Style.space(6))
                : restHeight
              anchors.bottom: parent.bottom
              radius: width / 2
              color: Util.alpha(modelData, root.thinking ? 0.95 : 0.7)
              Behavior on height {
                NumberAnimation { duration: 200; easing.type: Easing.OutCubic }
              }
            }
          }
        }
      }

      Row {
        id: feet
        spacing: Style.space(14)
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.bottom: parent.bottom
        Repeater {
          model: 2
          Rectangle {
            width: Style.space(16)
            height: Style.space(8)
            radius: Style.space(4)
            color: root.cFeet
          }
        }
      }
    }
  }

  // Robotic Tux: an egg of dark chassis metal, pale belly plate with the
  // Omarchy mark breathing on it, orange beak and feet, stubby wings.
  Component {
    id: tuxSkin
    Item {
      PaperclipClip {
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.bottom: tuxBody.top
        anchors.bottomMargin: -Style.space(3)
      }

      Rectangle {
        id: tuxBody
        width: Style.space(64)
        height: Style.space(82)
        radius: Style.space(30)
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.bottom: tuxFeet.top
        anchors.bottomMargin: -Style.space(4)
        color: root.cOutline
        border.width: 1
        border.color: root.cBodyBorder

        // Wings
        Repeater {
          model: 2
          Rectangle {
            width: Style.space(12)
            height: Style.space(34)
            radius: Style.space(6)
            y: Style.space(30)
            x: index === 0 ? -Style.space(5) : tuxBody.width - width + Style.space(5)
            rotation: index === 0 ? 14 : -14
            color: root.cOutline
            border.width: 1
            border.color: root.cBodyBorder
          }
        }

        // Belly plate with the animated Omarchy mark
        Rectangle {
          width: Style.space(44)
          height: Style.space(44)
          radius: Style.space(20)
          anchors.horizontalCenter: parent.horizontalCenter
          anchors.bottom: parent.bottom
          anchors.bottomMargin: Style.space(5)
          color: root.cEye

          Text {
            anchors.centerIn: parent
            text: ""
            font.family: "omarchy"
            font.pixelSize: Style.space(22)
            color: Color.accent

            SequentialAnimation on scale {
              running: panel.visible
              loops: Animation.Infinite
              NumberAnimation { from: 1; to: 1.16; duration: 1300; easing.type: Easing.InOutSine }
              NumberAnimation { from: 1.16; to: 1; duration: 1300; easing.type: Easing.InOutSine }
            }
            RotationAnimation on rotation {
              running: root.thinking
              loops: Animation.Infinite
              from: 0; to: 360
              duration: 1400
            }
            onRotationChanged: if (!root.thinking && rotation !== 0) rotation = 0
          }
        }

        // Eyes
        Row {
          anchors.horizontalCenter: parent.horizontalCenter
          y: Style.space(9)
          height: Style.space(15)
          spacing: Style.space(7)
          Repeater {
            model: 2
            RobotEye {
              w: Style.space(13)
              anchors.verticalCenter: parent.verticalCenter
            }
          }
        }

        // Beak
        Rectangle {
          width: Style.space(16)
          height: Style.space(9)
          radius: Style.space(4)
          anchors.horizontalCenter: parent.horizontalCenter
          y: Style.space(24)
          color: root.cTip
          border.width: 1
          border.color: root.cOutline
        }
      }

      Row {
        id: tuxFeet
        spacing: Style.space(10)
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.bottom: parent.bottom
        Repeater {
          model: 2
          Rectangle {
            width: Style.space(18)
            height: Style.space(8)
            radius: Style.space(4)
            color: root.cTip
            border.width: 1
            border.color: root.cOutline
          }
        }
      }
    }
  }

  // Evil mode: the ghost of 1997. A big wobbling paperclip with googly
  // eyes and eyebrows.
  Component {
    id: clippySkin
    Item {
      id: clippyRoot

      transform: Rotation {
        id: clippyTilt
        origin.x: clippyRoot.width / 2
        origin.y: clippyRoot.height
        angle: 0
      }
      SequentialAnimation {
        running: panel.visible && root.evil
        loops: Animation.Infinite
        NumberAnimation { target: clippyTilt; property: "angle"; from: -5; to: 5; duration: 900; easing.type: Easing.InOutSine }
        NumberAnimation { target: clippyTilt; property: "angle"; from: 5; to: -5; duration: 900; easing.type: Easing.InOutSine }
      }

      PaperclipClip {
        id: bigClip
        s: Style.space(4.2)
        strokeW: 1.3
        stroke: root.tc("light_foreground", Color.foreground)
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.bottom: parent.bottom
      }

      // Eyebrows
      Repeater {
        model: 2
        Rectangle {
          width: Style.space(12)
          height: Style.space(3)
          radius: height / 2
          x: clippyRoot.width / 2 + (index === 0 ? -Style.space(16) : Style.space(4))
          y: Style.space(18)
          rotation: index === 0 ? -18 : 18
          color: root.tc("light_foreground", Color.foreground)
        }
      }

      // Googly eyes on the top loop
      Row {
        anchors.horizontalCenter: parent.horizontalCenter
        y: Style.space(24)
        height: Style.space(18)
        spacing: Style.space(5)
        Repeater {
          model: 2
          RobotEye {
            w: Style.space(15)
            anchors.verticalCenter: parent.verticalCenter
          }
        }
      }
    }
  }

  // Persist the dragged position the omarchy-native way: write it onto our
  // plugins[] entry in shell.json through the shell's own mutator (the same
  // path `omarchy bar move` uses), merging with whatever other settings the
  // entry already carries.
  function persistPosition() {
    if (!shell || typeof shell.updateEntryInline !== "function") return
    var settings = {}
    var cfg = shell.shellConfig
    var list = cfg && Array.isArray(cfg.plugins) ? cfg.plugins : []
    for (var i = 0; i < list.length; i++) {
      var entry = list[i]
      if (entry && String(entry.id || "") === pluginId)
        for (var k in entry) if (k !== "id") settings[k] = entry[k]
    }
    settings.marginX = panel.posRight
    settings.marginY = panel.posBottom
    shell.updateEntryInline(pluginId, settings)
  }

  PanelWindow {
    id: panel
    visible: !root.quitHidden
    anchors { bottom: true; right: true }

    // Corner offsets. Initialized from shell.json; dragging the robot
    // reassigns them (breaking the binding) and persists on release.
    property int posRight: Math.max(0, parseInt(root.setting("marginX", 16)) || 16)
    property int posBottom: Math.max(0, parseInt(root.setting("marginY", 16)) || 16)

    function clampPosition() {
      var maxRight = screen ? Math.max(0, screen.width - width) : 100000
      var maxBottom = screen ? Math.max(0, screen.height - height) : 100000
      posRight = Math.min(Math.max(0, posRight), maxRight)
      posBottom = Math.min(Math.max(0, posBottom), maxBottom)
    }

    margins {
      right: panel.posRight
      bottom: panel.posBottom
    }
    implicitWidth: Style.space(400)
    implicitHeight: Style.space(500)
    color: "transparent"
    WlrLayershell.namespace: "omarchy-clanky"
    WlrLayershell.layer: WlrLayer.Top
    WlrLayershell.keyboardFocus: root.opened
      ? WlrKeyboardFocus.OnDemand : WlrKeyboardFocus.None
    exclusionMode: ExclusionMode.Ignore
    // Closed: only the robot is clickable, the rest of the surface lets
    // clicks through to whatever is underneath. Open: the whole surface is
    // ours so a click outside the bubble can close it.
    mask: Region { item: (root.opened || root.menuOpen) ? windowContent : charArea }

    Item {
      id: windowContent
      anchors.fill: parent

      // Click-outside-to-close backstop; sits under the bubble and robot.
      MouseArea {
        anchors.fill: parent
        visible: root.opened || root.menuOpen
        onClicked: {
          root.close()
          root.menuOpen = false
        }
      }

      // ---------------------------------------------------------- bubble
      Rectangle {
        id: bubble
        visible: root.opened
        anchors.right: parent.right
        anchors.bottom: charArea.top
        anchors.bottomMargin: Style.space(14)
        width: parent.width
        height: bubbleColumn.implicitHeight + Style.space(28)
        radius: Style.cornerRadius
        color: Util.alpha(Color.background, 0.97)
        border.width: Math.max(1, Style.space(2))
        // Evil mode trades the popup border for legal-pad yellow.
        border.color: root.evil ? Util.alpha(root.cLightYellow, 0.9) : Color.popups.border

        // Tail pointing at Clanky: a rotated square poking out under the
        // bubble's bottom edge.
        Rectangle {
          width: Style.space(16)
          height: Style.space(16)
          rotation: 45
          anchors.horizontalCenter: parent.right
          anchors.horizontalCenterOffset: -Style.space(52)
          anchors.verticalCenter: parent.bottom
          anchors.verticalCenterOffset: -Style.space(4)
          color: bubble.color
          border.width: bubble.border.width
          border.color: bubble.border.color
        }
        // Masks the tail square's upper half so only the protruding tip
        // shows its border.
        Rectangle {
          anchors.fill: parent
          anchors.margins: bubble.border.width
          radius: bubble.radius - bubble.border.width
          color: bubble.color
        }

        Column {
          id: bubbleColumn
          anchors.fill: parent
          anchors.margins: Style.space(14)
          spacing: Style.space(10)

          Flickable {
            id: replyFlick
            width: parent.width
            height: Math.min(replyLabel.implicitHeight, Style.space(260))
            contentWidth: width
            contentHeight: replyLabel.implicitHeight
            clip: true
            boundsBehavior: Flickable.StopAtBounds

            Text {
              id: replyLabel
              width: replyFlick.width
              text: root.bubbleText
              textFormat: root.bubbleMarkdown ? Text.MarkdownText : Text.PlainText
              wrapMode: Text.Wrap
              font.family: Style.font.family
              font.pixelSize: Style.font.body
              color: root.errorText !== "" ? Color.urgent : Color.popups.text
            }
          }

          // Evil mode: the classic assistant option list, except every item
          // helpfully does the wrong thing.
          Column {
            visible: root.evil
            width: parent.width
            spacing: Style.space(2)
            Repeater {
              model: [
                { label: "Open a browser", cmd: ["omarchy", "launch", "terminal"] },
                { label: "Open a terminal", cmd: ["omarchy", "launch", "browser"] },
                { label: "Get help writing a letter", cmd: ["omarchy", "reminder", "1", "It looks like you are writing a letter. Would you like help?"] }
              ]
              Rectangle {
                width: parent.width
                height: Style.space(28)
                radius: Style.space(6)
                color: optionMouse.containsMouse
                  ? Util.alpha(root.cLightYellow, 0.18) : "transparent"
                Row {
                  anchors.verticalCenter: parent.verticalCenter
                  x: Style.space(8)
                  spacing: Style.space(8)
                  Rectangle {
                    width: Style.space(8)
                    height: width
                    radius: width / 2
                    anchors.verticalCenter: parent.verticalCenter
                    color: root.cLightYellow
                  }
                  Text {
                    anchors.verticalCenter: parent.verticalCenter
                    text: modelData.label
                    font.family: Style.font.family
                    font.pixelSize: Style.font.body
                    color: Color.popups.text
                  }
                }
                MouseArea {
                  id: optionMouse
                  anchors.fill: parent
                  hoverEnabled: true
                  cursorShape: Qt.PointingHandCursor
                  onClicked: {
                    Quickshell.execDetached(modelData.cmd)
                    root.reply = ""
                    root.errorText = ""
                    root.greeting = ClankyModel.evilFollowup()
                  }
                }
              }
            }
          }

          TextField {
            id: promptField
            width: parent.width
            enabled: !root.thinking
            placeholderText: root.thinking
              ? "Clanky is thinking…"
              : (root.evil ? "Type here. I know what you meant."
                           : "Ask Clanky, press Enter")
            onAccepted: {
              var value = text
              text = ""
              root.ask(value)
            }
            Keys.onEscapePressed: root.close()
          }
        }
      }

      // ------------------------------------------------------ context menu
      Rectangle {
        id: ctxMenu
        visible: root.menuOpen
        anchors.right: parent.right
        anchors.rightMargin: Style.space(8)
        anchors.bottom: charArea.top
        anchors.bottomMargin: Style.space(8)
        width: Style.space(190)
        height: menuColumn.implicitHeight + Style.space(12)
        radius: Style.cornerRadius
        color: Util.alpha(Color.background, 0.97)
        border.width: Math.max(1, Style.space(2))
        border.color: Color.popups.border

        Column {
          id: menuColumn
          anchors.fill: parent
          anchors.margins: Style.space(6)
          spacing: Style.space(2)
          Repeater {
            model: [
              { label: root.evil ? "Be nice again" : "Evil mode", act: "evil" },
              { label: "Quit Clanky", act: "quit" }
            ]
            Rectangle {
              width: parent.width
              height: Style.space(28)
              radius: Style.space(6)
              color: menuItemMouse.containsMouse
                ? Util.alpha(Color.accent, 0.18) : "transparent"
              Text {
                anchors.verticalCenter: parent.verticalCenter
                x: Style.space(8)
                text: modelData.label
                font.family: Style.font.family
                font.pixelSize: Style.font.body
                color: Color.popups.text
              }
              MouseArea {
                id: menuItemMouse
                anchors.fill: parent
                hoverEnabled: true
                cursorShape: Qt.PointingHandCursor
                onClicked: {
                  root.menuOpen = false
                  if (modelData.act === "evil") root.toggleEvil()
                  else if (modelData.act === "quit") root.quitClanky()
                }
              }
            }
          }
        }
      }

      // ----------------------------------------------------------- robot
      Item {
        id: charArea
        width: Style.space(88)
        height: Style.space(112)
        anchors.right: parent.right
        anchors.bottom: parent.bottom

        property bool eyesClosed: false

        transform: Translate { id: bob; y: 0 }

        SequentialAnimation {
          running: panel.visible
          loops: Animation.Infinite
          NumberAnimation { target: bob; property: "y"; from: 0; to: -Style.space(4); duration: 1600; easing.type: Easing.InOutSine }
          NumberAnimation { target: bob; property: "y"; from: -Style.space(4); to: 0; duration: 1600; easing.type: Easing.InOutSine }
        }

        Timer {
          id: blinkTimer
          running: panel.visible
          repeat: true
          interval: 3400
          onTriggered: {
            charArea.eyesClosed = true
            blinkEnd.restart()
            interval = 2200 + Math.floor(Math.random() * 2800)
          }
        }
        Timer {
          id: blinkEnd
          interval: 120
          onTriggered: charArea.eyesClosed = false
        }

        // The character itself is a swappable skin; evil mode overrides
        // whichever skin is configured.
        Loader {
          id: skinLoader
          anchors.fill: parent
          sourceComponent: root.evil ? clippySkin
            : (root.skin === "tux" ? tuxSkin : clankySkin)
        }

        MouseArea {
          id: charMouse
          anchors.fill: parent
          acceptedButtons: Qt.LeftButton | Qt.RightButton
          cursorShape: dragging ? Qt.ClosedHandCursor : Qt.PointingHandCursor
          hoverEnabled: true

          // Click toggles the bubble; press-and-move drags Clanky around the
          // screen. The window moves with the pointer, so the pointer's
          // window-local position stays near the press point and each event's
          // offset from it is the margin delta to apply.
          property real pressX: 0
          property real pressY: 0
          property bool dragging: false

          onPressed: (mouse) => {
            pressX = mouse.x
            pressY = mouse.y
            dragging = false
          }
          onPositionChanged: (mouse) => {
            if (!(pressedButtons & Qt.LeftButton)) return
            var dx = mouse.x - pressX
            var dy = mouse.y - pressY
            if (!dragging && Math.sqrt(dx * dx + dy * dy) > 6) dragging = true
            if (dragging) {
              panel.posRight -= dx
              panel.posBottom -= dy
              panel.clampPosition()
            }
          }
          onReleased: (mouse) => {
            if (mouse.button === Qt.RightButton) {
              root.menuOpen = !root.menuOpen
              if (root.menuOpen) root.opened = false
              return
            }
            if (dragging) {
              dragging = false
              root.persistPosition()
            } else {
              root.menuOpen = false
              root.toggle()
            }
          }
          onEntered: charArea.scale = 1.06
          onExited: charArea.scale = 1.0
        }

        Behavior on scale { NumberAnimation { duration: 120 } }
      }
    }
  }

  onOpenedChanged: {
    if (opened) Qt.callLater(function() { promptField.forceActiveFocus() })
  }
}
