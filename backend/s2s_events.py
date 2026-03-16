class S2sEvent:
    # Default configuration values
    DEFAULT_INFER_CONFIG = {"maxTokens": 1024, "topP": 0.95, "temperature": 0.3}
    
    # Turn detection configuration - MEDIUM sensitivity for balanced turn-taking
    DEFAULT_TURN_DETECTION = {
        "endpointingSensitivity": "MEDIUM"  # MEDIUM = 1.75s pause (balanced)
    }

    DEFAULT_AUDIO_INPUT_CONFIG = {
        "mediaType": "audio/lpcm",
        "sampleRateHertz": 16000,
        "sampleSizeBits": 16,
        "channelCount": 1,
        "audioType": "SPEECH",
        "encoding": "base64",
    }
    DEFAULT_AUDIO_OUTPUT_CONFIG = {
        "mediaType": "audio/lpcm",
        "sampleRateHertz": 24000,
        "sampleSizeBits": 16,
        "channelCount": 1,
        "voiceId": "matthew",
        "encoding": "base64",
        "audioType": "SPEECH",
    }

    @staticmethod
    def get_system_prompt():
        # Load system prompt from file.
        import os
        prompt_file = os.path.join(os.path.dirname(__file__), "system_prompt.txt")
        try:
            with open(prompt_file, "r", encoding="utf-8") as f:
                return f.read().strip()
        except FileNotFoundError:
            # Fallback if file doesn't exist
            return (
                "You are a friendly assistant. The user and you will engage in a spoken dialog "
                "exchanging the transcripts of a natural real-time conversation. Keep your responses short, "
                "generally two or three sentences for chatty scenarios."
            )
    
    @staticmethod
    def get_default_tool_config(stage="identity"):
        # Get tool configuration from tool registry.
        from tools import get_all_tool_specs
        
        # Don't force tool choice - let Nova Sonic decide based on context
        # The system prompt will guide which tool to use
        return {"tools": get_all_tool_specs()}
    
    # Keep for backward compatibility
    DEFAULT_TOOL_CONFIG = None  # Will be set dynamically

    @staticmethod
    def session_start(inference_config=DEFAULT_INFER_CONFIG, turn_detection_config=None):
        # Create sessionStart event with inference and turn detection configuration.
        if turn_detection_config is None:
            turn_detection_config = S2sEvent.DEFAULT_TURN_DETECTION
            
        return {
            "event": {
                "sessionStart": {
                    "inferenceConfiguration": inference_config,
                    "turnDetectionConfiguration": turn_detection_config
                }
            }
        }

    @staticmethod
    def prompt_start(
        prompt_name,
        audio_output_config=DEFAULT_AUDIO_OUTPUT_CONFIG,
        tool_config=None,
        stage="identity",
    ):
        if tool_config is None:
            tool_config = S2sEvent.get_default_tool_config(stage)
        return {
            "event": {
                "promptStart": {
                    "promptName": prompt_name,
                    "textOutputConfiguration": {"mediaType": "text/plain"},
                    "audioOutputConfiguration": audio_output_config,
                    "toolUseOutputConfiguration": {"mediaType": "application/json"},
                    "toolConfiguration": tool_config,
                }
            }
        }

    @staticmethod
    def content_start_text(prompt_name, content_name):
        return {
            "event": {
                "contentStart": {
                    "promptName": prompt_name,
                    "contentName": content_name,
                    "type": "TEXT",
                    "interactive": False,
                    "role": "SYSTEM",
                    "textInputConfiguration": {"mediaType": "text/plain"},
                }
            }
        }

    @staticmethod
    def text_input(prompt_name, content_name, system_prompt=None):
        if system_prompt is None:
            system_prompt = S2sEvent.get_system_prompt()
        return {
            "event": {
                "textInput": {
                    "promptName": prompt_name,
                    "contentName": content_name,
                    "content": system_prompt,
                }
            }
        }

    @staticmethod
    def content_end(prompt_name, content_name):
        return {
            "event": {
                "contentEnd": {"promptName": prompt_name, "contentName": content_name}
            }
        }

    @staticmethod
    def content_start_audio(
        prompt_name, content_name, audio_input_config=DEFAULT_AUDIO_INPUT_CONFIG
    ):
        return {
            "event": {
                "contentStart": {
                    "promptName": prompt_name,
                    "contentName": content_name,
                    "type": "AUDIO",
                    "interactive": True,
                    "role": "USER",
                    "audioInputConfiguration": audio_input_config,
                }
            }
        }

    @staticmethod
    def audio_input(prompt_name, content_name, content):
        return {
            "event": {
                "audioInput": {
                    "promptName": prompt_name,
                    "contentName": content_name,
                    "content": content,
                }
            }
        }

    @staticmethod
    def content_start_tool(prompt_name, content_name, tool_use_id):
        return {
            "event": {
                "contentStart": {
                    "promptName": prompt_name,
                    "contentName": content_name,
                    "interactive": False,
                    "type": "TOOL",
                    "role": "TOOL",
                    "toolResultInputConfiguration": {
                        "toolUseId": tool_use_id,
                        "type": "TEXT",
                        "textInputConfiguration": {"mediaType": "text/plain"},
                    },
                }
            }
        }

    @staticmethod
    def text_input_tool(prompt_name, content_name, content):
        return {
            "event": {
                "toolResult": {
                    "promptName": prompt_name,
                    "contentName": content_name,
                    "content": content,
                    # "role": "TOOL"
                }
            }
        }

    @staticmethod
    def prompt_end(prompt_name):
        return {"event": {"promptEnd": {"promptName": prompt_name}}}

    @staticmethod
    def session_end():
        return {"event": {"sessionEnd": {}}}
