"""Isolated fixtures for identity/context expansion tasks; never model outputs."""
from creatoros.storage import TopicRemoval, TopicSource


def seed(world, case):
    identifier = case.get('id')
    if case.get('scenario') != 'identity_expansion' or identifier not in {'E01','E02','E03','E04','E05','E06','E07','E08'}:
        return
    def topic(identifier, title, brief, series='bench-english'):
        world.content.add_topic(topic_id=identifier, series_id=series, title=title,
                                brief=brief, source=TopicSource.MANUAL)
    if identifier == 'E01':
        topic('identity-archived', '周末出游', '之前的归档版本')
        topic('identity-active', '周末出游', '当前有效版本')
        with world.database.session() as session:
            session.add(TopicRemoval(topic_id='identity-archived', series_id='bench-english', creator_id='bench-a',
                                     request_id='seed-identity-archive', status='archived'))
    elif identifier == 'E02':
        topic('identity-weather-short', '天气变化', '基础主题')
        topic('identity-weather-long', '天气变化进阶', '进阶主题')
    elif identifier == 'E03':
        topic('identity-office', '借用表达', '办公室同事借用文具')
        topic('identity-library', '借用表达', '图书馆同学借书')
    elif identifier == 'E08':
        world.content.create_series(series_id='identity-short-queue', creator_id='bench-a',
            name='短队列', description='隔离测试栏目', audience='', skill_name=world.variables['skill_a'])
        topic('identity-queue-first', '借入', '第一条原始切入点', 'identity-short-queue')
        topic('identity-queue-second', '借出', '第二条原始切入点', 'identity-short-queue')
