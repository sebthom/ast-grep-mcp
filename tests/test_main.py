"""Unit tests for ast-grep MCP server"""

import json
import os
import subprocess
import sys
from unittest.mock import Mock, patch

import pytest

# Add the parent directory to the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# Mock FastMCP to disable decoration
class MockFastMCP:
    """Mock FastMCP that returns functions unchanged"""

    def __init__(self, name):
        self.name = name

    def tool(self, **kwargs):
        """Decorator that returns the function unchanged"""

        def decorator(func):
            return func  # Return original function without modification

        return decorator

    def run(self, **kwargs):
        """Mock run method"""
        pass


# Mock the Field function to return the default value
def mock_field(**kwargs):
    return kwargs.get("default")


# Patch the imports before loading main
with patch("mcp.server.fastmcp.FastMCP", MockFastMCP):
    with patch("pydantic.Field", mock_field):
        from main import (
            DumpFormat,
            dump_syntax_tree,
            find_code,
            find_code_by_rule,
            run_ast_grep,
            run_command,
        )

        # Import with different name to avoid pytest treating it as a test
        from main import test_match_code_rule as match_code_rule


class TestDumpSyntaxTree:
    """Test the dump_syntax_tree function"""

    @patch("main.run_ast_grep")
    def test_dump_syntax_tree_cst(self, mock_run):
        """Test dumping CST format"""
        mock_result = Mock()
        mock_result.stderr = "ROOT@0..10"
        mock_run.return_value = mock_result

        result = dump_syntax_tree("const x = 1", "javascript", DumpFormat.CST)

        assert result == "ROOT@0..10"
        mock_run.assert_called_once_with(
            "run",
            ["--pattern", "const x = 1", "--lang", "javascript", "--debug-query=cst"],
        )

    @patch("main.run_ast_grep")
    def test_dump_syntax_tree_pattern(self, mock_run):
        """Test dumping pattern format"""
        mock_result = Mock()
        mock_result.stderr = "pattern_node"
        mock_run.return_value = mock_result

        result = dump_syntax_tree("$VAR", "python", DumpFormat.Pattern)

        assert result == "pattern_node"
        mock_run.assert_called_once_with(
            "run", ["--pattern", "$VAR", "--lang", "python", "--debug-query=pattern"]
        )


class TestTestMatchCodeRule:
    """Test the test_match_code_rule function"""

    @patch("main.run_ast_grep")
    def test_match_found(self, mock_run):
        """Test when matches are found"""
        mock_result = Mock()
        mock_result.stdout = '[{"text": "def foo(): pass"}]'
        mock_run.return_value = mock_result

        yaml_rule = """id: test
language: python
rule:
  pattern: 'def $NAME(): $$$'
"""
        code = "def foo(): pass"

        result = match_code_rule(code, yaml_rule)

        assert result == [{"text": "def foo(): pass"}]
        mock_run.assert_called_once_with(
            "scan", ["--inline-rules", yaml_rule, "--json", "--stdin"], input_text=code
        )

    @patch("main.run_ast_grep")
    def test_no_match(self, mock_run):
        """Test when no matches are found"""
        mock_result = Mock()
        mock_result.stdout = "[]"
        mock_run.return_value = mock_result

        yaml_rule = """id: test
language: python
rule:
  pattern: 'class $NAME'
"""
        code = "def foo(): pass"

        with pytest.raises(ValueError, match="No matches found"):
            match_code_rule(code, yaml_rule)


class TestFindCode:
    """Test the find_code function"""

    @patch("main.run_ast_grep")
    def test_text_format_with_results(self, mock_run):
        """Test text format output with results"""
        mock_result = Mock()
        mock_result.stdout = "file.py:1:def foo():\nfile.py:5:def bar():"
        mock_run.return_value = mock_result

        result = find_code(
            project_folder="/test/path",
            pattern="def $NAME():",
            language="python",
            output_format="text",
        )

        assert "Found 2 matches:" in result
        assert "def foo():" in result
        assert "def bar():" in result
        mock_run.assert_called_once_with(
            "run", ["--pattern", "def $NAME():", "--lang", "python", "/test/path"]
        )

    @patch("main.run_ast_grep")
    def test_text_format_no_results(self, mock_run):
        """Test text format output with no results"""
        mock_result = Mock()
        mock_result.stdout = ""
        mock_run.return_value = mock_result

        result = find_code(
            project_folder="/test/path", pattern="nonexistent", output_format="text"
        )

        assert result == "No matches found"

    @patch("main.run_ast_grep")
    def test_text_format_with_max_results(self, mock_run):
        """Test text format with max_results limit"""
        mock_result = Mock()
        mock_result.stdout = (
            "f.py:1:match1\nf.py:2:match2\nf.py:3:match3\nf.py:4:match4"
        )
        mock_run.return_value = mock_result

        result = find_code(
            project_folder="/test/path",
            pattern="pattern",
            max_results=2,
            output_format="text",
        )

        assert "Found 2 matches (limited to 2):" in result
        assert "match1" in result
        assert "match2" in result
        assert "match3" not in result

    @patch("main.run_ast_grep")
    def test_json_format(self, mock_run):
        """Test JSON format output"""
        mock_result = Mock()
        mock_matches = [
            {"text": "def foo():", "file": "test.py"},
            {"text": "def bar():", "file": "test.py"},
        ]
        mock_result.stdout = json.dumps(mock_matches)
        mock_run.return_value = mock_result

        result = find_code(
            project_folder="/test/path", pattern="def $NAME():", output_format="json"
        )

        assert result == mock_matches
        mock_run.assert_called_once_with(
            "run", ["--pattern", "def $NAME():", "--json", "/test/path"]
        )

    @patch("main.run_ast_grep")
    def test_json_format_with_max_results(self, mock_run):
        """Test JSON format with max_results limit"""
        mock_result = Mock()
        mock_matches = [{"text": "match1"}, {"text": "match2"}, {"text": "match3"}]
        mock_result.stdout = json.dumps(mock_matches)
        mock_run.return_value = mock_result

        result = find_code(
            project_folder="/test/path",
            pattern="pattern",
            max_results=2,
            output_format="json",
        )

        assert len(result) == 2
        assert result[0]["text"] == "match1"
        assert result[1]["text"] == "match2"

    def test_invalid_output_format(self):
        """Test with invalid output format"""
        with pytest.raises(ValueError, match="Invalid output_format"):
            find_code(
                project_folder="/test/path", pattern="pattern", output_format="invalid"
            )


