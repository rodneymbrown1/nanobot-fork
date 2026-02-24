"""Tests for configuration loading — env-only mode, file precedence, env path override."""

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from nanobot.config.loader import get_config_path, load_config


class TestGetConfigPath:
    """Tests for get_config_path()."""

    def test_default_path(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("NANOBOT_CONFIG_PATH", None)
            assert get_config_path() == Path.home() / ".nanobot" / "config.json"

    def test_env_override(self):
        with patch.dict(os.environ, {"NANOBOT_CONFIG_PATH": "/tmp/custom.json"}):
            assert get_config_path() == Path("/tmp/custom.json")


class TestLoadConfig:
    """Tests for load_config()."""

    def test_env_only_loading(self, tmp_path):
        """When no config file exists, NANOBOT_* env vars populate config."""
        missing = tmp_path / "does_not_exist.json"
        env = {
            "NANOBOT_GATEWAY__PORT": "9999",
            "NANOBOT_GATEWAY__HOST": "0.0.0.0",
        }
        with patch.dict(os.environ, env, clear=False):
            config = load_config(config_path=missing)
        assert config.gateway.port == 9999
        assert config.gateway.host == "0.0.0.0"

    def test_file_takes_precedence(self, tmp_path):
        """Values in config file win over env vars."""
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text(json.dumps({"gateway": {"port": 1234}}))
        env = {"NANOBOT_GATEWAY__PORT": "9999"}
        with patch.dict(os.environ, env, clear=False):
            config = load_config(config_path=cfg_file)
        assert config.gateway.port == 1234

    def test_empty_defaults(self, tmp_path):
        """With no file and no env vars, defaults apply."""
        missing = tmp_path / "nope.json"
        config = load_config(config_path=missing)
        assert config.gateway.port == 18790
        assert config.gateway.host == "127.0.0.1"

    def test_env_path_override(self, tmp_path):
        """NANOBOT_CONFIG_PATH directs load_config to a custom file."""
        cfg_file = tmp_path / "alt.json"
        cfg_file.write_text(json.dumps({"gateway": {"port": 5555}}))
        with patch.dict(os.environ, {"NANOBOT_CONFIG_PATH": str(cfg_file)}):
            config = load_config()  # no explicit path — uses env
        assert config.gateway.port == 5555

    def test_corrupt_file_falls_back(self, tmp_path):
        """Corrupt JSON falls back to env / defaults."""
        cfg_file = tmp_path / "bad.json"
        cfg_file.write_text("NOT JSON!!!")
        config = load_config(config_path=cfg_file)
        assert config.gateway.port == 18790
