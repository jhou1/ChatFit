from llm_factory.llm_factory import LLMConfig, create_chat_model
from agent.agent import FitnessAgent

def main():
    llm_config = LLMConfig(
        provider="google",
        model_name="gemini-3.5-flash",
        temperature=0.5,
        max_tokens=2048
    )
    fitness_agent = FitnessAgent(llm_config)

    print('Agent initialized')
    while True:
        user_input = input("User: ")
        if user_input.lower() == 'quit':
            break

        response = fitness_agent.chat(user_input)
        print(response)


if __name__ == "__main__":
    main()
