# Base class for all tools.

from abc import ABC, abstractmethod
from typing import Dict, Any


class BaseTool(ABC):
    # Base class for all agent tools.
    
    @property
    @abstractmethod
    def name(self) -> str:
        # Tool name (must match Bedrock tool spec).
        pass
    
    @property
    @abstractmethod
    def description(self) -> str:
        # Tool description for the agent.
        pass
    
    @property
    def input_schema(self) -> Dict[str, Any]:
        # JSON schema for tool input; override if your tool needs parameters.
        return {
            "type": "object",
            "properties": {},
            "required": []
        }
    
    @abstractmethod
    async def execute(self, tool_use_content: Dict[str, Any]) -> Dict[str, Any]:
        # Execute the tool with the given input.
        pass
    
    def get_spec(self) -> Dict[str, Any]:
        # Get the Bedrock tool specification.
        import json
        return {
            "toolSpec": {
                "name": self.name,
                "description": self.description,
                "inputSchema": {
                    "json": json.dumps(self.input_schema)
                }
            }
        }
