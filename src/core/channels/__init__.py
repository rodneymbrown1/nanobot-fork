"""Chat channels module with plugin architecture."""

from core.channels.base import BaseChannel
from core.channels.manager import ChannelManager

__all__ = ["BaseChannel", "ChannelManager"]
