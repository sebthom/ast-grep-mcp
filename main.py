from typing import Any, List, Optional, TypedDict
from mcp.server.fastmcp import FastMCP
import subprocess
from pydantic import Field
import json
from enum import Enum
import argparse
import os
import sys
import yaml

# Determine how the script was invoked
if sys.argv[0].endswith('main.py'):
    # Direct execution: python main.py
    prog = 'python main.py'
else:
    # Installed script execution (via uvx, pip install, etc.)
    prog = None  # Let argparse use the default

# Parse command-line arguments
parser = argparse.ArgumentParser(
    prog=prog,
    description='ast-grep MCP Server - Provides structural code search capabilities via Model Context Protocol',
    epilog='''
environment variables:
  AST_GREP_CONFIG    Path to sgconfig.yaml file (overridden by --config flag)

For more information, see: https://github.com/ast-grep/ast-grep-mcp
    ''',
    formatter_class=argparse.RawDescriptionHelpFormatter
)
parser.add_argument(
    '--config',
    type=str,
    metavar='PATH',
    help='Path to sgconfig.yaml file for customizing ast-grep behavior (language mappings, rule directories, etc.)'
)
args = parser.parse_args()

# Determine config path with precedence: --config flag > AST_GREP_CONFIG env > None
CONFIG_PATH = None
if args.config:
    if not os.path.exists(args.config):
        print(f"Error: Config file '{args.config}' does not exist")
        sys.exit(1)
    CONFIG_PATH = args.config
elif os.environ.get('AST_GREP_CONFIG'):
    env_config = os.environ.get('AST_GREP_CONFIG')
    if not os.path.exists(env_config):
        print(f"Error: Config file '{env_config}' specified in AST_GREP_CONFIG does not exist")
        sys.exit(1)
    CONFIG_PATH = env_config

# Initialize FastMCP server
mcp = FastMCP("ast-grep")

# Type definitions for search results
class SearchMetadata(TypedDict):
    total_matches: int
    offset: int
    limit: Optional[int]
    returned: int
    has_more: bool

class SearchResult(TypedDict):
    results: List[dict[str, Any]]  # List of ast-grep match objects
    metadata: SearchMetadata

# Global cache - stores only the last search as (key, results) tuple
_last_search: Optional[tuple[tuple, List[dict[str, Any]]]] = None

def get_paginated_results(
    key: tuple,
    offset: int,
    limit: Optional[int],
    execute_search
) -> SearchResult:
    """Helper function to handle caching and pagination logic.

    Args:
        key: Tuple identifying the search (used for cache comparison)
        offset: Number of results to skip
        limit: Maximum number of results to return
        execute_search: Callable that executes the actual search and returns results

    Returns:
        SearchResult with paginated results and metadata
    """
    global _last_search

    # If offset is 0, always perform fresh search
    if offset == 0:
        results = execute_search()
        _last_search = (key, results)
    else:
        # Try to use cached results for pagination
        if _last_search and _last_search[0] == key:
            results = _last_search[1]
        else:
            # Cache miss - need to re-execute
            results = execute_search()
            _last_search = (key, results)

    # Apply offset and limit
    total_matches = len(results)
    end_idx = offset + limit if limit is not None else None
    sliced_results = results[offset:end_idx]

    return {
        "results": sliced_results,
        "metadata": {
            "total_matches": total_matches,
            "offset": offset,
            "limit": limit,
            "returned": len(sliced_results),
            "has_more": (offset + len(sliced_results)) < total_matches
        }
    }


class DumpFormat(Enum):
    Pattern = "pattern"
    CST = "cst"
    AST = "ast"

