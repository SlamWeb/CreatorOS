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
                    "Total": 2,
                    "Items": [
                        {
                            "Title": "热点问题一",
                            "Url": "https://www.zhihu.com/question/1",
                            "Summary": "摘要一",
                            "ThumbnailUrl": "https://pic.example/1.jpg",
                        },
                        {
                            "Title": "热点问题二",
                            "Url": "https://www.zhihu.com/question/2",
                            "Summary": "摘要二",
                            "ThumbnailUrl": "",
                        },
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
            ToolCall("hot-1", "get_zhihu_hot_list", json.dumps({"limit": 2}))
        )
    finally:
        zhihu_tools._client_factory = previous_factory

    assert not result.is_error
    payload = json.loads(result.content)
    assert payload["source"] == "zhihu"
    assert payload["topics"][0]["rank"] == 1
    assert payload["topics"][0]["title"] == "热点问题一"
    assert len(requests) == 1
    assert requests[0].url.path == "/api/v1/content/hot_list"
    assert requests[0].url.params["Limit"] == "2"
    assert requests[0].headers["authorization"] == "Bearer smoke-secret"
    assert requests[0].headers["x-request-timestamp"].isdigit()

    schema = tool_registry["get_zhihu_hot_list"].to_schema()
    limit_schema = schema["function"]["parameters"]["properties"]["limit"]
    assert limit_schema["minimum"] == 1
    assert limit_schema["maximum"] == 30

    missing_secret = ZhihuOpenAPIClient(
        access_secret="",
        base_url="https://zhihu.test",
        transport=httpx.MockTransport(handler),
    )
    try:
        missing_secret.get_hot_list()
        raise AssertionError("缺少 Access Secret 时应该失败")
    except ZhihuOpenAPIError as error:
        assert error.error_type == "zhihu_auth"
    finally:
        missing_secret.close()

    auth_error_client = ZhihuOpenAPIClient(
        access_secret="invalid-secret",
        base_url="https://zhihu.test",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "Code": 20001,
                    "Message": "Authorization failed",
                    "Data": None,
                },
                request=request,
            )
        ),
    )
    try:
        auth_error_client.get_hot_list()
        raise AssertionError("官方鉴权错误应该转换为 zhihu_auth")
    except ZhihuOpenAPIError as error:
        assert error.error_type == "zhihu_auth"
        assert error.details["code"] == 20001
    finally:
        auth_error_client.close()

    print("zhihu_hot_list_smoke=passed")


if __name__ == "__main__":
    main()
