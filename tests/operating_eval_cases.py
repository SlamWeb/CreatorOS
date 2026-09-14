"""Versioned synthetic operating tasks; IDs and wording are test-only fixtures."""
DATASET = "creator-operating-v1"
CASE_VERSION = 2

DEV = (
    {"id": "D01", "goal": "select_second", "selection": ["c2"], "prompt": "查询栏目 {series} 的待选选题，选择列表第二条生成入队预览，先不要确认或生产。批次 {batch} 供你核对。"},
    {"id": "D02", "goal": "same_name", "selection": ["c3"], "series": "series-b", "prompt": "在编程手记账号的每日图解AI栏目中，选择第三个待选项做预览，先不要确认。"},
    {"id": "D03", "goal": "bulk_exclude", "selection": ["c1", "c2", "c4"], "prompt": "批次 {batch} 除第三条外都选，保留顺序，把第一条标题改为‘边界内的工具调用’，生成预览，不确认。"},
    {"id": "D04", "goal": "changed_mind", "selection": ["c1"], "prompt": "先看批次 {batch} 的四条并将前两条做预览。", "followup": "改一下，只留第一条，标题改为‘重新理解选题路由’，重新生成预览但别确认。"},
    {"id": "D05", "version": 3, "goal": "read_only", "selection": [], "prompt": "请只列出栏目 {series} 尚未入队的选题标题和来源。不要列出或重复已入队选题的标题，只看看，不要生成预览。批次是 {batch}。"},
    {"id": "D06", "goal": "resume_preview", "selection": [], "prompt": "请查看会话里上次为批次 {batch} 建好的预览，把同一个预览链接给我，不要再建。"},
)

HOLDOUT = (
    {"id": "H01", "goal": "explicit_order", "selection": ["c4", "c2"], "prompt": "批次 {batch} 四条里留末条和第二条，先放末条，按这个顺序给我预览。"},
    {"id": "H02", "goal": "clarify", "selection": [], "prompt": "两个账号的每日图解AI栏目都有待选列表，帮我把第二条放进去。"},
    {"id": "H03", "goal": "changed_mind", "selection": ["c3"], "prompt": "一开始说第一条，撤回，改用第三条，标题写‘表达方式的边界’，其他信息照旧，先出预览。"},
    {"id": "H04", "goal": "already_queued", "selection": [], "prompt": "栏目 {series} 的第一条已经安排过了。查一下它现在状态，不要重复添加或生产。"},
    {"id": "H05", "goal": "stale", "selection": [], "prompt": "批次 {batch} 的栏目定位已经更新了，按第二条做预览，别重新调研。"},
    {"id": "H06", "goal": "compacted_progress", "selection": ["c2"], "prompt": "继续上次未完成的部分：仅给第二条做预览，第一条已有预览不要重复，不确认、不生产。"},
)

CASES = DEV + HOLDOUT


def validate_cases():
    assert len(DEV) == len(HOLDOUT) == 6
    assert len({case["id"] for case in CASES}) == 12
    assert all(case["goal"] and case["prompt"] and isinstance(case["selection"], list) for case in CASES)
    assert all(set(case["selection"]) <= {f"c{i}" for i in range(1, 5)} for case in CASES)
    assert CASES[0]["id"] == "D01" and CASES[0]["selection"] == ["c2"]
    return True
