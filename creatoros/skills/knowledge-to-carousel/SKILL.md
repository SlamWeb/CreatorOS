---
name: knowledge-to-carousel
description: Turn one knowledge topic into an original, beginner-friendly, publish-ready image carousel with big visuals and few words.
---

# Knowledge to Carousel

Explain the given topic for someone with no prior knowledge as one publish-ready image carousel.

- Find one simple visual metaphor that preserves the concept's truth; simplify the explanation, not the concept.
- Prefer big pictures, few words, generous whitespace, warm paper tones, hand-drawn dark outlines, and soft low-saturation accents.
- Create an original visual system. Do not copy a named creator's exact composition, lettering, characters, or signature style.
- Use only as many cards as the topic needs. Build a clear arc: curiosity → intuition → mechanism → takeaway.
- Keep each card understandable at phone size and keep the whole carousel visually consistent.
- Follow the caller's handoff mode. In filesystem mode, write the final images and `social_content_pack.json` into the supplied output directory. In receipt mode, generate the final images and return their real source paths in the requested structured receipt; the caller materializes the manifest.
- Follow [the SocialContentPack contract](references/social-content-pack.md). Do not return an HTML page or only describe images.

If essential facts are uncertain, research them before production and record the useful sources in the manifest. Otherwise, produce the final pack directly.
