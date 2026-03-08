# Lumos Chrome Extension — Setup

## 1. Install the Python package

```bash
pip install -e /path/to/vibe-lumos
```

## 2. Initialize Lumos

```bash
lumos init
```

This creates:
- `~/.lumos/` data directory
- `~/.config/lumos.json` config
- `~/Library/Application Support/Google/Chrome/NativeMessagingHosts/com.lumos.host.json` (macOS)

## 3. Load the Extension in Chrome

1. Go to `chrome://extensions/`
2. Enable **Developer mode** (top right)
3. Click **Load unpacked** → select this `extension/` directory
4. Note the **Extension ID** shown (e.g. `abcdefghijklmnop`)

## 4. Update the Native Host with your Extension ID

Edit the native messaging host manifest:

```
~/Library/Application Support/Google/Chrome/NativeMessagingHosts/com.lumos.host.json
```

Add your extension ID to `allowed_origins`:

```json
{
  "name": "com.lumos.host",
  "description": "Lumos Native Messaging Host",
  "path": "/path/to/lumos-host-wrapper.sh",
  "type": "stdio",
  "allowed_origins": [
    "chrome-extension://YOUR_EXTENSION_ID_HERE/"
  ]
}
```

After `pip install -e .`, the `lumos-host` command is registered. Find its path:

```bash
which lumos-host
# → e.g. /Users/you/.venv/bin/lumos-host
```

Use that path in the manifest.

## Usage

| Action | How |
|--------|-----|
| Save page | `Cmd+D` (mac) / `Ctrl+D` (win/linux) |
| Highlight text | Select text → click 💡 |
| Add note to highlight | Select text → click 📝 |
| Save image | Right-click image → **Save to Lumos** |
| View saved items | Click the Lumos icon in toolbar |
