import asyncio
import json
import logging
import time
import uuid
from typing import Dict, Any, AsyncGenerator, Tuple

import cloudscraper
from fastapi import HTTPException
from fastapi.responses import StreamingResponse, JSONResponse

from app.core.config import settings
from app.providers.base_provider import BaseProvider
from app.utils.sse_utils import create_sse_data, create_chat_completion_chunk, DONE_CHUNK

logger = logging.getLogger(__name__)

class DreamInterpreterProvider(BaseProvider):
    def __init__(self):
        self.scraper = cloudscraper.create_scraper()
        self.api_url = "https://dreaminterpreter.ai/"

    def _prepare_headers(self) -> Dict[str, str]:
        if not settings.DREAMINTERPRETER_COOKIE:
            raise ValueError("DREAMINTERPRETER_COOKIE 未在 .env 文件中配置。")
        
        return {
            "accept": "text/x-component",
            "accept-language": "zh-CN,zh;q=0.9,en;q=0.8",
            "content-type": "text/plain;charset=UTF-8",
            "next-action": "40f12b58f04c47f0b77d33de1b8e910dbff9059260",
            "next-router-state-tree": "%5B%22%22%2C%7B%22children%22%3A%5B%22__PAGE__%22%2C%7B%7D%2C%22%2F%22%2C%22refresh%22%5D%7D%2Cnull%2Cnull%2Ctrue%5D",
            "origin": "https://dreaminterpreter.ai",
            "priority": "u=1, i",
            "referer": "https://dreaminterpreter.ai/",
            "sec-ch-ua": '"Google Chrome";v="141", "Not?A_Brand";v="8", "Chromium";v="141"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
            "cookie": settings.DREAMINTERPRETER_COOKIE
        }

    def _prepare_payload(self, prompt: str) -> str:
        payload_obj = [{
            "dream": prompt,
            "country": "CN",
            "language": "zh-CN",
            "userID": str(uuid.uuid4()),
            "lat": None,
            "long": None
        }]
        return json.dumps(payload_obj)

    def _fix_mojibake(self, text: str) -> str:
        """修复因错误编码导致的乱码问题"""
        try:
            # 尝试将错误的 latin-1 编码字节流重新解码为 utf-8
            return text.encode('latin-1').decode('utf-8')
        except (UnicodeEncodeError, UnicodeDecodeError):
            # 如果转换失败，返回原始文本，避免程序崩溃
            logger.warning(f"修复乱码失败，返回原始文本: {text[:50]}...")
            return text

    def _parse_response(self, response_text: str) -> Tuple[str, str]:
        """
        实现 [模式：NextJS-RSC-Payload-Extraction]
        从非标准响应中提取标题和解梦内容，并修复乱码。
        """
        try:
            json_start_index = response_text.find('1:')
            if json_start_index == -1:
                raise ValueError("在响应中未找到 '1:' 分隔符。")
            
            json_string = response_text[json_start_index + 2:]
            data = json.loads(json_string)
            
            dream_data = data.get("dream", {})
            title = self._fix_mojibake(dream_data.get("title", "解梦结果"))
            interpretation = self._fix_mojibake(dream_data.get("interpretation", "未能解析梦境。"))
            
            if not title or not interpretation:
                raise ValueError("解析出的 JSON 中缺少 'title' 或 'interpretation' 字段。")
                
            return title, interpretation
        except Exception as e:
            logger.error(f"解析上游响应失败: {e}\n响应内容: {response_text[:500]}...")
            raise ValueError(f"解析上游响应失败: {e}")

    async def chat_completion(self, request_data: Dict[str, Any]) -> StreamingResponse:
        messages = request_data.get("messages", [])
        last_user_message = next((m['content'] for m in reversed(messages) if m.get('role') == 'user'), None)

        if not last_user_message:
            raise HTTPException(status_code=400, detail="请求体中未找到用户消息。")

        async def stream_generator() -> AsyncGenerator[bytes, None]:
            request_id = f"chatcmpl-{uuid.uuid4()}"
            model_name = request_data.get("model", settings.DEFAULT_MODEL)
            
            try:
                headers = self._prepare_headers()
                payload = self._prepare_payload(last_user_message)

                response = self.scraper.post(
                    self.api_url,
                    headers=headers,
                    data=payload,
                    timeout=settings.API_REQUEST_TIMEOUT
                )
                response.raise_for_status()
                
                title, interpretation = self._parse_response(response.text)
                
                # 【关键修正】将字符串替换操作移出 f-string
                # 首先，将文本中字面的 "\\n" 替换为实际的换行符，以实现 Markdown 的段落分隔
                formatted_interpretation = interpretation.replace('\\n', '\n\n')
                # 然后，安全地构造完整的 Markdown 文本
                full_text = f"## {title}\n\n{formatted_interpretation}"
                
                # 实现 [模式：伪流式生成]
                chunk_size = 10
                for i in range(0, len(full_text), chunk_size):
                    content_chunk = full_text[i:i+chunk_size]
                    chunk = create_chat_completion_chunk(request_id, model_name, content_chunk)
                    yield create_sse_data(chunk)
                    await asyncio.sleep(0.02)

            except Exception as e:
                logger.error(f"处理流时发生错误: {e}", exc_info=True)
                error_message = f"内部服务器错误: {str(e)}"
                error_chunk = create_chat_completion_chunk(request_id, model_name, error_message, "stop")
                yield create_sse_data(error_chunk)
            
            finally:
                final_chunk = create_chat_completion_chunk(request_id, model_name, "", "stop")
                yield create_sse_data(final_chunk)
                yield DONE_CHUNK

        return StreamingResponse(stream_generator(), media_type="text/event-stream")

    async def get_models(self) -> JSONResponse:
        model_data = {
            "object": "list",
            "data": [
                {"id": name, "object": "model", "created": int(time.time()), "owned_by": "lzA6"}
                for name in settings.KNOWN_MODELS
            ]
        }
        return JSONResponse(content=model_data)