@mcp.tool()
def dump_syntax_tree(
    code: str = Field(description = "The code you need"),
    language: str = Field(description = "The language of the code"),
    format: DumpFormat = Field(description = "Code dump format. Available values: pattern, ast, cst", default = "cst"),
) -> str:
    """
    Dump code's syntax structure or dump a query's pattern structure.
    This is useful to discover correct syntax kind and syntax tree structure. Call it when debugging a rule.
    The tool requires three arguments: code, language and format. The first two are self-explanatory.
    `format` is the output format of the syntax tree.
    use `format=cst` to inspect the code's concrete syntax tree structure, useful to debug target code.
    use `format=pattern` to inspect how ast-grep interprets a pattern, useful to debug pattern rule.

    Uses: ast-grep run --pattern <code> --lang <language> --debug-query=<format>
    """
    result = run_ast_grep("run", ["--pattern", code, "--lang", language, f"--debug-query={format.value}"])
    return result.stderr.strip()

@mcp.tool()
def get_supported_languages() -> List[str]:
    """
    Get list of languages supported by ast-grep.

    Returns common language identifiers that can be used in the 'language' parameter of other tools.
    Includes custom languages from config file if provided.
    """
    base_languages = [  # https://ast-grep.github.io/reference/languages.html
        "bash",
        "c",
        "cpp",
        "csharp",
        "css",
        "elixir",
        "go",
        "haskell",
        "html",
        "java",
        "javascript",
        "json",
        "jsx",
        "kotlin",
        "lua",
        "nix",
        "php",
        "python",
        "ruby",
        "rust",
        "scala",
        "solidity",
        "swift",
        "tsx",
        "typescript",
        "yaml"
    ]

    # Check for custom languages in config file
    # https://ast-grep.github.io/advanced/custom-language.html#register-language-in-sgconfig-yml
    if CONFIG_PATH and os.path.exists(CONFIG_PATH):
        try:
            import yaml
            with open(CONFIG_PATH, 'r') as f:
                config = yaml.safe_load(f)
                if config and 'customLanguages' in config:
                    custom_langs = list(config['customLanguages'].keys())
                    return sorted(set(base_languages + custom_langs))
        except Exception:
            pass

    return base_languages

@mcp.tool()
def test_match_code_rule(
    code: str = Field(description="The code to test against the rule"),
    yaml: str = Field(description="The ast-grep YAML rule to search. It must have id, language, rule fields."),
) -> List[dict[str, Any]]:
    """
    Test a code against an ast-grep YAML rule.
    This is useful to test a rule before using it in a project.

    Uses: ast-grep scan --inline-rules <yaml> --json --stdin
    """
    result = run_ast_grep("scan", ["--inline-rules", yaml, "--json", "--stdin"], input_text = code)
    matches = json.loads(result.stdout.strip())
    if not matches:
        raise ValueError("No matches found for the given code and rule. Try adding `stopBy: end` to your inside/has rule.")
    return matches

@mcp.tool()
def find_code(
    project_folder: str = Field(description="The absolute path to the project folder. It must be absolute path."),
    pattern: str = Field(description="The ast-grep pattern to search for. Note, the pattern must have valid AST structure."),
    language: str = Field(description="The language of the query", default=""),
    offset: int = Field(description="Number of results to skip. Use 0 for fresh search, >0 for pagination.", default=0),
    limit: Optional[int] = Field(description="Maximum number of results to return. None returns all remaining results.", default=None),
) -> SearchResult:
    """
    Find code in a project folder that matches the given ast-grep pattern.
    Pattern is good for simple and single-AST node result.
    For more complex usage, please use YAML by `find_code_by_rule`.

    Uses: ast-grep run --pattern <pattern> --json <project_folder>

    IMPORTANT: This tool returns detailed match data that can consume significant tokens.
    Use pagination to avoid hitting token limits:
    - limit: Maximum results to return (recommended: 50-100 depending on pattern complexity)
    - offset: Number of results to skip for pagination (default: 0)

    Without limit, ALL matches are returned which may exceed token limits in large codebases.

    Returns a dictionary with:
    - results: List of matches (limited by offset/limit parameters)
    - metadata: Information about the search including:
      - total_matches: Total number across all pages
      - has_more: Boolean indicating if more results exist
      - offset/limit: Current pagination state

    Example usage for token-efficient searching:
      Initial search: find_code(pattern="class $NAME", limit=10)
      If metadata.has_more is true and you need more results:
      Next page: find_code(pattern="class $NAME", limit=10, offset=10)
    """
    args = ["--pattern", pattern, "--json"]
    if language:
        args.extend(["--lang", language])
    args.append(project_folder)
    return get_paginated_results(
        key = (pattern, project_folder, language),
        offset = offset,
        limit = limit,
        execute_search = lambda: json.loads(run_ast_grep("run", args).stdout.strip() or "[]")
    )

