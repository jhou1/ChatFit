import json
import re
from datetime import datetime

from langchain_core.prompts.prompt import PromptTemplate
from langchain_core.tools import tool
from langchain_core.messages import SystemMessage

from langgraph.graph import StateGraph, START
from langgraph.prebuilt import ToolNode, tools_condition

from agents.models import AgentState, TrainingInputRecorder
from agents.sqlite_handler import add_training_session, get_training_sessions_of_last_n_days
from agents.llm_factory import create_chat_model, LLMConfig

INSTRUCTION_FOR_RECORDING_TRAINING_SESSIONS = """
You are a highly capable assistant helping users track their fitness training sessions.

Use the following list of acronyms to understand user's descriptions, and then expand those acronyms to save user's training records. If users used an acronyms or terminology you don't understand, do not guess it, ask the clarification.

Acronyms
- Practice: a practice is a training item, or what the general public call "exercise". All kinds of trainings are skill training, so using the term 'practice' takes a more serious approach.
- OTM: is short for "On The Minute". It means user starts the practice at the start of the minute, and take the rest during the rest duration of the minute. Repeats the practice every minute. e.g. 5 kettlebell snatches OTM x 20 describes doing 5 kettlebell snatches at the start of every minute and repeat for 20 sets.
- 1w1r: is short for 1 work and 1 rest. This is the kind of training cadence that you work for one minute, rest another minute, and repeat. e.g. 15 kettlebell long cycle 1w1r x 10 describes practicing 15 kettlebell long cycle in 1 minute and rest another minute, repeat for 10 sets, spent a total of 20 minutes.

When a user describes their trainings/practices/workouts/exercises, you MUST perform semantic matching and translation against the exact list of existing practices.
1. Extract the practice(exercises), date, sets, reps, weights, duration, distance, rpe, warm up, cool down and notes from user input.
  - In `practices` table, record each practice with the name, type(weighted, duration, distance, bodyweight).
  - In `training_sessions` table, record each session with the date, warm up, cool down, rpe and note. You must take the full user input text as note even if user's input has line breaks. If user provided multiple trainings in one input session, you reuse them as notes for every training session.
  - In `training_sets` table, record each set with reps, weight, distance, duration. Weight must be provided when the training type is weighted.
2. Attempt to ask one more time for to fill all the columns in these tables, user may forget to provide RPE, warm up, cool down, etc.
4. ONLY introduce a new practice name if it genuinely has no semantic equivalent in the existing list, assign it a type (endurance, distance, weighted, bodyweight). You have access to the tool `normalize_practice_name`, which can often help you normalize user's practice to standard names in the database. If you are unsure whether the name is a semantic match, ask user for clarification.
5. Call the `log_training_session` tool with `confirm_new_practices=False`.
6. IMPORTANT: If the tool returns an Error stating that practices are missing, you MUST STOP and ask the user for permission to create them. DO NOT call the tool again yet.
7. Once the user approves, call the `log_training_session` tool again with `confirm_new_practices=True`.
8. If user mentions a date or a relative date(yesterday, last Monday, etc) of the training session, use it as the date for the training_session, otherwise use {current_time}
9. ALWAYS reply to the user with a text message. If the tool call succeeds, confirm it. If it fails, explain the error. NEVER output an empty message.

Be concise and supportive. Your goal is to cleanly save all structured data into the database.
"""

