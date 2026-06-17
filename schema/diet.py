
import datetime
from pydantic import BaseModel, Field

class Diet(BaseModel):
    date: datetime.date = Field(description="Date of your training")
    note: str = Field(description="Your diet, be descriptive, record your breakfast, lunch, dinner, extral meals, etc. Your future self will thank you.")
