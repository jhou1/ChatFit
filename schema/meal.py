import datetime
from typing import Optional, Literal
from pydantic import BaseModel, Field

class MealRecord(BaseModel):
    date: datetime.date = Field(description="Date of your training")
    meal_type: Optional[Literal["breakfast", "lunch", "dinner", "snack", "extra"]] = Field(default=None, description="Type of the meal")
    items: Optional[str] = Field(default=None, description="food eaten")
    note: str = Field(description="Your diet, be descriptive, record your breakfast, lunch, dinner, extra meals, etc. Your future self will thank you.")
