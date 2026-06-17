from typing import Optional, List, Literal
from pydantic import BaseModel, Field
from llm_factory.llm_factory import LLMConfig, create_chat_model

from langchain_core.messages import HumanMessage

class UserProfile(BaseModel):
    """User profile schema with typed fields"""

    username: str
    diet_preference: Optional[List[str]] = Field(description="User's diet preference")
    training_preference: Optional[List[str]] = Field(description="User's training preference")

class AgentState(BaseModel):
    message: list
    user_profile: UserProfile


"""
class FitnessAgent:
    def __init__(self, llm_config: LLMConfig):
        self.llm = create_chat_model(llm_config)

    def chat(self, user_input):
        return self.llm.invoke([HumanMessage(content=user_input)])
"""
