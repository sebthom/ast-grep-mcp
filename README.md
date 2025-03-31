# ast-grep MCP

This is an experimental MCP implementation using ast-grep CLI.


## Prerequisite

1. Installing `ast-grep` and `uv`
2. Installing MCP client, preferably Cursor
3. Clone this repo, install via `uv`
3. Configure MCP json

```json
{
    "mcpServers": {
      "server-name": {
        "command": "uv",
        "args": ["--directory", "/path/to/repo", "run", "main.py"],
        "env": {}
      }
    }
  }
```

4. Ask cursor ai to search your codebase!
