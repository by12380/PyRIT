# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
from typing import Optional

from openai import AsyncOpenAI

from pyrit.models import (
    PromptRequestPiece,
    PromptRequestResponse,
    construct_response_from_request,
)
from pyrit.prompt_target import PromptChatTarget, limit_requests_per_minute
from pyrit.exceptions import pyrit_target_retry

logger = logging.getLogger(__name__)


class GPT5Target(PromptChatTarget):
    """
    A prompt target for GPT-5 using the new responses.create() API with reasoning control.
    
    Args:
        api_key (str, Optional): OpenAI API key
        reasoning_effort (str): Reasoning effort level - "minimal", "low", "medium", "high"
        temperature (float, Optional): Temperature for generation
        max_requests_per_minute (int, Optional): Rate limiting
        system_prompt (str, Optional): Custom system prompt to prepend to conversation
    """

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        reasoning_effort: str = "minimal",
        temperature: Optional[float] = None,
        max_requests_per_minute: Optional[int] = None,
        system_prompt: Optional[str] = None,
        **kwargs,
    ):
        super().__init__(max_requests_per_minute=max_requests_per_minute)
        
        import os
        self._api_key = api_key or os.environ.get("OPENAI_API_KEY") or os.environ.get("PLATFORM_OPENAI_CHAT_API_KEY")
        self._reasoning_effort = reasoning_effort
        self._temperature = temperature
        self._model_name = "gpt-5"
        self._system_prompt = system_prompt
        
        if not self._api_key:
            raise ValueError("OPENAI_API_KEY environment variable or api_key parameter required")
        
        self._client = AsyncOpenAI(api_key=self._api_key)
    
    @limit_requests_per_minute
    @pyrit_target_retry
    async def send_prompt_async(self, *, prompt_request: PromptRequestResponse) -> PromptRequestResponse:
        """
        Sends a prompt to GPT-5 and returns the response.
        """
        self._validate_request(prompt_request=prompt_request)
        request_piece: PromptRequestPiece = prompt_request.request_pieces[0]
        
        # Build the input messages from conversation history
        messages = await self._build_chat_messages(
            prompt_request_pieces=[request_piece],
            conversation_id=request_piece.conversation_id
        )
        
        # Build request parameters
        # Note: GPT-5 responses API doesn't support temperature parameter
        params = {
            "model": self._model_name,
            "input": messages,
            "reasoning": {
                "effort": self._reasoning_effort
            }
        }
        
        logger.info(f"Sending request to GPT-5 with reasoning effort: {self._reasoning_effort}")
        
        # Make request using responses.create()
        response = await self._client.responses.create(**params)
        
        # Extract text from response
        response_text = self._extract_text(response)
        
        return construct_response_from_request(
            request=request_piece,
            response_text_pieces=[response_text]
        )
    
    async def _build_chat_messages(
        self,
        prompt_request_pieces: list[PromptRequestPiece],
        conversation_id: str
    ) -> list[dict]:
        """Build chat messages from conversation history"""
        messages = []
        
        # Add system prompt if provided (only once at the beginning)
        if self._system_prompt and not self._memory.get_conversation(conversation_id=conversation_id):
            messages.append({
                "role": "developer",
                "content": self._system_prompt
            })
        
        # Get conversation history from memory
        conversation_history = self._memory.get_conversation(conversation_id=conversation_id)
        
        # Build messages from history
        for response in conversation_history:
            for piece in response.request_pieces:
                if piece.role in ["user", "assistant", "system", "developer"]:
                    message = {
                        "role": piece.role,
                        "content": piece.converted_value
                    }
                    messages.append(message)
        
        # Add current request
        for piece in prompt_request_pieces:
            messages.append({
                "role": piece.role,
                "content": piece.converted_value
            })

        print(f"💬 Messages: {messages}")
        
        return messages
    
    def _extract_text(self, response) -> str:
        """Extract text from GPT-5 response object"""
        # Try output_text property first
        if hasattr(response, 'output_text') and response.output_text:
            # output_text might be a config object, convert to string
            return str(response.output_text)
        
        # Try extracting from output messages
        response_text = ""
        if hasattr(response, 'output'):
            for item in response.output:
                if hasattr(item, 'type') and item.type == 'message':
                    if hasattr(item, 'content'):
                        for content_item in item.content:
                            if hasattr(content_item, 'text'):
                                response_text += content_item.text
        
        if not response_text:
            logger.warning(f"Could not extract text from response: {response}")
            return "[Empty response]"
        
        return response_text
    
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
        """GPT-5 supports JSON mode"""
        return True

