# README visuals

How the three images in the README were made, so they can be re-recorded when the tools change.

| File | What it is | How it was made |
|---|---|---|
| `screenshots/demo-terminal.gif` | A real MCP client session: connect, list tools, `scene_compose`, `scene_to_project` | Window capture of a console running a small MCP stdio client against `engine-mcp-spine` |
| `screenshots/sample-rig.gif` | `idle`, `wave` and `cheer` playing in the Spine editor | Window capture of Spine in animate mode with loop on, cropped to the viewport |
| `screenshots/spine-editor.jpg` | The sample rig in Spine with the bone tree expanded | `spine_import` on `examples/mecha.json`, then `spine_open_editor`, then a window capture |

## Before you publish any recording

- `spine_version` returns the Spine launcher's output verbatim, which includes the **licensee
  name and email**. Do not call it on camera.
- The Spine launcher window shows the same licence details. Start capturing only once the editor
  itself is open.
- Capture the tool's own window rather than the whole screen, and check every frame for absolute
  paths and usernames.
