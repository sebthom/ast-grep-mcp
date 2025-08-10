from typing import Any, List, Optional
from mcp.server.fastmcp import FastMCP
import subprocess
from pydantic import Field
import json
from enum import Enum
import argparse
import os
import sys

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

    Internally calls: ast-grep run --pattern <code> --lang <language> --debug-query=<format>
    """
    result = run_ast_grep("run", ["--pattern", code, "--lang", language, f"--debug-query={format.value}"])
    return result.stderr.strip()

@mcp.tool()
def test_match_code_rule(
    code: str = Field(description="The code to test against the rule"),
    yaml: str = Field(description="The ast-grep YAML rule to search. It must have id, language, rule fields."),
) -> List[dict[str, Any]]:
    """
    Test a code against an ast-grep YAML rule.
    This is useful to test a rule before using it in a project.

    Internally calls: ast-grep scan --inline-rules <yaml> --json --stdin
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
    max_results: Optional[int] = Field(default=None, description="Maximum results to return"),
    output_format: str = Field(default="text", description="'text' or 'json'"),
) -> str | List[dict[str, Any]]:
    """
    Find code in a project folder that matches the given ast-grep pattern.
    Pattern is good for simple and single-AST node result.
    For more complex usage, please use YAML by `find_code_by_rule`.

    Internally calls: ast-grep run --pattern <pattern> [--json] <project_folder>

    Output formats:
    - text (default): Simple file:line:content format, ~75% fewer tokens
    - json: Full match objects with metadata

    Example usage:
      find_code(pattern="class $NAME", max_results=20)  # Returns text format
      find_code(pattern="class $NAME", output_format="json")  # Returns JSON with metadata
    """
    if output_format not in ["text", "json"]:
        raise ValueError(f"Invalid output_format: {output_format}. Must be 'text' or 'json'.")

    args = ["--pattern", pattern]
    if language:
        args.extend(["--lang", language])

    if output_format == "json":
        # JSON format - return structured data
        result = run_ast_grep("run", args + ["--json", project_folder])
        matches = json.loads(result.stdout.strip() or "[]")
        # Limit results if max_results is specified
        if max_results is not None and len(matches) > max_results:
            matches = matches[:max_results]
        return matches
    else:
        # Text format - return plain text output
        result = run_ast_grep("run", args + [project_folder])
        output = result.stdout.strip()
        if not output:
            output = "No matches found"
        else:
            # Apply max_results limit if specified
            lines = output.split('\n')
            non_empty_lines = [line for line in lines if line.strip()]
            if max_results is not None and len(non_empty_lines) > max_results:
                # Limit the results
                non_empty_lines = non_empty_lines[:max_results]
                output = '\n'.join(non_empty_lines)
                header = f"Found {len(non_empty_lines)} matches (limited to {max_results}):\n"
            else:
                header = f"Found {len(non_empty_lines)} matches:\n"
            output = header + output
        return output

@mcp.tool()
def find_code_by_rule(
    project_folder: str = Field(description="The absolute path to the project folder. It must be absolute path."),
    yaml: str = Field(description="The ast-grep YAML rule to search. It must have id, language, rule fields."),
    max_results: Optional[int] = Field(default=None, description="Maximum results to return"),
    output_format: str = Field(default="text", description="'text' or 'json'"),
    ) -> str | List[dict[str, Any]]:
    """
    Find code using ast-grep's YAML rule in a project folder.
    YAML rule is more powerful than simple pattern and can perform complex search like find AST inside/having another AST.
    It is a more advanced search tool than the simple `find_code`.

    Tip: When using relational rules (inside/has), add `stopBy: end` to ensure complete traversal.

    Internally calls: ast-grep scan --inline-rules <yaml> [--json] <project_folder>

    Output formats:
    - text (default): Simple file:line:content format, ~75% fewer tokens
    - json: Full match objects with metadata

    Example usage:
      find_code_by_rule(yaml="id: x\\nlanguage: python\\nrule: {pattern: 'class $NAME'}", max_results=20)
      find_code_by_rule(yaml="...", output_format="json")  # For full metadata
    """
    if output_format not in ["text", "json"]:
        raise ValueError(f"Invalid output_format: {output_format}. Must be 'text' or 'json'.")

    args = ["--inline-rules", yaml]

    if output_format == "json":
        # JSON format - return structured data
        result = run_ast_grep("scan", args + ["--json", project_folder])
        matches = json.loads(result.stdout.strip() or "[]")
        # Limit results if max_results is specified
        if max_results is not None and len(matches) > max_results:
            matches = matches[:max_results]
        return matches
    else:
        # Text format - return plain text output
        result = run_ast_grep("scan", args + [project_folder])
        output = result.stdout.strip()
        if not output:
            output = "No matches found"
        else:
            # Apply max_results limit if specified
            lines = output.split('\n')
            non_empty_lines = [line for line in lines if line.strip()]
            if max_results is not None and len(non_empty_lines) > max_results:
                # Limit the results
                non_empty_lines = non_empty_lines[:max_results]
                output = '\n'.join(non_empty_lines)
                header = f"Found {len(non_empty_lines)} matches (limited to {max_results}):\n"
            else:
                header = f"Found {len(non_empty_lines)} matches:\n"
            output = header + output
        return output

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
