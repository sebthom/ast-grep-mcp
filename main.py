from typing import Any, List, Optional
from mcp.server.fastmcp import FastMCP
import subprocess
from pydantic import Field
import json

# Initialize FastMCP server
mcp = FastMCP("ast-grep")

@mcp.tool()
def find_code(
    project_folder: str = Field(description="The path to the project folder"),
    pattern: str = Field(description="The ast-grep pattern to search for"),
    language: str = Field(description="The language of the query", default=""),
) -> List[dict[str, Any]]:
    """Find code in a project folder that matches the given ast-grep pattern.
    """
    return run_ast_grep_command(pattern, project_folder, language)

def run_ast_grep_command(pattern: str, project_folder: str, language: Optional[str]) -> List[dict[str, Any]]:
    try:
        args = ["ast-grep", "--pattern", pattern, "--json", project_folder]
        if language:
            args.extend(["--lang", language])
        # Run command and capture output
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            check=True  # Raises CalledProcessError if return code is non-zero
        )
        return json.loads(result.stdout)
    except subprocess.CalledProcessError as e:
        print(f"Command failed with return code {e.returncode}")
        print("Error output:", e.stderr)
        return e.stderr
    except FileNotFoundError:
        print("Command not found")
        return []

if __name__ == "__main__":
    mcp.run(transport = "stdio")
