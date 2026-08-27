from __future__ import annotations

__all__ = ["extract_text_from_image"]

import os
import base64
import logging

from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))


async def extract_text_from_image(image_path: str) -> str:
    try:
        with open(image_path, "rb") as image_file:
            image_bytes = image_file.read()

        base64_image = base64.b64encode(image_bytes).decode("utf-8")

        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            max_tokens=1500,
            temperature=0.0,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "Extract all visible text from this product label. "
                                "Return ONLY the raw text, preserving the natural "
                                "reading order. Do not add any conversational text."
                            ),
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{base64_image}"
                            },
                        },
                    ],
                }
            ],
        )

        content = response.choices[0].message.content
        if content:
            return content.strip()
        return ""

    except Exception as e:
        logger.exception(
            "Failed to extract text from image at path '%s': %s", image_path, e
        )
        return ""