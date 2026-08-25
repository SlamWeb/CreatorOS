import json

import httpx

import creatoros.tools.zhihu as zhihu_tools
from creatoros.ai.types import ToolCall
from creatoros.integrations.zhihu import ZhihuOpenAPIClient, ZhihuOpenAPIError
from creatoros.tools import tool_registry
from creatoros.tools.execution import execute_tool_call


def main():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "Code": 0,
                "Message": "success",
                "Data": {
                    "HasMore": False,
                    "SearchHashId": "search-123",
                    "Items": [
                        {
                            "Title": "Agent Memory 怎么设计？",
                            "ContentType": "Answer",
                            "ContentID": "answer-1",
                            "ContentText": "从短期状态与长期记忆讲起。",
                            "Url": "https://www.zhihu.com/question/1/answer/1",
                            "CommentCount": 12,
                            "VoteUpCount": 128,
                            "AuthorName": "测试作者",
                            "EditTime": 1787500000,
                            "AuthorityLevel": "2",
                            "RankingScore": 0.98,
                        }
                    ],
                },
            },
            request=request,
        )

    def factory():
        return ZhihuOpenAPIClient(
            access_secret="smoke-secret",
            base_url="https://zhihu.test",
            transport=httpx.MockTransport(handler),
        )

    previous_factory = zhihu_tools._client_factory
    zhihu_tools._client_factory = factory
    try:
        result = execute_tool_call(
            ToolCall(
                "search-1",
                "search_zhihu",
                json.dumps({"query": "Agent Memory", "count": 1}),
            )
        )
    finally:
        zhihu_tools._client_factory = previous_factory

    assert not result.is_error
    payload = json.loads(result.content)
    assert payload["query"] == "Agent Memory"
    assert payload["search_hash_id"] == "search-123"
    assert payload["items"][0]["author_name"] == "测试作者"
    assert payload["items"][0]["vote_up_count"] == 128

    request = requests[0]
    assert request.url.path == "/api/v1/content/zhihu_search"
    assert request.url.params["Query"] == "Agent Memory"
    assert request.url.params["Count"] == "1"
    assert request.headers["authorization"] == "Bearer smoke-secret"
    assert request.headers["x-request-timestamp"].isdigit()

    schema = tool_registry["search_zhihu"].to_schema()
    properties = schema["function"]["parameters"]["properties"]
    assert properties["query"]["maxLength"] == 100
    assert properties["count"]["maximum"] == 10

    invalid = execute_tool_call(
        ToolCall("search-2", "search_zhihu", json.dumps({"query": ""}))
    )
    assert invalid.is_error
    assert invalid.error_type == "invalid_arguments"

    missing_secret = ZhihuOpenAPIClient(
        access_secret="",
        base_url="https://zhihu.test",
        transport=httpx.MockTransport(handler),
    )
    try:
        missing_secret.search("Agent")
        raise AssertionError("缺少 Access Secret 时应该失败")
    except ZhihuOpenAPIError as error:
        assert error.error_type == "zhihu_auth"
    finally:
        missing_secret.close()

    print("zhihu_search_smoke=passed")


if __name__ == "__main__":
    main()
