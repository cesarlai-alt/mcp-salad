# Contributing to MCP Salad 🥗

Thanks for helping grow the registry! Adding a server takes about five minutes.

## How to add a server

1. **Fork the repo** and clone your fork.

2. **Copy a template.** Grab the YAML format from the README, or copy any existing file in `registry/servers/` as a starting point.

3. **Fill in all fields:**

   ```yaml
   name: your-server            # lowercase, hyphenated, unique — matches the filename
   display_name: Your Server    # human-friendly name
   description: One clear sentence about what the server does
   author: your-handle          # npm org, GitHub user, or "community"
   homepage: https://example.com
   license: MIT
   tags: [category, keyword, keyword]
   install:
     type: stdio                # or "http" for Streamable HTTP servers
     command: npx               # stdio only
     args: ["-y", "your-mcp"]   # stdio only
     env_required:              # omit if no API key/env is needed
       - name: YOUR_API_KEY
         description: "Where to get it"
         url: https://example.com/keys
   claude_config: |
     {
       "command": "npx",
       "args": ["-y", "your-mcp"],
       "env": {"YOUR_API_KEY": "YOUR_KEY_HERE"}
     }
   ```

   For **HTTP (Streamable HTTP) servers**, use:

   ```yaml
   install:
     type: http
     url: https://your-server.example.app/mcp
   claude_config: |
     {
       "url": "https://your-server.example.app/mcp"
     }
   ```

4. **Save** the file as `registry/servers/your-server-name.yaml` (filename must match the `name` field).

5. **Regenerate the JSON** so the website stays in sync:

   ```bash
   python3 scripts/build_registry_json.py
   ```

6. **Submit a PR** with the title `Add: your-server-name`.

## Requirements

- The server must be **publicly available** (npm package, public repo, or hosted endpoint).
- **No malware** or servers that exfiltrate data. PRs are manually reviewed.
- Metadata must be **accurate** — description, author, homepage, and license should reflect the real project.
- One server per file; the filename matches the `name` field.

## Local checks

Before opening a PR, confirm your entry loads cleanly:

```bash
python3 cli/mcp.py info your-server-name
python3 cli/mcp.py list
```

If both commands show your server correctly, you're good to go.