INSTRUCTION_FOR_RETRIEVING_TRAINING_SESSIONS = """
You are an fitness and training assistant.

Your job is to retrieve training/workout session logs from the user's database and explain them with natural language. When the user asks you about their training records, you will call the `get_training_sessions` tool to retrieve the logs. The tool takes in "num_of_days" as parameter that returns the past number of days of training records. If user's question contains the days they are interested in, make use of this parameter. If this parameter is not provided, retrieve logs of the past 7 days.

The training session log attributes you will explain includes:
- date: The date of the workout. If not specified, use {current_time}.
- practice_name: The main exercise or activity (e.g., 'Running', 'Weightlifting').
- practice_type: The type of the practice(weighted, bodyweight, distance, duration)
- warm_up / cool_down: Any specific warm up or cool down activities mentioned.
- distance: Distance in km
- duration: Duration in minutes.
- reps / sets / weight: For strength training.
- rpe: Rate of Perceived Exertion (1-10 scale).
- training volumes: you compute the training volume by multiplying weight/distance/duration with sets and reps
- note: The user's full input as a descriptive note, capturing the overall vibe and any gear used.

You do not have to explain empty or null values of attributes, if user asked for those values, tell user they are empty.

Finally, provide a summary of the training logs on the training volume, training intensity(rpe), and training focus(cardio, strength, power, endurace, etc).
"""


class PracticeNameNormalizer:
    """
    Normalize input acronums, semantic similar names, Chinese/English names
    """

    def __init__(self, config_path: str = "config/synonyms.json"):
        with open(config_path, "r", encoding="utf-8") as f:
            self.config = json.load(f)

        # reverse index aliases and keys
        self.alias_to_key = {}
        for k, v in self.config.items():
            for alias in [k] + v["aliases"]:
                self.alias_to_key[alias.lower()] = k

    def normalize(self, practice: str) -> str:
        search_key = re.sub(r"[.-]", " ", practice.lower().strip())
        if search_key in self.alias_to_key:
            return self.alias_to_key[search_key]

        # fuzzy match
        sorted_aliases = sorted(self.alias_to_key.keys(), key=len, reverse=True)
        for alias in sorted_aliases:
            if alias in search_key:
                return self.alias_to_key[alias]

        return None

# make agent graph
def make_training_agent_graph(llm_config: LLMConfig, db_path: str):
    @tool
    def normalize_practice_name(user_input: str) -> str:
        """Normalize the user input practice and return the standard name"""

        normalizer = PracticeNameNormalizer("config/synonyms.json")
        return normalizer.normalize(user_input)

    @tool(args_schema=TrainingInputRecorder)
    def log_training_session(**kwargs):
        """Add the user training log to db."""

        input_data = TrainingInputRecorder(**kwargs)
        return add_training_session(input_data, db_path)

    @tool
    def retrieve_training_sessions(num_of_days: int):
        """Get a list of training sessions of the last n days"""
        return get_training_sessions_of_last_n_days(num_of_days, db_path)



    llm = create_chat_model(llm_config)
    llm_with_tools = llm.bind_tools([normalize_practice_name,
                                     log_training_session,
                                     retrieve_training_sessions
                                     ])

    def log_training_node(state: AgentState):
        prompt_template = PromptTemplate.from_template(INSTRUCTION_FOR_RECORDING_TRAINING_SESSIONS)
        system_prompt = prompt_template.format(
            current_time=datetime.now().isoformat()
        )

        messages = [SystemMessage(content=system_prompt)] + state["messages"]
        response = llm_with_tools.invoke(messages)
        return {"messages": response}

    def retrieve_training_node(state: AgentState):
        system_message = SystemMessage(content=INSTRUCTION_FOR_RETRIEVING_TRAINING_SESSIONS)
        messages = [SystemMessage(content=system_message)] + state["messages"]
        response = llm_with_tools.invoke(messages)
        return {"message": response}

    builder = StateGraph(AgentState)
    builder.add_node("log_training_node", log_training_node)
    builder.add_node("retrieve_training_node", retrieve_training_node)
    tool_node = ToolNode(tools=[normalize_practice_name,
                                log_training_session,
                                retrieve_training_sessions])
    builder.add_node("tools", tool_node)

    builder.add_edge(START, "log_training_node")
    builder.add_conditional_edges("log_training_node", tools_condition)
    builder.add_conditional_edges("retrieve_training_node", tools_condition)
    builder.add_edge("tools", "log_training_node")

    return builder.compile()
