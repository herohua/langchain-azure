import os

from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import CustomCredential
from azure.identity import DefaultAzureCredential


def load_tool_api_key() -> str:
    with (
        DefaultAzureCredential() as credential,
        AIProjectClient(
            endpoint=os.environ["FOUNDRY_PROJECT_ENDPOINT"],
            credential=credential,
        ) as project,
    ):
        connection = project.connections.get(
            os.environ["TOOL_CONNECTION_NAME"], include_credentials=True
        )
        credentials = connection.credentials
        if not isinstance(credentials, CustomCredential):
            raise ValueError("The tool connection must use CustomKeys credentials.")
        api_key = credentials.credential_keys.get("api_key")
        if not api_key:
            raise ValueError("The tool connection needs an api_key secret field.")
        return api_key
