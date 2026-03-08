# Lumos

Personal knowledge capture tool. Bookmark in Chrome. Search in Terminal.

## CLI

### Setup

```bash
# Install
git clone https://github.com/e9t/vibe-lumos.git
cd vibe-lumos
pip install -e .

# Initialize
lumos-init --data-dir /some/directory   # use a custom data directory
```

### Config

Config lives at `~/.config/lumos.json`. Override the data dir with `LUMOS_DATA_DIR`.

```json
{
  "data_dir": "~/.lumos",
  "cache": { "formats": ["mhtml", "readable"] },
  "models": {
    "ocr": { "enabled": true, "provider": "upstage", "api_key_env": "UPSTAGE_API_KEY" },
    "llm": { "model": "solar-mini", "api_key_env": "UPSTAGE_API_KEY" }
  },
  "list": { "default_limit": 10 },
  "theme": { "highlight_color": "yellow", "selection_color": "yellow" }
}
```

### Usage

```bash
lumos                           # open interactive TUI
lumos "query terms"             # search and open TUI
lumos --sort priority:desc      # sort by priority
lumos --type kindle             # filter by source
lumos --begin 7d                # last 7 days
lumos --match exact "phrase"    # exact match (default: smart/LLM-expanded)
```

### Import external data

```bash
lumos-import diigo export.jsonl         # import Diigo bookmarks/highlights from https://mm-diigo-rescue-285.netlify.app 
lumos-import kindle "My Clippings.txt"  # import Kindle highlights
```

## Chrome Extension

### Setup

Load `extension/` as an unpacked extension in Chrome. Then register it:

```bash
lumos-init --extension-id <ID>
```

### Usage

- **Text highlights** — select text, click the toolbar to save
- **Image capture** — right-click an image, "Save to Lumos" (with async OCR via Upstage)
- **Page save** — `Cmd+D` / `Ctrl+D` to bookmark + cache (MHTML + Readability)
- **Highlight restoration** — revisit a page and see your highlights restored

## License

MIT
