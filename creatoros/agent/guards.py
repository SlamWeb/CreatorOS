DEFAULT_MAX_TURNS = 30


class MaxTurnGuard:
    def __init__(self, max_turns: int = DEFAULT_MAX_TURNS):
        if max_turns < 1:
            raise ValueError("max_turns 必须至少为 1。")
        self.max_turns = max_turns

    def should_stop(self, turns_used: int) -> bool:
        return turns_used >= self.max_turns
