"""Synthetic fixtures for compound topic operations in a disposable world."""
from creatoros.storage import TopicSource


def seed(world, case):
    if case.get("scenario") != "extra_compound_topics":
        return
    fixtures = {
        "X01": [("extra-append", "问路用语", "场景：车站问路。")],
        "X02": [("extra-swap-a", "迟到表达", "办公室场景"), ("extra-swap-b", "道歉表达", "课堂场景")],
        "X03": [("extra-filter-a", "短语甲", "需校对：第一个"), ("extra-filter-b", "短语乙", "已经校对"), ("extra-filter-c", "短语丙", "需校对：第二个")],
        "X04": [],
    }
    for ident, title, brief in fixtures.get(case.get("id"), []):
        world.content.add_topic(topic_id=ident, series_id="bench-english", title=title,
                                brief=brief, source=TopicSource.MANUAL)
