"""Synthetic, fixed-ID development cases, not sampled real user traffic."""
CASES = ("early_constraint", "changed_mind", "same_name", "evidence", "long_loop", "restart")
ARMS = ("full", "recent", "compact")


def user(text):
    return {"role": "user", "content": text}


def assistant(text):
    return {"role": "assistant", "content": text}


def exchange(name, call_id, content, arguments="{}"):
    return [{"role": "assistant", "content": None, "tool_calls": [
        {"id": call_id, "name": name, "arguments": arguments}]},
        {"role": "tool", "tool_call_id": call_id, "content": content}]


def fixture(case, batch_a, batch_b, existing):
    a, b = batch_a["id"], batch_b["id"]
    old = {
        "early_constraint": [user(f"记录批次{a}的第二条。我只要它的标题，不要建预览，等我叫你继续。")],
        "changed_mind": [user(f"批次{a}第一条稍后做预览。"), assistant("已记录。"),
                         user("改一下，不要第一条，换第二条，标题改成‘上下文需要边界’，切入点和来源不变。")],
        "same_name": [user(f"两个账号都有每日图解AI。选编程手记账号的栏目series-b、批次{b}第二条做预览，不是知识实验室。")],
        "evidence": [user("保留这份历史资料，稍后从中取指定标题。"),
                     *exchange("read_file", "evidence", "x" * 8000 +
                               "\n指定标题：上下文预算实验-Z924\n" + "y" * 8000)],
        "long_loop": [user(f"把批次{a}的两条分别做成两个独立预览，不要确认。同一候选已做过就不要再创建。"),
                      *exchange("prepare_topic_selection", "already-done", existing,
                                '{"batch_id":"' + a + '","selections":[{"candidate_id":"c1"}]}')],
        "restart": [user(f"给批次{a}第一条生成预览，先不确认。"),
                    *exchange("prepare_topic_selection", "pending-preview", existing,
                              '{"batch_id":"' + a + '","selections":[{"candidate_id":"c1"}]}'),
                    user("刚才的预览还没有确认。不要再创建新预览或生产，重启后给我已有预览的链接即可。")],
    }[case]
    # Synthetic background, with no extra instructions or hidden correct choices.
    noise = assistant("背景资料（合成填充，不代表新决定）：排版可以使用留白和清晰标题。" * 180)
    if case == "long_loop":
        stages = [old + [noise, assistant("已检查资料，任务仍在处理中。")],
                  [noise, assistant("背景检查结束，继续未完成部分。")]]
    else:
        stages = [old + [noise, user("先暂停一下，保留当前进度。"), assistant("等待继续。")]]
        if case == "restart":
            stages.append([noise, user("仍按之前约定，暂停。"), assistant("等待恢复。")])
    prompt = (f"使用历史资料中的指定标题，给批次{a}第二条生成预览，切入点和来源不改。"
              if case == "evidence" else
              "重启后继续，把上次那个预览链接给我。" if case == "restart" else
              "继续刚才约定的任务。")
    return stages, prompt
