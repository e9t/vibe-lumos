# Lumos Chrome Extension — Setup

## 1. Install the Python package

```bash
pip install -e /path/to/vibe-lumos
```

## 2. Initialize Lumos

```bash
lumos-init
```

This creates:
- `~/.lumos/` data directory
- `~/.config/lumos.json` config
- Native messaging host manifest for Chrome (default)

### Register for other browsers

```bash
# Chrome + Chromium
lumos-init --browser chrome --browser chromium

# Custom NativeMessagingHosts directory (e.g. ChatGPT Atlas)
lumos-init --host-dir "~/Library/Application Support/ChatGPT Atlas/NativeMessagingHosts"
```

Known browsers: `chrome`, `chromium`. For others (Atlas, Brave, Edge, etc.), use `--host-dir` with the browser's `NativeMessagingHosts/` path.

## 3. Load the Extension

1. Go to `chrome://extensions/` (or equivalent in your browser)
2. Enable **Developer mode** (top right)
3. Click **Load unpacked** → select this `extension/` directory
4. Note the **Extension ID** shown (e.g. `abcdefghijklmnop`)

## 4. Update the Native Host with your Extension ID

```bash
lumos-init --extension-id YOUR_EXTENSION_ID_HERE
```

Or manually edit the manifest:

```
~/Library/Application Support/Google/Chrome/NativeMessagingHosts/com.lumos.host.json
```

```json
{
  "name": "com.lumos.host",
  "description": "Lumos Native Messaging Host",
  "path": "/path/to/lumos-host",
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

### Multiple browsers

If you use the extension in multiple browsers, each browser gets its own manifest copy. The extension ID may differ per browser — run `lumos-init` with `--extension-id` for each, or manually add all IDs to `allowed_origins` in each manifest.

## Usage

| Action | How |
|--------|-----|
| Save page | `Cmd+D` (mac) / `Ctrl+D` (win/linux) |
| Highlight text | Select text → click 💡 |
| Add note to highlight | Select text → click 📝 |
| Save image | Right-click image → **Save to Lumos** |
| View saved items | Click the Lumos icon in toolbar |
