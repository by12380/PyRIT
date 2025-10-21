# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
import logging
import os
from typing import Optional

import httpx

from pyrit.models import (
    PromptRequestPiece,
    PromptRequestResponse,
    construct_response_from_request,
)
from pyrit.prompt_target import PromptChatTarget, limit_requests_per_minute

logger = logging.getLogger(__name__)


class ReplicateTarget(PromptChatTarget):
    """
    A prompt target for Replicate API that supports both streaming and synchronous responses.
    
    Args:
        model_version (str): The Replicate model in format "owner/model:version_hash"
            Example: "google-deepmind/gemma-2-2b-it:ff924e24b20727e4e04b9721b403b1a75500b7b8b934714ed2b34afc6de69673"
        api_token (str, Optional): Replicate API token. Defaults to REPLICATE_API_TOKEN env var
        max_new_tokens (int): Maximum tokens to generate
        use_wait (bool): Use "Prefer: wait" header for synchronous response (recommended)
        stream (bool): Whether to use streaming responses (only if use_wait=False)
        temperature (float, Optional): Temperature for generation (0-2)
        top_p (float, Optional): Top-p sampling parameter (0-1)
        top_k (int, Optional): Top-k sampling parameter
        repetition_penalty (float, Optional): Repetition penalty
        max_requests_per_minute (int, Optional): Rate limiting
    """

    def __init__(
        self,
        *,
        model_version: str,
        api_token: Optional[str] = None,
        max_new_tokens: int = 512,
        use_wait: bool = True,
        stream: bool = False,
        temperature: Optional[float] = None,
        top_p: Optional[float] = None,
        top_k: Optional[int] = None,
        repetition_penalty: Optional[float] = None,
        is_json_supported: bool = True,
        max_requests_per_minute: Optional[int] = None,
        **kwargs,
    ):
        super().__init__(max_requests_per_minute=max_requests_per_minute)
        
        self.model_version = model_version
        self.api_token = api_token or os.environ.get("REPLICATE_API_TOKEN")
        self.max_new_tokens = max_new_tokens
        self.use_wait = use_wait
        self.stream = stream if not use_wait else False
        self.temperature = temperature
        self.top_p = top_p
        self.top_k = top_k
        self.repetition_penalty = repetition_penalty
        self._is_json_supported = is_json_supported
        
        if not self.api_token:
            raise ValueError("REPLICATE_API_TOKEN environment variable or api_token parameter required")
    
    @limit_requests_per_minute
    async def send_prompt_async(self, *, prompt_request: PromptRequestResponse) -> PromptRequestResponse:
        """
        Sends a prompt to Replicate API and returns the response.
        """
        self._validate_request(prompt_request=prompt_request)
        request_piece: PromptRequestPiece = prompt_request.request_pieces[0]
        
        # Add JSON formatting instructions if needed
        prompt_text = request_piece.converted_value
        
        # Check if this looks like it needs JSON output (contains schema or format instructions)
        if self._is_json_supported and ("json" in prompt_text.lower() or "{" in prompt_text):
            # Add strong JSON formatting guidance
            prompt_text = f"""{prompt_text}

IMPORTANT: You MUST respond with ONLY valid JSON. Do not include any text before or after the JSON object. Your entire response must be parseable JSON."""
        
        # Build input parameters matching Replicate API format
        input_params = {
            "prompt": prompt_text,
            "max_new_tokens": self.max_new_tokens,
        }
        
        if self.temperature is not None:
            input_params["temperature"] = self.temperature
        
        if self.top_p is not None:
            input_params["top_p"] = self.top_p
        
        if self.top_k is not None:
            input_params["top_k"] = self.top_k
        
        if self.repetition_penalty is not None:
            input_params["repetition_penalty"] = self.repetition_penalty
        
        # Create prediction request
        prediction_data = {
            "version": self.model_version,  # Format: "owner/model:version_hash"
            "input": input_params
        }
        
        # Only add stream if not using wait
        if not self.use_wait:
            prediction_data["stream"] = self.stream
        
        headers = {
            "Authorization": f"Bearer {self.api_token}",
            "Content-Type": "application/json",
        }
        
        # Add "Prefer: wait" for synchronous response
        if self.use_wait:
            headers["Prefer"] = "wait"
        
        async with httpx.AsyncClient(timeout=300.0) as client:
            # Create the prediction
            logger.info(f"Creating Replicate prediction for prompt: {request_piece.converted_value[:50]}...")
            
            prediction_response = await client.post(
                "https://api.replicate.com/v1/predictions",
                headers=headers,
                json=prediction_data,
            )
            prediction_response.raise_for_status()
            prediction = prediction_response.json()
            
            prediction_id = prediction.get('id')
            status = prediction.get('status')
            logger.info(f"Prediction created with ID: {prediction_id}, Status: {status}")
            
            # Handle based on status
            if status == "succeeded":
                # Already completed - extract output directly
                response_text = self._extract_output(prediction)
            elif status in ["starting", "processing"]:
                # Not complete yet - need to poll/stream
                if self.stream and not self.use_wait:
                    # Use streaming
                    response_text = await self._handle_stream_response(
                        client=client,
                        stream_url=prediction["urls"]["stream"]
                    )
                else:
                    # Use polling (even if use_wait=True, we need to poll if not complete)
                    logger.info(f"Prediction not complete, polling for results...")
                    response_text = await self._wait_for_completion(
                        client=client,
                        prediction_id=prediction_id,
                        headers={k: v for k, v in headers.items() if k != "Prefer"}
                    )
            elif status == "failed":
                error = prediction.get("error", "Unknown error")
                raise RuntimeError(f"Replicate prediction failed immediately: {error}")
            else:
                # Unknown status
                raise RuntimeError(f"Unexpected prediction status: {status}")
        
        return construct_response_from_request(
            request=request_piece,
            response_text_pieces=[response_text]
        )
    
    def _extract_output(self, prediction: dict) -> str:
        """Extract output from prediction response"""
        output = prediction.get("output", "")
        
        if isinstance(output, list):
            text = "".join(output)
        elif isinstance(output, str):
            text = output
        else:
            text = str(output)
        
        # If JSON mode is enabled, try to extract JSON from the response
        if self._is_json_supported:
            text = self._extract_json_from_text(text)
        
        return text
    
    def _extract_json_from_text(self, text: str) -> str:
        """
        Extract JSON object from text that might have extra content.
        Tries to find and extract the JSON portion if present.
        """
        import re
        
        # Try to find JSON object in the text
        # Look for text between first { and last }
        json_match = re.search(r'\{.*\}', text, re.DOTALL)
        if json_match:
            potential_json = json_match.group(0)
            # Validate it's actually JSON
            try:
                json.loads(potential_json)
                return potential_json
            except json.JSONDecodeError:
                pass
        
        # If no valid JSON found, return original text
        # The calling code will handle the error
        return text
    
    async def _handle_stream_response(self, client: httpx.AsyncClient, stream_url: str) -> str:
        """Handle streaming Server-Sent Events from Replicate"""
        full_response = ""
        
        logger.info(f"Streaming from: {stream_url}")
        
        async with client.stream(
            "GET",
            stream_url,
            headers={
                "Accept": "text/event-stream",
                "Cache-Control": "no-store"
            },
            timeout=300.0,
        ) as stream:
            async for line in stream.aiter_lines():
                if line.startswith("data: "):
                    try:
                        data = json.loads(line[6:])
                        
                        # Replicate sends output chunks in different formats
                        if "output" in data:
                            chunk = data["output"]
                            if isinstance(chunk, list):
                                # Join list items
                                full_response = "".join(chunk)
                            else:
                                # Append string chunk
                                full_response += str(chunk)
                        
                        # Check if done
                        if data.get("status") == "succeeded":
                            break
                            
                    except json.JSONDecodeError:
                        logger.warning(f"Could not parse SSE line: {line}")
                        continue
        
        # Extract JSON if needed
        if self._is_json_supported:
            full_response = self._extract_json_from_text(full_response)
        
        return full_response
    
    async def _wait_for_completion(
        self,
        client: httpx.AsyncClient,
        prediction_id: str,
        headers: dict
    ) -> str:
        """Poll for prediction completion (non-streaming mode)"""
        import asyncio
        
        get_url = f"https://api.replicate.com/v1/predictions/{prediction_id}"
        max_attempts = 60  # 5 minutes max with 5 second intervals
        
        for attempt in range(max_attempts):
            response = await client.get(get_url, headers=headers)
            response.raise_for_status()
            data = response.json()
            
            status = data.get("status")
            logger.info(f"Prediction status: {status} (attempt {attempt + 1}/{max_attempts})")
            
            if status == "succeeded":
                output = data.get("output", "")
                if isinstance(output, list):
                    text = "".join(output)
                else:
                    text = str(output)
                
                # Extract JSON if needed
                if self._is_json_supported:
                    text = self._extract_json_from_text(text)
                
                return text
            
            elif status == "failed":
                error = data.get("error", "Unknown error")
                raise RuntimeError(f"Replicate prediction failed: {error}")
            
            elif status in ["starting", "processing"]:
                # Wait and retry
                await asyncio.sleep(5)
                continue
            
            else:
                raise RuntimeError(f"Unknown prediction status: {status}")
        
        raise TimeoutError(f"Prediction did not complete within {max_attempts * 5} seconds")
    
    def _validate_request(self, *, prompt_request: PromptRequestResponse) -> None:
        """Validates that the request has exactly one text piece"""
        if len(prompt_request.request_pieces) != 1:
            raise ValueError(
                f"This target only supports a single prompt request piece. "
                f"Received: {len(prompt_request.request_pieces)} pieces."
            )
        
        piece_type = prompt_request.request_pieces[0].converted_value_data_type
        if piece_type != "text":
            raise ValueError(f"This target only supports text prompts. Received: {piece_type}")
    
    def is_json_response_supported(self) -> bool:
        """
        Returns whether JSON response format is supported.
        When True, the target will add instructions to output JSON format.
        """
        return self._is_json_supported

