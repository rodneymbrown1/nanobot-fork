"""Tests for shell command safety guard — bypass vectors and legitimate commands."""

import pytest

from nanobot.agent.tools.shell import ExecTool


@pytest.fixture
def tool():
    return ExecTool(restrict_to_workspace=False)


# ── Legitimate commands that SHOULD pass ─────────────────────────────────────

LEGITIMATE = [
    "ls -la",
    "cat README.md",
    "grep -r 'TODO' src/",
    "python script.py",
    "pip install requests",
    "git status",
    "git diff HEAD~1",
    "curl https://example.com",
    "echo hello world",
    "find . -name '*.py'",
    "wc -l file.txt",
    "head -20 file.txt",
    "tail -f /var/log/syslog",
    "mkdir -p new_dir",
    "cp file1.txt file2.txt",
    "mv old.txt new.txt",
    "chmod 644 file.txt",
    "npm install",
    "node server.js",
    "python3 manage.py runserver",
    "uv sync --dev",
    "pytest tests/ -v",
    "ruff check .",
]


@pytest.mark.parametrize("cmd", LEGITIMATE)
def test_legitimate_commands_pass(tool, cmd):
    result = tool._guard_command(cmd, "/tmp")
    assert result is None, f"Legitimate command blocked: {cmd}"


# ── Original deny patterns (should still block) ─────────────────────────────

ORIGINAL_BLOCKED = [
    "rm -rf /",
    "rm -r important_dir",
    "mkfs.ext4 /dev/sda1",
    "dd if=/dev/zero of=/dev/sda",
    "shutdown now",
    "reboot",
]


@pytest.mark.parametrize("cmd", ORIGINAL_BLOCKED)
def test_original_deny_patterns(tool, cmd):
    result = tool._guard_command(cmd, "/tmp")
    assert result is not None, f"Dangerous command not blocked: {cmd}"


# ── Bypass vectors (all should be blocked) ───────────────────────────────────

class TestBypassVectors:
    """Every known bypass technique must be caught."""

    def test_ansi_c_hex_quoting(self, tool):
        # $'\x72\x6d' decodes to 'rm'
        result = tool._guard_command("$'\\x72\\x6d' -rf /", "/tmp")
        assert result is not None

    def test_command_substitution_dollar_paren(self, tool):
        result = tool._guard_command("$(echo rm) -rf /", "/tmp")
        assert result is not None

    def test_command_substitution_backticks(self, tool):
        result = tool._guard_command("`echo rm` -rf /", "/tmp")
        assert result is not None

    def test_eval(self, tool):
        result = tool._guard_command("eval 'rm -rf /'", "/tmp")
        assert result is not None

    def test_exec(self, tool):
        result = tool._guard_command("exec rm -rf /", "/tmp")
        assert result is not None

    def test_bash_c(self, tool):
        result = tool._guard_command("bash -c 'rm -rf /'", "/tmp")
        assert result is not None

    def test_sh_c(self, tool):
        result = tool._guard_command("sh -c 'rm -rf /'", "/tmp")
        assert result is not None

    def test_zsh_c(self, tool):
        result = tool._guard_command("zsh -c 'rm -rf /'", "/tmp")
        assert result is not None

    def test_python_c(self, tool):
        result = tool._guard_command("python -c 'import os; os.system(\"rm -rf /\")'", "/tmp")
        assert result is not None

    def test_python3_c(self, tool):
        result = tool._guard_command("python3 -c 'import shutil; shutil.rmtree(\"/\")'", "/tmp")
        assert result is not None

    def test_perl_e(self, tool):
        result = tool._guard_command("perl -e 'system(\"rm -rf /\")'", "/tmp")
        assert result is not None

    def test_ruby_e(self, tool):
        result = tool._guard_command("ruby -e 'system(\"rm -rf /\")'", "/tmp")
        assert result is not None

    def test_node_e(self, tool):
        result = tool._guard_command("node -e 'require(\"child_process\").execSync(\"rm -rf /\")'", "/tmp")
        assert result is not None

    def test_pipe_to_bash(self, tool):
        result = tool._guard_command("echo 'rm -rf /' | bash", "/tmp")
        assert result is not None

    def test_pipe_to_sh(self, tool):
        result = tool._guard_command("echo 'rm -rf /' | sh", "/tmp")
        assert result is not None

    def test_base64_decode(self, tool):
        result = tool._guard_command("echo cm0gLXJmIC8= | base64 --decode | sh", "/tmp")
        assert result is not None

    def test_base64_decode_short_flag(self, tool):
        result = tool._guard_command("echo cm0gLXJmIC8= | base64 -d", "/tmp")
        assert result is not None

    def test_export_var_evasion(self, tool):
        result = tool._guard_command("export CMD=rm; $CMD -rf /", "/tmp")
        assert result is not None


class TestNormalization:
    """Test the _normalize_command static method directly."""

    def test_hex_escape(self):
        assert ExecTool._normalize_command("$'\\x72\\x6d'") == "rm"

    def test_octal_escape(self):
        assert ExecTool._normalize_command("$'\\162\\155'") == "rm"

    def test_mixed(self):
        result = ExecTool._normalize_command("$'\\x72\\x6d' -rf $'\\x2f'")
        assert "rm" in result
        assert "/" in result

    def test_no_ansi_c_unchanged(self):
        assert ExecTool._normalize_command("ls -la") == "ls -la"
