# Tool registry for Bedrock agent tools.

from .base import BaseTool
from .triage_tool import TriageTool
from .disconnect_tool import DisconnectTool

# Available tools for the agent
AVAILABLE_TOOLS = {
    "triage": TriageTool(),
    "disconnect": DisconnectTool(),
}


def get_tool(tool_name: str) -> BaseTool:
    # Get a tool by name (case-insensitive).
    return AVAILABLE_TOOLS.get(tool_name.lower())


def get_all_tool_specs():
    # Get all tool specifications for Bedrock configuration.
    return [tool.get_spec() for tool in AVAILABLE_TOOLS.values()]
