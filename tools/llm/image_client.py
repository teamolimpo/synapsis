"""OpenRouter HTTP client for image generation (merged from tools/image_gen/client.py).

Handles chat completions API calls with retry logic, timeout, and
structured response parsing. Supports Gemini-style (modalities) and
FLUX-style (per-MP pricing) models.
"""

from __future__ import annotations

import base64
import json
import time
from pathlib import Path
from typing import Any

import httpx
from loguru import logger

from tools.llm.config import (
    IMAGE_MAX_RETRIES,
    IMAGE_REQUEST_TIMEOUT,
    OPENROUTER_API_URL,
    estimate_image_cost,
)

# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class ImageResult:
    """Result of an image generation API call."""

    def __init__(
        self,
        success: bool,
        image_base64: str | None = None,
        mime_type: str = "image/png",
        content_type: str | None = None,
        error_type: str | None = None,
        error_message: str = "",
        retryable: bool = False,
        cost: float = 0.0,
        model: str = "",
        input_tokens: int = 0,
        output_tokens: int = 0,
        generation_time_s: float = 0.0,
    ) -> None:
        self.success = success
        self.image_base64 = image_base64
        self.mime_type = mime_type
        self.content_type = content_type
        self.error_type = error_type
        self.error_message = error_message
        self.retryable = retryable
        self.cost = cost
        self.model = model
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.generation_time_s = generation_time_s

    @classmethod
    def from_error(
        cls,
        error_type: str,
        error_message: str,
        retryable: bool = False,
        model: str = "",
        generation_time_s: float = 0.0,
    ) -> ImageResult:
        return cls(
            success=False,
            error_type=error_type,
            error_message=error_message,
            retryable=retryable,
            model=model,
            generation_time_s=generation_time_s,
        )


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class OpenRouterImageClient:
    """HTTP client for OpenRouter chat completions image generation.

    Args:
        api_key: OpenRouter API key.
        site_url: HTTP-Referer header value.
        app_name: X-Title header value.
    """

    def __init__(
        self,
        api_key: str,
        site_url: str = "https://github.com/TeamOlimpo",
        app_name: str = "TeamOlimpo-Fidia",
    ) -> None:
        self.api_key = api_key
        self.site_url = site_url
        self.app_name = app_name
        self._client: httpx.Client | None = None

    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                timeout=httpx.Timeout(IMAGE_REQUEST_TIMEOUT),
                headers=self._build_headers(),
            )
        return self._client

    def _build_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": self.site_url,
            "X-Title": self.app_name,
        }

    def generate(
        self,
        prompt: str,
        model: str = "openai/gpt-5-image-mini",
        size: str = "1K",
        ratio: str = "1:1",
        input_image_path: str | None = None,
        negative_prompt: str | None = None,
        seed: int | None = None,
        image_config_json: str | None = None,
    ) -> ImageResult:
        """Call OpenRouter API to generate an image.

        Args:
            prompt: Text prompt for generation.
            model: OpenRouter model ID.
            size: Image size (1K, 2K, 4K).
            ratio: Aspect ratio (1:1, 16:9, etc.).
            input_image_path: Path for image-to-image (models that support it).
            negative_prompt: Negative prompt (models that support it).
            seed: Random seed for reproducibility.
            image_config_json: Extra JSON for advanced image config.

        Returns:
            ImageResult with image data or error.
        """
        body = self._build_request_body(
            prompt=prompt,
            model=model,
            size=size,
            ratio=ratio,
            input_image_path=input_image_path,
            negative_prompt=negative_prompt,
            seed=seed,
            image_config_json=image_config_json,
        )

        last_error: ImageResult | None = None
        start_time = time.monotonic()

        for attempt in range(IMAGE_MAX_RETRIES + 1):
            if attempt > 0:
                wait = 2**attempt
                logger.info(f"Retry attempt {attempt}/{IMAGE_MAX_RETRIES} in {wait}s...")
                time.sleep(wait)

            try:
                response = self.client.post(OPENROUTER_API_URL, json=body)
                elapsed = time.monotonic() - start_time
                result = self._parse_response(response, model, elapsed)

                if not result.success and result.retryable and attempt < IMAGE_MAX_RETRIES:
                    last_error = result
                    continue

                if result.success and result.input_tokens > 0:
                    tok = result.input_tokens
                    result.cost = estimate_image_cost(model, size, prompt_tokens=tok)

                return result

            except httpx.TimeoutException:
                elapsed = time.monotonic() - start_time
                last_error = ImageResult.from_error(
                    "bad_request",
                    f"Request timed out after {IMAGE_REQUEST_TIMEOUT}s",
                    retryable=True,
                    model=model,
                )
                last_error.generation_time_s = elapsed
                if attempt < IMAGE_MAX_RETRIES:
                    continue
                return last_error

            except httpx.HTTPStatusError as e:
                elapsed = time.monotonic() - start_time
                res = ImageResult.from_error(
                    "bad_request",
                    f"HTTP {e.response.status_code}: {e.response.text[:200]}",
                    retryable=False,
                    model=model,
                )
                res.generation_time_s = elapsed
                return res

            except httpx.RequestError as e:
                elapsed = time.monotonic() - start_time
                last_error = ImageResult.from_error(
                    "bad_request",
                    f"Connection error: {e}",
                    retryable=True,
                    model=model,
                )
                last_error.generation_time_s = elapsed
                if attempt < IMAGE_MAX_RETRIES:
                    continue
                return last_error

        return last_error or ImageResult.from_error(
            "generic", "All retry attempts exhausted", model=model
        )

    # ------------------------------------------------------------------
    # Request builder
    # ------------------------------------------------------------------

    def _build_request_body(
        self,
        prompt: str,
        model: str,
        size: str,
        ratio: str,
        input_image_path: str | None = None,
        negative_prompt: str | None = None,
        seed: int | None = None,
        image_config_json: str | None = None,
    ) -> dict[str, Any]:
        """Build the request body for the OpenRouter API.

        Different model families use different request formats:
        - Gemini/GPT-5 Image: use ``modalities`` + ``image_config``
        - FLUX: may use text completions with special format
        - Seedream: fixed price per image
        """
        image_config: dict[str, Any] = {
            "aspect_ratio": ratio,
            "image_size": size,
        }

        if negative_prompt:
            image_config["negative_prompt"] = negative_prompt
        if seed is not None:
            image_config["seed"] = seed

        if image_config_json:
            try:
                extra = json.loads(image_config_json)
                image_config.update(extra)
            except json.JSONDecodeError:
                logger.warning(f"Invalid --image-config-json: {image_config_json}")

        body: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "modalities": ["image", "text"],
            "image_config": image_config,
        }

        if input_image_path:
            body["messages"] = [
                {
                    "role": "user",
                    "content": self._build_multimodal_content(prompt, input_image_path),
                }
            ]

        return body

    def _build_multimodal_content(self, prompt: str, image_path: str) -> list[dict[str, Any]]:
        """Build multimodal content array with text and image parts."""
        path = Path(image_path)
        if not path.is_file():
            raise FileNotFoundError(f"Input image not found: {image_path}")

        raw = path.read_bytes()
        encoded = base64.b64encode(raw).decode("utf-8")
        mime = self._guess_mime(path.suffix)

        return [
            {"type": "text", "text": prompt},
            {
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{encoded}"},
            },
        ]

    @staticmethod
    def _guess_mime(suffix: str) -> str:
        mimes = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".webp": "image/webp",
            ".gif": "image/gif",
            ".bmp": "image/bmp",
        }
        return mimes.get(suffix.lower(), "image/png")

    # ------------------------------------------------------------------
    # Response parser
    # ------------------------------------------------------------------

    def _parse_response(self, response: httpx.Response, model: str, elapsed: float) -> ImageResult:
        """Parse the OpenRouter API response.

        Args:
            response: HTTP response from OpenRouter.
            model: Model ID for cost tracking.
            elapsed: Elapsed time in seconds.

        Returns:
            ImageResult with extracted image data or error.
        """
        if response.status_code == 429:
            return ImageResult.from_error(
                "rate_limit",
                "Rate limit exceeded. Try again later.",
                retryable=True,
                model=model,
                generation_time_s=elapsed,
            )

        if response.status_code == 402:
            return ImageResult.from_error(
                "credit_error",
                "Insufficient credits. Please add funds to your OpenRouter account.",
                retryable=False,
                model=model,
                generation_time_s=elapsed,
            )

        if response.status_code == 401:
            return ImageResult.from_error(
                "policy_rejection",
                "Invalid API key or unauthorized.",
                retryable=False,
                model=model,
                generation_time_s=elapsed,
            )

        if response.status_code == 400:
            err_msg = self._extract_error_message(response)
            return ImageResult.from_error(
                "bad_request",
                err_msg,
                retryable=False,
                model=model,
                generation_time_s=elapsed,
            )

        if response.status_code != 200:
            err_msg = self._extract_error_message(response)
            logger.warning(f"Unexpected status {response.status_code}: {err_msg}")
            return ImageResult.from_error(
                "generic",
                f"HTTP {response.status_code}: {err_msg}",
                retryable=response.status_code >= 500,
                model=model,
                generation_time_s=elapsed,
            )

        try:
            data = response.json()
        except json.JSONDecodeError as e:
            return ImageResult.from_error(
                "bad_request",
                f"Invalid JSON response: {e}",
                retryable=False,
                model=model,
                generation_time_s=elapsed,
            )

        result = ImageResult(success=True, model=model, generation_time_s=elapsed)

        image_data = self._extract_image(data)
        if image_data is None:
            text_content = self._extract_text(data)
            if text_content and "data:image" in text_content:
                result.image_base64 = text_content.split("base64,")[-1].split('"')[0]
            elif text_content and text_content.startswith("data:image"):
                result.image_base64 = text_content.split("base64,")[-1]
            else:
                err_msg = self._extract_error_message_from_choices(data) or ""
                if err_msg:
                    return ImageResult.from_error(
                        "generic",
                        err_msg,
                        retryable=True,
                        model=model,
                        generation_time_s=elapsed,
                    )
                return ImageResult.from_error(
                    "generic",
                    "No image data found in response",
                    retryable=True,
                    model=model,
                    generation_time_s=elapsed,
                )
        else:
            result.image_base64 = image_data

        result.content_type = self._extract_content_type(data)
        result.mime_type = result.content_type or "image/png"

        usage = data.get("usage", {})
        result.input_tokens = usage.get("prompt_tokens", 0) or usage.get("input_tokens", 0)
        result.output_tokens = usage.get("completion_tokens", 0) or usage.get("output_tokens", 0)

        api_cost = self._extract_cost(data)
        if api_cost is not None:
            result.cost = api_cost

        return result

    @staticmethod
    def _extract_image(data: dict[str, Any]) -> str | None:
        """Extract base64-encoded image data from OpenRouter response."""
        try:
            choices = data.get("choices", [])
            if not choices:
                return None
            message = choices[0].get("message", {})
            images = message.get("images", [])
            if images and isinstance(images, list):
                img = images[0]
                # OpenRouter returns image_url.url format
                image_url = img.get("image_url", {})
                if isinstance(image_url, dict):
                    url = image_url.get("url", "")
                else:
                    url = str(image_url) if image_url else ""
                if not url:
                    # Fallback: check data_url directly
                    url = img.get("data_url", "")
                if url and "base64," in url:
                    return url.split("base64,")[-1]
        except (KeyError, IndexError, TypeError):
            pass
        return None

    @staticmethod
    def _extract_text(data: dict[str, Any]) -> str | None:
        """Extract text content from the response."""
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            return None

    @staticmethod
    def _extract_content_type(data: dict[str, Any]) -> str | None:
        """Extract MIME type from the image response."""
        try:
            choices = data.get("choices", [])
            if not choices:
                return None
            message = choices[0].get("message", {})
            images = message.get("images", [])
            if images and isinstance(images, list):
                img = images[0]
                image_url = img.get("image_url", {})
                if isinstance(image_url, dict):
                    url = image_url.get("url", "")
                    if url and url.startswith("data:image/"):
                        mime = url.split(";")[0].replace("data:", "")
                        if mime:
                            return mime
                return img.get("format") or "image/png"
        except (IndexError, TypeError):
            pass
        return None

    @staticmethod
    def _extract_cost(data: dict[str, Any]) -> float | None:
        """Extract cost from OpenRouter response if available."""
        try:
            return float(data.get("usage", {}).get("total_cost", 0))
        except (KeyError, TypeError, ValueError):
            return None

    @staticmethod
    def _extract_error_message(response: httpx.Response) -> str:
        """Extract error message from non-200 response."""
        try:
            data = response.json()
            err = data.get("error", {})
            if isinstance(err, dict):
                return err.get("message", str(err))
            return str(err)
        except (json.JSONDecodeError, AttributeError):
            return response.text[:200]

    @staticmethod
    def _extract_error_message_from_choices(data: dict[str, Any]) -> str | None:
        """Extract error message from choices if the model returned an error."""
        try:
            finish = data["choices"][0].get("finish_reason", "")
            if finish == "error":
                return data["choices"][0].get("message", {}).get("content", "Model error")
        except (KeyError, IndexError, TypeError):
            pass
        return None

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def __enter__(self) -> OpenRouterImageClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
