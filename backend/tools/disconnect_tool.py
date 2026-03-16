# Disconnect/End Conversation tool for clean session termination.

import json
import asyncio
from typing import Dict, Any
from .base import BaseTool


class DisconnectTool(BaseTool):
    # Tool to cleanly end the conversation and close the session.
    
    @property
    def name(self) -> str:
        return "disconnect"
    
    @property
    def description(self) -> str:
        return (
            "End the conversation gracefully and close the session. "
            "Use this when the patient says goodbye or the triage is complete and they want to leave. "
            "The tool will send a final goodbye message and close the websocket connection after 7 seconds."
        )
    
    @property
    def input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": (
                        "Final message to say to the patient. "
                        "Keep it warm and brief (1-2 sentences). "
                        "Examples: 'Bye bye! Feel better soon.', 'Goodbye! Take care.'"
                    )
                }
            },
            "required": ["message"]
        }
    
    async def execute(self, tool_use_content: Dict[str, Any]) -> Dict[str, Any]:
        # Execute the disconnect tool and return a structured disconnect event.
        try:
            tool_input = tool_use_content.get("input", {})
            message = tool_input.get("message", "Goodbye! Feel better soon.")
            
            # Return structured disconnect event with 7-second timer
            result = {
                "status": "disconnecting",
                "message": message,
                "disconnect_delay_seconds": 7,
                "action": "close_connection",
                "reason": "User initiated disconnect"
            }
            
            return {
                "result": json.dumps(result)
            }
        
        except Exception as e:
            return {
                "result": json.dumps({
                    "status": "error",
                    "message": f"Disconnect error: {str(e)}",
                    "error": str(e)
                })
            }
