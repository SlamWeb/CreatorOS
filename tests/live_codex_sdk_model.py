"""Low-cost live SDK model/new+resume check; no tools, images or business writes."""
import asyncio
from tempfile import TemporaryDirectory

from openai_codex import ApprovalMode, AsyncCodex, Sandbox

from creatoros.integrations.codex import CODEX_MODEL, CODEX_EFFORT


async def main():
    with TemporaryDirectory(prefix="creatoros-sdk-model-") as cwd:
        async with AsyncCodex() as codex:
            models = await codex.models()
            assert CODEX_MODEL in {item.model for item in models.data}, "Pinned SDK runtime does not list requested model"
            thread = await codex.thread_start(model=CODEX_MODEL, cwd=cwd, sandbox=Sandbox.read_only,
                                               approval_mode=ApprovalMode.deny_all)
            for resume in (False, True):
                if resume:
                    thread = await codex.thread_resume(thread.id, model=CODEX_MODEL, cwd=cwd,
                                                       sandbox=Sandbox.read_only, approval_mode=ApprovalMode.deny_all)
                result = await thread.run("Do not call any tools. Reply exactly READY.", model=CODEX_MODEL, effort=CODEX_EFFORT)
                assert (result.final_response or "").strip() == "READY", result.final_response
                print(f"live_sdk_model={'resume' if resume else 'new'} passed model={CODEX_MODEL} effort={CODEX_EFFORT}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
