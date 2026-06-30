import datetime
from typing import Optional, Literal, TypedDict, Annotated
from pydantic import BaseModel, Field
from langgraph.graph.message import add_messages


# Track nutrition
class MealInfo(BaseModel):
    date: datetime.date = Field(description="Date of your meal")
    meal_type: Optional[Literal["breakfast", "lunch", "dinner", "snack", "extra"]] = Field(default=None, description="Type of the meal")
    items: Optional[str] = Field(default=None, description="food eaten")
    note: str = Field(description="Your diet, be descriptive, record your breakfast, lunch, dinner, extra meals, etc. Your future self will thank you.")


# Track Trainings
PracticeType = Literal["endurance", "distance", "weighted", "bodyweight"]

class TrainingSet(BaseModel):
    """TrainingSet tracks the sets performed during a training session"""
    set_number: int = Field(description="Sequential set number, starting from 1")
    weight: Optional[float] = Field(default=None, description="Weight used, in kg")
    reps: Optional[int] = Field(default=None, description="Number of reps performed")
    distance: Optional[float] = Field(default=None, description="Distance completed, in km")
    duration: Optional[float] = Field(default=None, description="Duration in minutes")

class TrainingSession(BaseModel):
    """TraningSession tracks the practice, sets, notes of a training"""

    practice_name: str = Field(description="Name of the practice (e.g., Kettlebell Snatch)")
    practice_type: PracticeType = Field(description="Categorization of the practice")
    rpe: Optional[int] = Field(default=None, description="Rate of perceived exertion(1~10)")
    warm_up: Optional[str] = Field(default=None, description="Warm up activities before the training session")
    cool_down: Optional[str] = Field(default=None, description="Cool down activities after the training session")
    sets: list[TrainingSet] = Field(description="List of sets performed for this practice")
    note: str = Field(description="Specific notes, RPE, or feelings about this practice")

class RecordTrainingInput(BaseModel):
    """Record user's training input
    If user specifies a new practice that does not exist, request explicit approval
    This prevents inconsistent data
    """

    date: datetime.date = Field(description="Date of the training session")
    sessions: list[TrainingSession] = Field(description="A list of practices and their sets performed in this session")
    confirm_new_practices: bool = Field(
        default=False,
        description="Set to True ONLY if the user has explicitly approved creating any new practices."
    )


# Agent
class AgentState(TypedDict):
    """The assistant to record meal and training info"""

    messages: Annotated[list, add_messages]
    assistant_names: list[str]
