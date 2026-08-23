from copy import deepcopy
from dataclasses import dataclass


_STABLE_ROLES = {"system", "developer"}


@dataclass(frozen=True)
class ModelContext:
    """Read-only request snapshot assembled for one model turn."""

    system_messages: tuple[dict, ...]
    tools: tuple[dict, ...]
    messages: tuple[dict, ...]

    @classmethod
    def from_messages(cls, messages, tools):
        system_messages = []
        conversation = []
        in_conversation = False
        for message in messages:
            copied = deepcopy(message)
            if not in_conversation and copied.get("role") in _STABLE_ROLES:
                system_messages.append(copied)
            else:
                in_conversation = True
                conversation.append(copied)
        return cls(
            system_messages=tuple(system_messages),
            tools=tuple(deepcopy(tool) for tool in tools),
            messages=tuple(conversation),
        )

    def to_request(self) -> tuple[list[dict], list[dict]]:
        request_messages = [
            deepcopy(message)
            for message in (*self.system_messages, *self.messages)
        ]
        request_tools = [deepcopy(tool) for tool in self.tools]
        return request_messages, request_tools
