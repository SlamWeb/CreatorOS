from pathlib import Path
from tempfile import TemporaryDirectory

from creatoros.integrations.codex import CodexProducer


PROMPT = """
Do not call tools or create files. Return one valid ProductionReceipt JSON.
Use content_summary='protocol probe', one cover card with order=1,
headline='probe', highlights=[], source_image_path='D:/probe.png',
publish_copy title/body='probe' and empty hashtags/sources.
""".strip()


with TemporaryDirectory() as temporary:
    directory = Path(temporary)
    producer = CodexProducer(
        project_root=directory,
        generated_images_root=directory,
        timeout_seconds=300,
    )
    observed: list[str] = []
    first = producer._execute(PROMPT, directory, on_thread_started=observed.append)
    assert observed == [first.thread_id]
    second = producer._execute(
        PROMPT,
        directory,
        thread_id=first.thread_id,
        on_thread_started=lambda thread_id: observed.append(thread_id),
    )
    assert second.thread_id == first.thread_id
    assert observed[-1] == first.thread_id

print(f"live_codex_resume_protocol=passed thread={first.thread_id}")
