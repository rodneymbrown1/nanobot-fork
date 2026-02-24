"""Shell execution tool."""

import asyncio
import os
import re
import signal
from pathlib import Path
from urllib.parse import unquote
from typing import Any

from nanobot.agent.tools.base import Tool


class ExecTool(Tool):
    """Tool to execute shell commands."""
    
    def __init__(
        self,
        timeout: int = 60,
        working_dir: str | None = None,
        deny_patterns: list[str] | None = None,
        allow_patterns: list[str] | None = None,
        restrict_to_workspace: bool = False,
    ):
        self.timeout = timeout
        self.working_dir = working_dir
        self.deny_patterns = deny_patterns or [
            # Destructive file/disk operations
            r"\brm\s+-[rf]{1,2}\b",
            r"\bdel\s+/[fq]\b",
            r"\brmdir\s+/s\b",
            r"(?:^|[;&|]\s*)format\b",
            r"\b(mkfs|diskpart)\b",
            r"\bdd\s+if=",
            r">\s*/dev/sd",
            r"\b(shutdown|reboot|poweroff)\b",
            r":\(\)\s*\{.*\};\s*:",
            # Meta-execution vectors
            r"\beval\b",
            r"\bexec\b",
            r"\bbash\s+-c\b",
            r"\bsh\s+-c\b",
            r"\bzsh\s+-c\b",
            r"\bpython[23]?\s+-c\b",
            r"\bperl\s+-e\b",
            r"\bruby\s+-e\b",
            r"\bnode\s+-e\b",
            # Pipe to shell
            r"\|\s*(bash|sh|zsh)\b",
            # Base64 decode (common evasion)
            r"\bbase64\s+--?d(ecode)?\b",
            # Command substitution
            r"\$\(",
            r"`",
            # Variable-based evasion
            r"\bexport\s+\w+=",
        ]
        self.allow_patterns = allow_patterns or []
        self.restrict_to_workspace = restrict_to_workspace
    
    @property
    def name(self) -> str:
        return "exec"
    
    @property
    def description(self) -> str:
        return (
            "Execute a shell command and return its output. Use with caution. "
            "Command substitution ($(...) and backticks) is blocked — run commands separately instead."
        )
    
    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The shell command to execute"
                },
                "working_dir": {
                    "type": "string",
                    "description": "Optional working directory for the command"
                }
            },
            "required": ["command"]
        }
    
    async def execute(self, command: str, working_dir: str | None = None, **kwargs: Any) -> str:
        cwd = working_dir or self.working_dir or os.getcwd()
        guard_error = self._guard_command(command, cwd)
        if guard_error:
            return guard_error
        
        try:
            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                # Run in a new process group so we can kill all child processes
                # on timeout without leaving orphans.
                start_new_session=True,
            )

            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=self.timeout
                )
            except asyncio.TimeoutError:
                # Kill the entire process group to reap child processes too.
                try:
                    if process.pid is not None:
                        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                except ProcessLookupError:
                    pass
                try:
                    await asyncio.wait_for(process.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    pass
                return f"Error: Command timed out after {self.timeout} seconds"
            
            output_parts = []
            
            if stdout:
                output_parts.append(stdout.decode("utf-8", errors="replace"))
            
            if stderr:
                stderr_text = stderr.decode("utf-8", errors="replace")
                if stderr_text.strip():
                    output_parts.append(f"STDERR:\n{stderr_text}")
            
            if process.returncode != 0:
                output_parts.append(f"\nExit code: {process.returncode}")
            
            result = "\n".join(output_parts) if output_parts else "(no output)"
            
            # Truncate very long output
            max_len = 10000
            if len(result) > max_len:
                result = result[:max_len] + f"\n... (truncated, {len(result) - max_len} more chars)"
            
            return result
            
        except Exception as e:
            return f"Error executing command: {str(e)}"

    @staticmethod
    def _normalize_command(cmd: str) -> str:
        """Decode ANSI-C quoting and hex escapes so deny patterns can match evasion attempts."""
        # Expand $'\xNN' and $'\NNN' ANSI-C style quoting
        def _expand_ansi_c(m: re.Match) -> str:
            inner = m.group(1)
            # Replace \xNN hex escapes
            inner = re.sub(r"\\x([0-9a-fA-F]{2})", lambda h: chr(int(h.group(1), 16)), inner)
            # Replace \NNN octal escapes
            inner = re.sub(r"\\([0-7]{1,3})", lambda o: chr(int(o.group(1), 8)), inner)
            return inner

        cmd = re.sub(r"\$'([^']*)'", _expand_ansi_c, cmd)
        return cmd

    def _guard_command(self, command: str, cwd: str) -> str | None:
        """Best-effort safety guard for potentially destructive commands."""
        cmd = command.strip()
        cmd = self._normalize_command(cmd)
        lower = cmd.lower()

        for pattern in self.deny_patterns:
            if re.search(pattern, lower):
                return "Error: Command blocked by safety guard (dangerous pattern detected)"

        if self.allow_patterns:
            if not any(re.search(p, lower) for p in self.allow_patterns):
                return "Error: Command blocked by safety guard (not in allowlist)"

        if self.restrict_to_workspace:
            # Check both literal and URL-decoded forms to catch encoded traversal attempts.
            decoded_cmd = unquote(cmd)
            if "..\\" in cmd or "../" in cmd or "..\\" in decoded_cmd or "../" in decoded_cmd:
                return "Error: Command blocked by safety guard (path traversal detected)"

            cwd_path = Path(cwd).resolve()

            win_paths = re.findall(r"[A-Za-z]:\\[^\\\"']+", cmd)
            # Only match absolute paths — avoid false positives on relative
            # paths like ".venv/bin/python" where "/bin/python" would be
            # incorrectly extracted by the old pattern.
            posix_paths = re.findall(r"(?:^|[\s|>])(/[^\s\"'>]+)", cmd)

            for raw in win_paths + posix_paths:
                try:
                    p = Path(raw.strip()).resolve()
                except Exception:
                    continue
                if p.is_absolute() and cwd_path not in p.parents and p != cwd_path:
                    return "Error: Command blocked by safety guard (path outside working dir)"

        return None
