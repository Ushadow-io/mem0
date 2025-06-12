import importlib.metadata

try:
    import importlib.metadata
    __version__ = importlib.metadata.version("mem0ai")
except Exception:
    __version__ = "dev"

from mem0.client.main import AsyncMemoryClient, MemoryClient  # noqa
from mem0.memory.main import AsyncMemory, Memory  # noqa
