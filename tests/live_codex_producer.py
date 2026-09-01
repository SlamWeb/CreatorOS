import argparse

from creatoros.integrations.codex import CodexProducer


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--topic-id", default="http-404")
    parser.add_argument("--topic-title", default="HTTP 404 到底是什么意思")
    args = parser.parse_args()
    produced = CodexProducer.from_defaults().produce(
        creator_id="creatoros-lab",
        series_id="knowledge-cards",
        topic_id=args.topic_id,
        topic_title=args.topic_title,
    )
    usage = produced.session.usage
    print(
        "live_codex_producer=passed "
        f"cards={len(produced.pack.cards)} thread={produced.session.thread_id} "
        f"input={usage.input_tokens} cached={usage.cached_input_tokens} "
        f"output={usage.output_tokens} directory={produced.directory}"
    )


if __name__ == "__main__":
    main()
