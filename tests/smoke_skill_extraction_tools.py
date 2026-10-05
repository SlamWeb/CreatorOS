"""Agent artifact-to-Skill adapters: schema, Studio transport and local image guardrails."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from creatoros.ai.types import ToolCall
from creatoros.context import RuntimeContext
from creatoros.tools import execute_tool_call, tools
from creatoros.tools.definitions import tool_registry
from creatoros.web.chat import STUDIO_TOOLS


PNG = b"\x89PNG\r\n\x1a\n" + b"fixture-data"


class FakeStudioClient:
    calls = []

    def __init__(self, _base_url=None, *, agent_session_id=None):
        self.base_url = "http://127.0.0.1:8765"
        self.agent_session_id = agent_session_id

    @classmethod
    def from_defaults(cls):
        return cls()

    def request(self, method, path, *, params=None, payload=None):
        self.calls.append((method, path, params, payload))
        if path == "/api/skill-extractions/uploads":
            return {"id": "upload-1", "name": payload["name"], "url": "/uploads/upload-1"}
        if method == "GET" and path == "/api/skill-extractions":
            return {"items": []}
        if method == "POST" and path == "/api/skill-extractions":
            return {"id": "job-1", "request_id": payload["request_id"], "status": "running",
                    "mode": payload["mode"], "source_text": payload["source_text"],
                    "uploads": [{"id": item} for item in payload["upload_ids"]]}
        if path.endswith("/save"):
            return {"id": "job-1", "status": "saved", "saved_skills": [{"id": "skill-1"}]}
        if path.endswith("/cancel"):
            return {"id": "job-1", "status": "running", "cancel_requested": True}
        return {"id": path.rsplit("/", 1)[-1], "status": "ready", "skills": [{"skill_md": "draft"}],
                "digest": "digest-1"}

    def close(self):
        pass


def _tool(name, args, context):
    call = ToolCall("test", name, json.dumps(args))
    return execute_tool_call(call, context=context, model_requested=True)


def main():
    from creatoros.integrations.skill_extraction import EXTRACTION_MODEL, EXTRACTION_EFFORT, MODE_GUIDANCE, extraction_prompt
    from creatoros.integrations.skill_draft_files import MODE_FOLDERS
    from creatoros.integrations.codex import CODEX_MODEL
    assert EXTRACTION_MODEL == "gpt-6-sol"
    assert EXTRACTION_EFFORT == "high"
    assert CODEX_MODEL == "gpt-6-luna", "Extraction must not change production/research model"
    assert len(MODE_GUIDANCE) == 4
    with TemporaryDirectory() as prompt_root:
        output_directory = Path(prompt_root) / "draft"
        for mode, guidance in MODE_GUIDANCE.items():
            assert "\n" not in guidance.strip()
            assert any("中" <= char <= "龥" for char in guidance)
            prompt = extraction_prompt(mode, "中英双语", ["assets/reference-1.jpg"], output_directory)
            assert guidance in prompt
            assert "YAML" not in prompt and "中英双语" in prompt
            assert "assets/reference-1.jpg" in prompt
            assert "skill-creator" not in prompt.lower()
            assert "legacy_end_to_end" not in prompt
            for folder in MODE_FOLDERS[mode]:
                assert str(output_directory / folder / "SKILL.md") in prompt
    names = {item["function"]["name"] for item in tools}
    expected = {"extract_skills_from_artifact", "get_skill_extraction",
                "save_extracted_skills", "cancel_skill_extraction", "edit_extracted_skills",
                "revise_extracted_skills", "trial_extracted_skills"}
    assert expected <= names
    assert expected <= set(tool_registry)
    assert expected <= STUDIO_TOOLS
    descriptions = {name: tool_registry[name].description for name in expected}
    assert "不会自动保存" in descriptions["extract_skills_from_artifact"]
    assert "明确确认" in descriptions["save_extracted_skills"]
    assert "查询结果中的 digest 原样作为 expected_digest" in descriptions["save_extracted_skills"]
    from creatoros.web.chat import WEB_INSTRUCTIONS
    assert "Web 会话不能读取本机项目路径" in WEB_INSTRUCTIONS
    assert "图片只能用已有 upload_ids" in WEB_INSTRUCTIONS
    assert "参考文案可直接传 source_text" in WEB_INSTRUCTIONS
    schema = tool_registry["extract_skills_from_artifact"].to_schema()["function"]["parameters"]
    assert "request_id" in schema["required"]
    assert "image_paths" in schema["properties"] and "upload_ids" in schema["properties"]
    assert "source_text" in schema["properties"]
    assert schema["properties"]["mode"]["default"] == "single"

    with TemporaryDirectory() as temporary:
        root = Path(temporary)
        image = root / "reference.png"
        image.write_bytes(PNG)
        (root / ".env").write_bytes(PNG)
        (root / "bad.jpg").write_bytes(b"not-an-image")
        (root / "large.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * (4 * 1024 * 1024))
        context = RuntimeContext(project_root=root, studio_url="http://127.0.0.1:8765")
        web_context = RuntimeContext(project_root=root, studio_url="http://127.0.0.1:8765",
                                     archive_only_reads=True)

        FakeStudioClient.calls = []
        with patch("creatoros.tools.studio.StudioClient", FakeStudioClient):
            result = _tool("extract_skills_from_artifact", {
                "image_paths": [str(image)], "request_id": "request-1", "mode": "pair",
                "instruction": "提取留白和标题层级",
            }, context)
            assert not result.is_error, result.content
            data = json.loads(result.content)
            assert data["id"] == "job-1"
            upload, submit = FakeStudioClient.calls
            assert upload[:2] == ("POST", "/api/skill-extractions/uploads")
            assert upload[3]["name"] == "reference.png"
            assert "data_base64" in upload[3]
            assert submit[:2] == ("POST", "/api/skill-extractions")
            assert submit[3] == {"request_id": "request-1", "upload_ids": ["upload-1"],
                                 "mode": "pair", "instruction": "提取留白和标题层级", "source_text": ""}
            assert upload[3]["data_base64"] not in result.content

            # Source text can be submitted without image paths or upload IDs; omitted mode is single.
            FakeStudioClient.calls = []
            text_only = _tool("extract_skills_from_artifact", {
                "request_id": "text-only", "source_text": "A short sample."}, context)
            assert not text_only.is_error, text_only.content
            assert FakeStudioClient.calls == [("POST", "/api/skill-extractions", None, {
                "request_id": "text-only", "upload_ids": [], "mode": "single", "instruction": "",
                "source_text": "A short sample."})]

            # Existing browser upload IDs use the same API and are not uploaded again.
            FakeStudioClient.calls = []
            direct = _tool("extract_skills_from_artifact", {
                "upload_ids": ["browser-upload"], "request_id": "request-2",
            }, context)
            assert not direct.is_error
            assert len(FakeStudioClient.calls) == 1
            assert FakeStudioClient.calls[0][3]["upload_ids"] == ["browser-upload"]
            FakeStudioClient.calls = []
            web_local = _tool("extract_skills_from_artifact", {
                "image_paths": [str(image)], "request_id": "web-local",
            }, web_context)
            assert web_local.is_error and web_local.error_type == "path_out_of_scope"
            assert "上传" in web_local.content and "upload_ids" in web_local.content
            assert not FakeStudioClient.calls
            web_upload = _tool("extract_skills_from_artifact", {
                "upload_ids": ["browser-upload"], "request_id": "web-upload",
            }, web_context)
            assert not web_upload.is_error

            for name, args, error in [
                ("extract_skills_from_artifact", {"image_paths": [".env"], "request_id": "r"}, "path_out_of_scope"),
                ("extract_skills_from_artifact", {"image_paths": ["..\\outside.png"], "request_id": "r"}, "path_out_of_scope"),
                ("extract_skills_from_artifact", {"image_paths": ["bad.jpg"], "request_id": "r"}, "invalid_image"),
                ("extract_skills_from_artifact", {"image_paths": ["large.png"], "request_id": "r"}, "image_too_large"),
            ]:
                FakeStudioClient.calls = []
                rejected = _tool(name, args, context)
                assert rejected.is_error and rejected.error_type == error, rejected
                assert not FakeStudioClient.calls

            # Oversized files are rejected by stat before any file content is opened.
            with patch.object(Path, "open", side_effect=AssertionError("oversized file was opened")):
                oversized = _tool("extract_skills_from_artifact", {
                    "image_paths": ["large.png"], "request_id": "too-large",
                }, context)
            assert oversized.is_error and oversized.error_type == "image_too_large"

            assert _tool("extract_skills_from_artifact", {"image_paths": ["reference.png"]}, context).is_error
            assert _tool("extract_skills_from_artifact", {
                "image_paths": ["reference.png"], "upload_ids": ["also-upload"], "request_id": "r",
            }, context).is_error

            FakeStudioClient.calls = []
            assert not _tool("get_skill_extraction", {}, context).is_error
            assert FakeStudioClient.calls[-1][1] == "/api/skill-extractions"
            assert not _tool("get_skill_extraction", {"job_id": "job/a"}, context).is_error
            assert FakeStudioClient.calls[-1][1] == "/api/skill-extractions/job%2Fa"
            assert not _tool("save_extracted_skills", {"job_id": "job-1", "expected_digest": "digest-1"}, context).is_error
            assert FakeStudioClient.calls[-1][3] == {"expected_digest": "digest-1"}
            assert not _tool("cancel_skill_extraction", {"job_id": "job-1"}, context).is_error
            assert json.loads(_tool("get_skill_extraction", {"job_id": "job-1"}, context).content)["status"] == "ready"

            FakeStudioClient.calls = []
            edited = _tool("edit_extracted_skills", {"job_id": "job-1", "expected_digest": "digest-1",
                "skills": [{"name": "draft", "role": "legacy_end_to_end", "skill_md": "A full draft." * 4}]}, context)
            assert not edited.is_error, edited.content
            assert FakeStudioClient.calls[-1][:2] == ("POST", "/api/skill-extractions/job-1/draft")
            assert FakeStudioClient.calls[-1][3]["expected_digest"] == "digest-1"
            assert not _tool("revise_extracted_skills", {"job_id": "job-1", "expected_digest": "digest-1",
                "request_id": "revise-1", "instruction": "Retain the pacing."}, context).is_error
            assert FakeStudioClient.calls[-1][:2] == ("POST", "/api/skill-extractions/job-1/revise")
            assert not _tool("trial_extracted_skills", {"job_id": "job-1", "expected_digest": "digest-1",
                "request_id": "trial-1", "topic": "Reuse the sample topic."}, context).is_error
            assert FakeStudioClient.calls[-1][:2] == ("POST", "/api/skill-extractions/job-1/trials")
    print("skill_extraction_tools_smoke=passed")


if __name__ == "__main__":
    main()
