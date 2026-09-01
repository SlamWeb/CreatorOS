from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import shutil

from pydantic import ValidationError

from creatoros.content import SocialContentPack


EXAMPLE = (
    Path(__file__).parents[1]
    / "examples"
    / "social_content_pack"
    / "agent-interview"
    / "agent-knowledge"
    / "20260902-agent-state"
)


def expect_validation_error(payload: dict, text: str) -> None:
    try:
        SocialContentPack.model_validate(payload)
    except ValidationError as exc:
        assert text in str(exc)
    else:
        raise AssertionError(f"预期校验失败：{text}")


def main() -> None:
    pack = SocialContentPack.load(EXAMPLE)
    assert pack.platform == "xiaohongshu"
    assert len(pack.cards) == 3
    assert pack.cards[-1].order == 3

    payload = pack.model_dump(mode="json")
    broken_order = deepcopy(payload)
    broken_order["cards"][1]["order"] = 3
    expect_validation_error(broken_order, "连续编号")

    unsafe_path = deepcopy(payload)
    unsafe_path["cards"][0]["image_path"] = "../card-01.png"
    expect_validation_error(unsafe_path, "安全相对路径")

    with TemporaryDirectory() as directory:
        copied = Path(directory) / "pack"
        shutil.copytree(EXAMPLE, copied)
        (copied / pack.cards[0].image_path).unlink()
        try:
            SocialContentPack.load(copied)
        except FileNotFoundError as exc:
            assert pack.cards[0].image_path in str(exc)
        else:
            raise AssertionError("缺少图片时应该读取失败。")

    print("social_content_pack_smoke=passed cards=3")


if __name__ == "__main__":
    main()
