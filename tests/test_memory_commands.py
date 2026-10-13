from agents.memory.commands import parse_memory_command


def test_remember_payload_strips_command_delimiters():
    assert parse_memory_command("记住，我不吃香菜").payload == "我不吃香菜"
    assert parse_memory_command("记住：我不吃香菜").payload == "我不吃香菜"
    assert parse_memory_command("我不吃香菜，记下来").payload == "我不吃香菜"