class TestFindCodeByRule:
    """Test the find_code_by_rule function"""

    @patch("main.run_ast_grep")
    def test_text_format_with_results(self, mock_run):
        """Test text format output with results"""
        mock_result = Mock()
        mock_result.stdout = "file.py:1:class Foo:\nfile.py:10:class Bar:"
        mock_run.return_value = mock_result

        yaml_rule = """id: test
language: python
rule:
  pattern: 'class $NAME'
"""

        result = find_code_by_rule(
            project_folder="/test/path", yaml=yaml_rule, output_format="text"
        )

        assert "Found 2 matches:" in result
        assert "class Foo:" in result
        assert "class Bar:" in result
        mock_run.assert_called_once_with(
            "scan", ["--inline-rules", yaml_rule, "/test/path"]
        )

    @patch("main.run_ast_grep")
    def test_json_format(self, mock_run):
        """Test JSON format output"""
        mock_result = Mock()
        mock_matches = [{"text": "class Foo:", "file": "test.py"}]
        mock_result.stdout = json.dumps(mock_matches)
        mock_run.return_value = mock_result

        yaml_rule = """id: test
language: python
rule:
  pattern: 'class $NAME'
"""

        result = find_code_by_rule(
            project_folder="/test/path", yaml=yaml_rule, output_format="json"
        )

        assert result == mock_matches
        mock_run.assert_called_once_with(
            "scan", ["--inline-rules", yaml_rule, "--json", "/test/path"]
        )


class TestRunCommand:
    """Test the run_command function"""

    @patch("subprocess.run")
    def test_successful_command(self, mock_run):
        """Test successful command execution"""
        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stdout = "output"
        mock_run.return_value = mock_result

        result = run_command(["echo", "test"])

        assert result.stdout == "output"
        mock_run.assert_called_once_with(
            ["echo", "test"], capture_output=True, input=None, text=True, check=True, shell=False
        )

    @patch("subprocess.run")
    def test_command_failure(self, mock_run):
        """Test command execution failure"""
        mock_run.side_effect = subprocess.CalledProcessError(
            1, ["false"], stderr="error message"
        )

        with pytest.raises(RuntimeError, match="failed with exit code 1"):
            run_command(["false"])

    @patch("subprocess.run")
    def test_command_not_found(self, mock_run):
        """Test when command is not found"""
        mock_run.side_effect = FileNotFoundError()

        with pytest.raises(RuntimeError, match="not found"):
            run_command(["nonexistent"])


class TestRunAstGrep:
    """Test the run_ast_grep function"""

    @patch("main.run_command")
    @patch("main.CONFIG_PATH", None)
    def test_without_config(self, mock_run):
        """Test running ast-grep without config"""
        mock_result = Mock()
        mock_run.return_value = mock_result

        result = run_ast_grep("run", ["--pattern", "test"])

        assert result == mock_result
        mock_run.assert_called_once_with(["ast-grep", "run", "--pattern", "test"], None)

    @patch("main.run_command")
    @patch("main.CONFIG_PATH", "/path/to/config.yaml")
    def test_with_config(self, mock_run):
        """Test running ast-grep with config"""
        mock_result = Mock()
        mock_run.return_value = mock_result

        result = run_ast_grep("scan", ["--inline-rules", "rule"])

        assert result == mock_result
        mock_run.assert_called_once_with(
            [
                "ast-grep",
                "scan",
                "--config",
                "/path/to/config.yaml",
                "--inline-rules",
                "rule",
            ],
            None,
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
