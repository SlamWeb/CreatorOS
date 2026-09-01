# SocialContentPack output contract

The caller supplies `creator_id`, `series_id`, `topic_id`, `topic_title`, and an output directory. Treat these values as fixed identifiers, not creative suggestions.

Write exactly one manifest named `social_content_pack.json` plus every referenced image into that directory. The manifest must match this shape:

```json
{
  "schema_version": 1,
  "pack_id": "creator-series-topic-date",
  "creator_id": "creator-id",
  "series_id": "series-id",
  "topic_id": "topic-id",
  "topic_title": "Topic title",
  "skill_name": "knowledge-to-carousel",
  "generated_at": "ISO-8601 timestamp",
  "platform": "xiaohongshu",
  "content_summary": "One-sentence summary",
  "cards": [],
  "publish_copy": {"title": "", "body": "", "hashtags": []},
  "sources": []
}
```

Each `cards` item contains `order`, `kind`, optional `section`, `headline`, optional `body`, `highlights`, optional `visual_brief`, and `image_path`. Number cards consecutively from 1. Use a different safe relative image path for every card.

Allowed card kinds are `cover`, `content`, `summary`, `sources`, and `cta`. Sources contain `source_id`, `title`, optional `url`, and optional `note`.

Before finishing, confirm that every image path exists, the carousel is readable in sequence, the copy does not promise facts unsupported by the content, and the manifest can be parsed without guessing missing fields.
