from langchain_core.messages import BaseMessage
from typing import Union, List, Dict, Any

def extract_text(message_or_content: Union[BaseMessage, str, List[Dict[str, Any]], Any]) -> str:
    """
    Safely extract text from a LangChain message or content field.
    Handles both OpenAI's string content and Gemini's list-based content.
    Appends a truncation warning if the message was cut off by max_tokens limit.
    """
    is_message = isinstance(message_or_content, BaseMessage)
    content = message_or_content.content if is_message else message_or_content

    result_text = ""
    if isinstance(content, str):
        result_text = content
    elif isinstance(content, list):
        result_text = "".join(part.get("text", "") for part in content if isinstance(part, dict) and "text" in part)
    else:
        result_text = str(content)

    if is_message and hasattr(message_or_content, "response_metadata"):
        metadata = message_or_content.response_metadata or {}
        # Handle cases where finish_reason might be missing or None
        finish_reason = str(metadata.get("finish_reason", "")).upper()
        if finish_reason in ["MAX_TOKENS", "LENGTH"]:
            result_text += "\n\n[OUTPUT TRUNCATED: text exceeds maximum length.]"

    return result_text
