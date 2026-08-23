import os

from .agent.loop import run_agent
from .ai.deepseek import DeepSeekProvider
from .ai.provider import ModelProvider
from .terminal import RichConsole


def main():
    console = RichConsole()
    console.banner()
    provider: ModelProvider = DeepSeekProvider(
        api_key=os.environ["DEEPSEEK_API_KEY"]
    )
    run_agent(provider, console=console)
