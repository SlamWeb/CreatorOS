from io import StringIO

from creatoros.ai.context import ContextBudget, ModelContext
from creatoros.events import AgentEvent
from creatoros.terminal import Console, RichConsole


def main():
    context = ModelContext.from_messages(
        [{"role": "system", "content": "stable"}, {"role": "user", "content": "hello"}],
        [{"type": "function", "function": {"name": "read_file"}}],
    )
    budget = ContextBudget.from_context(
        context, context_window=1000, reserve_output_tokens=200
    )
    assert budget.estimated_input_tokens > 0
    assert budget.input_limit == 800
    assert budget.remaining_tokens == 800 - budget.estimated_input_tokens
    assert not budget.needs_attention

    over = ContextBudget(100, 20, 90)
    assert over.is_over_limit
    assert over.needs_attention
    output = StringIO()
    Console(output=output).render_event(AgentEvent("context_warning", over.to_event_data()))
    assert "Context" in output.getvalue()
    assert "超出预算" in output.getvalue()
    rich_output = StringIO()
    RichConsole(output=rich_output).render_event(
        AgentEvent("context_warning", over.to_event_data())
    )
    assert "超出预算" in rich_output.getvalue()
    print("context_budget_smoke=passed")


if __name__ == "__main__":
    main()
