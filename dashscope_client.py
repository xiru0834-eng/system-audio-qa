"""Streaming Qwen text generation over the DashScope SSE interface."""

import json
import re

import httpx

from openai_client import QUICK_OUTPUT_TOKENS, ApiError, CloudClient, interview_instructions


REGIONS = {"北京": "cn-beijing", "新加坡": "ap-southeast-1"}


def dashscope_endpoint(workspace_id, region):
    if region not in REGIONS:
        raise ValueError("请选择北京或新加坡服务地域。")
    if not re.fullmatch(r"[A-Za-z0-9-]{3,80}", workspace_id.strip()):
        raise ValueError("请填写百炼业务空间 ID。")
    return f"https://{workspace_id.strip()}.{REGIONS[region]}.maas.aliyuncs.com"


class DashScopeClient(CloudClient):
    service_name = "阿里云百炼"

    def __init__(self, key, workspace_id, region, *, transport=None):
        endpoint = dashscope_endpoint(workspace_id, region)
        super().__init__(key, transport=transport)
        self.http.base_url = httpx.URL(f"{endpoint}/api/v1/services/aigc/")
        self.http.headers["X-DashScope-WorkSpace"] = workspace_id.strip()
        self.http.headers["X-DashScope-SSE"] = "enable"
        self.last_usage = None

    def answer(self, transcript, profile="", *, prepared_input=None, deep=False):
        request = {
            "model": "qwen-flash",
            "input": {"messages": [
                {"role": "system", "content": interview_instructions(prepared_input)},
                {"role": "user", "content": self.answer_input(transcript, profile, prepared_input)},
            ]},
            "parameters": {
                "result_format": "message",
                "incremental_output": True,
                "enable_thinking": bool(deep),
                "max_tokens": 1400 if deep else QUICK_OUTPUT_TOKENS,
                "temperature": 0.2,
            },
        }
        received_text = False
        finished = False
        with self.http.stream("POST", "text-generation/generation", json=request) as response:
            self.check(response)
            for line in response.iter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if not data:
                    continue
                if data == "[DONE]":
                    finished = True
                    break
                try:
                    event = json.loads(data)
                except json.JSONDecodeError as error:
                    raise ApiError("百炼返回了无法读取的流式数据。") from error
                if event.get("code") and "output" not in event:
                    raise ApiError("百炼文本服务返回错误。请检查地域、Key 和模型权限。")
                usage = event.get("usage")
                if isinstance(usage, dict):
                    self.last_usage = usage
                choices = event.get("output", {}).get("choices", [])
                if choices:
                    choice = choices[0]
                    content = choice.get("message", {}).get("content", "")
                    if isinstance(content, str) and content:
                        received_text = True
                        yield content
                    finished = finished or choice.get("finish_reason") == "stop"

        if not received_text:
            raise ApiError("百炼没有返回可显示的回答。")
        if not finished:
            raise ApiError("回答连接提前结束，屏幕上的内容可能不完整。")
