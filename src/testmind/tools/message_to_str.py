from langchain.messages import AnyMessage


def format_recent_messages(messages: list[AnyMessage]) -> str:
    """将langchain的信息形式转为字符串"""
    recent_messages = messages[-5:] if len(messages) > 5 else messages

    lines = []
    for message in recent_messages:
        role = message.type
        content = message.content
        lines.append(f"{role}: {content}")

    return "\n".join(lines)