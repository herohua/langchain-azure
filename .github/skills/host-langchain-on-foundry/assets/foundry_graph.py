from agent import build_agent
from langchain_azure_ai.agents.hosting import FoundryCheckpointSaver


def create_graph():
    return build_agent(
        checkpointer=FoundryCheckpointSaver(user_isolation=True),
    )
