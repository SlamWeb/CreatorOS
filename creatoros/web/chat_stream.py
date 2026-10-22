"""Incremental presentation transport; business versions and ledgers stay intact."""
from copy import deepcopy


def session_patch(previous, current):
    """Build an ordered patch; a new connection always starts from a full snapshot."""
    patch = {"id": current["id"], "base_revision": previous["stream_revision"],
             "stream_revision": current["stream_revision"], "entries_upsert": []}
    before = {entry["id"]: entry for entry in previous["entries"]}
    for entry in current["entries"]:
        old = before.get(entry["id"])
        if old == entry:
            continue
        if old is None:
            patch["entries_upsert"].append(deepcopy(entry))
            continue
        changes = {key: deepcopy(value) for key, value in entry.items() if old.get(key) != value}
        if (entry.get("kind") == "assistant" and isinstance(old.get("text"), str)
                and isinstance(entry.get("text"), str) and entry["text"].startswith(old["text"])
                and entry["text"] != old["text"]):
            patch.setdefault("entries_text_append", []).append({"id": entry["id"],
                                                               "text": entry["text"][len(old["text"]):]})
            changes.pop("text", None)
        if changes:
            patch["entries_upsert"].append({"id": entry["id"], **changes})
    order = [entry["id"] for entry in current["entries"]]
    if order != [entry["id"] for entry in previous["entries"]]:
        patch["entry_order"] = order
    for key, value in current.items():
        if key not in {"id", "stream_revision", "entries"} and previous.get(key) != value:
            patch[key] = deepcopy(value)
    return patch
