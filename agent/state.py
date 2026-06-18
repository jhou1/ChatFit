from typing import Annotated, Optional, List, TypedDict
from pydantic import BaseModel, Field

from langgraph.graph.message import add_messages

class UserProfile(BaseModel):
    """User profile schema with typed fields"""

    username: str
    diet_preference: Optional[List[str]] = Field(description="User's diet preference")
    training_preference: Optional[List[str]] = Field(description="User's training preference")

class AgentState(TypedDict):
    messages: Annotated[list, add_messages]
    next_agent: str
    user_profile: UserProfile
