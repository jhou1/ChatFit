from llm_factory.llm_factory import LLMConfig, create_chat_model

def test_create_google_chat_client():
    llm_config = LLMConfig(
        provider="google",
        model_name="gemini-3.5-flash",
        api_key="api",
        temperature=0.5,
        max_tokens=2048
    )
    create_chat_model(llm_config)

def test_create_google_llm_config_with_proxy():
    llm_config = LLMConfig(
        provider="google",
        model_name="gemini-3.5-flash",
        api_key="api",
        temperature=0.5,
        max_tokens=2048,
        kwargs={"client_args": {"proxy": "socks5://127.0.0.1:8990"}}
    )
    create_chat_model(llm_config)

def test_create_openai_chat_client():
    llm_config = LLMConfig(
        provider="openai",
        api_key="api",
        model_name="gpt-5.5",
        temperature=0.1
    )
    create_chat_model(llm_config)

def test_create_anthropic_chat_client():
    llm_config = LLMConfig(
        provider="anthropic",
        api_key="api",
        model_name="opus-4.6",
        temperature=0.2,
        max_tokens=1020
    )
    create_chat_model(llm_config)
