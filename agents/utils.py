from langchain_core.messages import BaseMessage
from typing import Union, List, Dict, Any

def extract_text(message_or_content: Union[BaseMessage, str, List[Dict[str, Any]], Any]) -> str:
    """
    Safely extract text from a LangChain message or content field.
    Handles both OpenAI's string content and Gemini's list-based content.
    """
    content = message_or_content.content if isinstance(message_or_content, BaseMessage) else message_or_content
    
    if isinstance(content, str):
        return content
    elif isinstance(content, list):
        return "".join(part.get("text", "") for part in content if isinstance(part, dict) and "text" in part)
    return str(content)
