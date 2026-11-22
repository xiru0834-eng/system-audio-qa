import json

from openai_client import QUICK_OUTPUT_TOKENS, ApiError, CloudClient, interview_instructions


class DeepSeekClient(CloudClient):
    service_name = "DeepSeek"
    base_url = "https://api.deepseek.com/v1/"

    def transcribe(self, wav):
        response = self.http.post("tools/asr", files={"file": ("playback.wav", wav, "audio/wav")})
        self.check(response)
        return self.transcript(response)

    def answer(self, transcript, profile="", *, prepared_input=None, deep=False):
        request = {
            "model": "deepseek-flash",
            "messages": [
                {"role": "system", "content": interview_instructions(prepared_input)},
                {"role": "user", "content": self.answer_input(transcript, profile, prepared_input)},
            ],
            "thinking": {"type": "enabled" if deep else "disabled"},
            "max_tokens": 4096 if deep else QUICK_OUTPUT_TOKENS,
            "stream": True,
        }
        if deep:
            request["reasoning_effort"] = "high"
        else:
            request["temperature"] = 0.2

        with self.http.stream("POST", "chat/completions", json=request) as response:
            self.check(response)
            received_text = False
            finished = False
            for line in response.iter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if not data:
                    continue
                if data == "[DONE]":
                    if not finished:
                        break
                    if not received_text:
                        raise ApiError("DeepSeek 没有返回可显示的回答。")
                    return
                event = json.loads(data)
                if "error" in event:
                    raise ApiError("DeepSeek 生成中断。可用当前转写重答。")
                for choice in event.get("choices", []):
                    chunk = choice.get("delta", {}).get("content")
                    if chunk:
                        received_text = received_text or bool(chunk.strip())
                        yield chunk
                    reason = choice.get("finish_reason")
                    if reason and reason != "stop":
                        raise ApiError("DeepSeek 未完整返回回答。可用当前转写重答。")
                    finished = finished or reason == "stop"
            raise ApiError("回答连接提前结束，屏幕上的内容可能不完整。")