@mcp.tool()
def find_code_by_rule(
    project_folder: str = Field(description="The absolute path to the project folder. It must be absolute path."),
    yaml: str = Field(description="The ast-grep YAML rule to search. It must have id, language, rule fields."),
    offset: int = Field(description="Number of results to skip. Use 0 for fresh search, >0 for pagination.", default=0),
    limit: Optional[int] = Field(description="Maximum number of results to return. None returns all remaining results.", default=None),
    ) -> SearchResult:
    """
    Find code using ast-grep's YAML rule in a project folder.
    YAML rule is more powerful than simple pattern and can perform complex search like find AST inside/having another AST.
    It is a more advanced search tool than the simple `find_code`.

    Tip: When using relational rules (inside/has), add `stopBy: end` to ensure complete traversal.

    Uses: ast-grep scan --inline-rules <yaml> --json <project_folder>

    IMPORTANT: This tool returns detailed match data that can consume significant tokens.
    Use pagination to avoid hitting token limits:
    - limit: Maximum results to return (recommended: 50-100 depending on pattern complexity)
    - offset: Number of results to skip for pagination (default: 0)

    Without limit, ALL matches are returned which may exceed token limits in large codebases.

    Returns a dictionary with:
    - results: List of matches (limited by offset/limit parameters)
    - metadata: Information about the search including:
      - total_matches: Total number across all pages
      - has_more: Boolean indicating if more results exist
      - offset/limit: Current pagination state

    Example usage for token-efficient searching:
      Initial search: find_code_by_rule(yaml="id: x\\nlanguage: python\\nrule: {pattern: 'class $NAME'}", limit=10)
      If metadata.has_more is true and you need more results:
      Next page: find_code_by_rule(yaml="id: x\\nlanguage: python\\nrule: {pattern: 'class $NAME'}", limit=10, offset=10)
    """
    args = ["--inline-rules", yaml, "--json", project_folder]
    return get_paginated_results(
        key = (yaml, project_folder),
        offset = offset,
        limit = limit,
        execute_search = lambda: json.loads(run_ast_grep("scan", args).stdout.strip() or "[]")
    )

def run_command(args: List[str], input_text: Optional[str] = None) -> subprocess.CompletedProcess:
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            input=input_text,
            text=True,
            check=True  # Raises CalledProcessError if return code is non-zero
        )
        return result
    except subprocess.CalledProcessError as e:
        stderr_msg = e.stderr.strip() if e.stderr else "(no error output)"
        error_msg = f"Command {e.cmd} failed with exit code {e.returncode}: {stderr_msg}"
        raise RuntimeError(error_msg) from e
    except FileNotFoundError as e:
        error_msg = f"Command '{args[0]}' not found. Please ensure {args[0]} is installed and in PATH."
        raise RuntimeError(error_msg) from e

def run_ast_grep(command:str, args: List[str], input_text: Optional[str] = None) -> subprocess.CompletedProcess:
    if CONFIG_PATH:
        args = ["--config", CONFIG_PATH] + args
    return run_command(["ast-grep", command] + args, input_text)

def run_mcp_server() -> None:
    """
    Run the MCP server.
    This function is used to start the MCP server when this script is run directly.
    """
    mcp.run(transport="stdio")

if __name__ == "__main__":
    run_mcp_server()
