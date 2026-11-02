"""Model-facing production actions; the existing REST DTOs stay independent."""
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field

from .argument_contract import ActionContract


ObjectId = Annotated[str, Field(min_length=1, max_length=80)]
SkillId = Annotated[str, Field(
    min_length=1, max_length=120,
    pattern=r"^(knowledge-to-carousel|[a-z0-9-]+--[a-f0-9]{16})$",
)]
SkillIds = Annotated[list[SkillId], Field(min_length=1, max_length=12)]


class ProductionArguments(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", str_strip_whitespace=True)


class CollectionInputs(ProductionArguments):
    collection_id: str = Field(min_length=1, max_length=128)
    export_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


class MaterialBgmInputs(CollectionInputs):
    bgm_material_id: str = Field(pattern=r"^[a-f0-9]{64}$", description="已有音频或视频的素材 ID。")


class ReferenceBgmInputs(CollectionInputs):
    bgm_reference: str = Field(min_length=1, max_length=10000,
        description="用户给的 BGM 分享原文或链接，直接交给 Codex；无需预解析或先导入。")


TaskInputs = Union[MaterialBgmInputs, ReferenceBgmInputs]


class ProductionTask(ProductionArguments):
    title: str = Field(min_length=1, max_length=240, description="该独立制作任务的简短名称。")
    brief: str = Field(min_length=1, max_length=10000,
        description="本项独立画面或视频的完整要求；独立理由图一图一项，25张填25项。")
    skill_ids: SkillIds | None = Field(default=None,
        description="本次使用的精确 Skill 版本 ID，可选择一项或多项；省略使用栏目对应阶段默认。")


class ImageProductionTask(ProductionTask):
    kind: Literal["image"]


class VideoProductionTask(ProductionTask):
    kind: Literal["video"]
    inputs: TaskInputs = Field(description="素材集及 BGM 素材ID或分享原文，两种 BGM 输入二选一。")
    depends_on: list[ObjectId] = Field(default_factory=list, max_length=100,
        description="用户明确选择的本选题生图任务 ID；后台等待完成，不轮询。")


ProductionTaskItem = Annotated[Union[ImageProductionTask, VideoProductionTask], Field(discriminator="kind")]
ProductionTaskArgs = ActionContract(ProductionTaskItem)
TaskInputsArgs = ActionContract(TaskInputs)


class StartDefaultRunArgs(ProductionArguments):
    action: Literal["start"]
    topic_ids: list[ObjectId] = Field(min_length=1, max_length=100,
        description="使用栏目默认 Skill 的真实选题 ID 列表；单个选题也填列表。")


class StartProductionTasksArgs(ProductionArguments):
    action: Literal["tasks"]
    topic_id: ObjectId
    tasks: list[ProductionTaskItem] = Field(min_length=1, max_length=100,
        description="同一选题的独立制作单位，每项一个 Codex thread；kind 必须显式选择。")
    instructions: str = Field(default="", max_length=3000, description="本批每项制作共同遵守的要求。")


class FreshProductionRunArgs(ProductionArguments):
    action: Literal["fresh"]
    source_run_id: ObjectId = Field(description="先读取的原 Run ID；从同一选题清空旧上下文重做。")
    expected_version: int = Field(gt=0, description="读取来源 Run 时取得的当前 version。")
    skill_ids: SkillIds | None = Field(default=None,
        description="本次重做使用的精确版本 ID；省略使用现有重做规则，不改栏目默认。")


class ReferenceProductionRunArgs(ProductionArguments):
    action: Literal["reference"]
    series_id: str = Field(min_length=1, description="按用户给的参考作品直接制作的目标栏目。")
    title: str = Field(min_length=1, max_length=240, description="仅用于展示的简短选题名称。")
    attachment_ids: list[Annotated[str, Field(min_length=1)]] = Field(min_length=1, max_length=20,
        description="当前会话中已有参考图片的附件 ID。")
    instructions: str = Field(default="", max_length=3000, description="参考制作的完整要求。")
    reference_presentation: bool = Field(default=False,
        description="默认只借鉴内容；用户明确要求参考画面风格或版式时才设 true。")
    skill_ids: SkillIds | None = Field(default=None,
        description="本次参考制作使用的一份或多份精确 Skill 版本 ID；省略沿用栏目默认，不改栏目绑定。")


StartRunArgs = ActionContract(Annotated[Union[
    StartDefaultRunArgs, StartProductionTasksArgs, FreshProductionRunArgs, ReferenceProductionRunArgs,
], Field(discriminator="action")])
