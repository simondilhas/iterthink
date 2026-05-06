"""AI provider abstraction layer for multiple model providers."""
from abc import ABC, abstractmethod
from typing import List, Dict, Optional
import httpx
import os


class AIProvider(ABC):
    """Abstract base class for AI providers."""
    
    def __init__(self, api_key: Optional[str] = None):
        if api_key:
            self.api_key = api_key
        else:
            # Try to get from environment if no key provided
            env_key = self.get_env_key()
            self.api_key = os.getenv(env_key, "")
        self.http_client = httpx.Client(
            follow_redirects=True,
            timeout=httpx.Timeout(60.0, connect=10.0)
        )
    
    @abstractmethod
    def get_env_key(self) -> str:
        """Return the environment variable key for this provider's API key."""
        pass
    
    @abstractmethod
    def generate(
        self,
        messages: List[Dict[str, str]],
        model: str,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None
    ) -> str:
        """Generate a response from the AI model."""
        pass
    
    def generate_with_usage(
        self,
        messages: List[Dict[str, str]],
        model: str,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None
    ) -> tuple[str, dict]:
        """Generate a response and return content with usage information."""
        content = self.generate(messages, model, temperature, max_tokens)
        usage = self.get_last_usage()
        return content, usage
    
    def get_last_usage(self) -> dict:
        """Get token usage from last API call. Override in subclasses."""
        return {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    
    def has_api_key(self) -> bool:
        """Check if API key is configured."""
        return bool(self.api_key and self.api_key.strip())


class OpenAIProvider(AIProvider):
    """OpenAI (ChatGPT) provider implementation."""
    
    def __init__(self, api_key: Optional[str] = None):
        super().__init__(api_key)
        self._last_usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    
    def get_env_key(self) -> str:
        return "OPENAI_API_KEY"
    
    def generate(
        self,
        messages: List[Dict[str, str]],
        model: str,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None
    ) -> str:
        if not self.has_api_key():
            raise ValueError("OpenAI API key not configured")
        
        from openai import OpenAI
        
        client = OpenAI(
            api_key=self.api_key,
            http_client=self.http_client
        )
        
        kwargs = {
            "model": model,
            "messages": messages
        }
        
        # Check if this model has temperature restrictions (e.g., GPT-5 only supports temperature=1)
        # Look up the model in MODEL_REGISTRY by matching model_id
        # Note: MODEL_REGISTRY is defined below, so we'll check it at runtime
        temperature_to_use = temperature
        if model == "gpt-5":
            # GPT-5 only supports temperature=1 (default)
            temperature_to_use = 1.0
        
        kwargs["temperature"] = temperature_to_use
        
        if max_tokens:
            kwargs["max_tokens"] = max_tokens
        
        response = client.chat.completions.create(**kwargs)
        
        # Store usage information
        if hasattr(response, 'usage') and response.usage:
            self._last_usage = {
                "input_tokens": response.usage.prompt_tokens if hasattr(response.usage, 'prompt_tokens') else 0,
                "output_tokens": response.usage.completion_tokens if hasattr(response.usage, 'completion_tokens') else 0,
                "total_tokens": response.usage.total_tokens if hasattr(response.usage, 'total_tokens') else 0
            }
        
        return response.choices[0].message.content.strip()
    
    def get_last_usage(self) -> dict:
        """Get token usage from last API call."""
        return self._last_usage


class InfomaniakProvider(AIProvider):
    """Infomaniak-hosted Mistral provider."""
    
    def __init__(self, api_key: Optional[str] = None):
        self._product_id = os.getenv("INFOMANIAK_PRODUCT_ID", "").strip()
        if not self._product_id:
            raise ValueError("Infomaniak integration requires INFOMANIAK_PRODUCT_ID to be set")
        super().__init__(api_key)
        self._last_usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    
    def get_env_key(self) -> str:
        return "INFOMANIAK_API_KEY"
    
    def generate(
        self,
        messages: List[Dict[str, str]],
        model: str,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None
    ) -> str:
        if not self.has_api_key():
            raise ValueError("Infomaniak API key not configured")
        
        from openai import OpenAI
        
        base_url = f"https://api.infomaniak.com/1/ai/{self._product_id}/openai"
        
        client = OpenAI(
            api_key=self.api_key,
            base_url=base_url,
            http_client=self.http_client
        )
        
        kwargs = {
            "model": model,
            "messages": messages,
            "temperature": temperature
        }
        # Infomaniak API requires max_tokens to be between 1 and 5000
        if max_tokens:
            kwargs["max_tokens"] = min(max(max_tokens, 1), 5000)
        else:
            # Default to 4000 if not provided (safe default within limit)
            kwargs["max_tokens"] = 4000
        
        # Add retry logic for rate limit errors
        import time
        from openai import RateLimitError, APIError
        
        max_retries = 3
        base_delay = 2  # Start with 2 seconds delay
        
        for attempt in range(max_retries):
            try:
                response = client.chat.completions.create(**kwargs)
                
                if hasattr(response, 'usage') and response.usage:
                    self._last_usage = {
                        "input_tokens": response.usage.prompt_tokens if hasattr(response.usage, 'prompt_tokens') else 0,
                        "output_tokens": response.usage.completion_tokens if hasattr(response.usage, 'completion_tokens') else 0,
                        "total_tokens": response.usage.total_tokens if hasattr(response.usage, 'total_tokens') else 0
                    }
                
                return response.choices[0].message.content.strip()
            
            except (RateLimitError, APIError) as e:
                # Check if it's a rate limit error (429 status)
                is_rate_limit = (
                    isinstance(e, RateLimitError) or
                    (hasattr(e, 'status_code') and e.status_code == 429) or
                    "rate_limit" in str(e).lower() or
                    "429" in str(e)
                )
                
                if is_rate_limit and attempt < max_retries - 1:
                    # Exponential backoff: 2s, 4s, 8s
                    delay = base_delay * (2 ** attempt)
                    print(f"Infomaniak API rate limit hit. Retrying in {delay} seconds... (attempt {attempt + 1}/{max_retries})")
                    time.sleep(delay)
                    continue
                else:
                    # Last attempt failed or not a rate limit error, raise
                    raise
    
    def get_last_usage(self) -> dict:
        return self._last_usage


class MistralProvider(AIProvider):
    """Mistral AI provider implementation (official API)."""
    
    def __init__(self, api_key: Optional[str] = None):
        super().__init__(api_key)
        self._last_usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    
    def get_env_key(self) -> str:
        return "MISTRAL_API_KEY"
    
    def generate(
        self,
        messages: List[Dict[str, str]],
        model: str,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None
    ) -> str:
        if not self.has_api_key():
            raise ValueError("Mistral API key not configured")
        
        from openai import OpenAI
        
        client = OpenAI(
            api_key=self.api_key,
            base_url="https://api.mistral.ai/v1",
            http_client=self.http_client
        )
        
        kwargs = {
            "model": model,
            "messages": messages,
            "temperature": temperature
        }
        if max_tokens:
            kwargs["max_tokens"] = max_tokens
        
        response = client.chat.completions.create(**kwargs)
        
        if hasattr(response, 'usage') and response.usage:
            self._last_usage = {
                "input_tokens": response.usage.prompt_tokens if hasattr(response.usage, 'prompt_tokens') else 0,
                "output_tokens": response.usage.completion_tokens if hasattr(response.usage, 'completion_tokens') else 0,
                "total_tokens": response.usage.total_tokens if hasattr(response.usage, 'total_tokens') else 0
            }
        
        return response.choices[0].message.content.strip()
    
    def get_last_usage(self) -> dict:
        return self._last_usage


class AnthropicProvider(AIProvider):
    """Anthropic (Claude) provider implementation."""
    
    def __init__(self, api_key: Optional[str] = None):
        super().__init__(api_key)
        self._last_usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    
    def get_env_key(self) -> str:
        return "ANTHROPIC_API_KEY"
    
    def generate(
        self,
        messages: List[Dict[str, str]],
        model: str,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None
    ) -> str:
        if not self.has_api_key():
            raise ValueError("Anthropic API key not configured")
        
        try:
            from anthropic import Anthropic
        except ImportError:
            raise ImportError("anthropic package is required. Install with: pip install anthropic")
        
        client = Anthropic(api_key=self.api_key)
        
        # Extract system message if present
        system_message = None
        user_messages = []
        
        for msg in messages:
            if msg["role"] == "system":
                system_message = msg["content"]
            else:
                user_messages.append(msg)
        
        kwargs = {
            "model": model,
            "messages": user_messages,
            "temperature": temperature,
            "max_tokens": max_tokens or 4096
        }
        
        if system_message:
            kwargs["system"] = system_message
        
        response = client.messages.create(**kwargs)
        
        # Store usage information
        if hasattr(response, 'usage'):
            self._last_usage = {
                "input_tokens": response.usage.input_tokens if hasattr(response.usage, 'input_tokens') else 0,
                "output_tokens": response.usage.output_tokens if hasattr(response.usage, 'output_tokens') else 0,
                "total_tokens": (response.usage.input_tokens + response.usage.output_tokens) if hasattr(response.usage, 'input_tokens') and hasattr(response.usage, 'output_tokens') else 0
            }
        
        # Extract text from response
        text_parts = []
        for content_block in response.content:
            if content_block.type == "text":
                text_parts.append(content_block.text)
        
        return "\n".join(text_parts).strip()
    
    def get_last_usage(self) -> dict:
        """Get token usage from last API call."""
        return self._last_usage


# Model Registry
MODEL_REGISTRY: Dict[str, Dict[str, any]] = {
    # Infomaniak Hosted Models
    "infomaniak-mistral-small": {
        "provider": "infomaniak",
        "model_id": "mistral3",
        "display_name": "Infomaniak - Mistral Small (CH)",
        "best_for": "Privacy, Quick edits",
        "provider_class": InfomaniakProvider
    },
    "infomaniak-llama": {
        "provider": "infomaniak",
        "model_id": "llama3",
        "display_name": "Infomaniak - Llama (CH)",
        "best_for": "Privacy, Writing help",
        "provider_class": InfomaniakProvider
    },
    
    # Official Mistral Models
    "mistral-pixtral-12b": {
        "provider": "mistral",
        "model_id": "pixtral-12b-2409",
        "display_name": "Mistral - Pixtral 12B (EU)",
        "best_for": "Small Edits",
        "provider_class": MistralProvider
    },
    "mistral-large-latest": {
        "provider": "mistral",
        "model_id": "mistral-large-latest",
        "display_name": "Mistral - Large (EU)",
        "best_for": "Complex reasoning",
        "provider_class": MistralProvider
    },
    "mistral-small-latest": {
        "provider": "mistral",
        "model_id": "mistral-small-latest",
        "display_name": "Mistral - Small (EU)",
        "best_for": "Fast replies",
        "provider_class": MistralProvider
    },
    
    # OpenAI Models
    "gpt-5": {
        "provider": "openai",
        "model_id": "gpt-5",
        "display_name": "OpenAI - GPT-5 (US)",
        "best_for": "Advanced reasoning",
        "provider_class": OpenAIProvider,
        "temperature_fixed": 1.0  # GPT-5 only supports default temperature of 1
    },
    "gpt-4o": {
        "provider": "openai",
        "model_id": "gpt-4o",
        "display_name": "OpenAI - GPT-4o (US)",
        "best_for": "Balanced tone",
        "provider_class": OpenAIProvider
    },
    "gpt-4o-mini": {
        "provider": "openai",
        "model_id": "gpt-4o-mini",
        "display_name": "OpenAI - GPT-4o Mini (US)",
        "best_for": "Cost saver",
        "provider_class": OpenAIProvider
    },

    "gpt-4": {
        "provider": "openai",
        "model_id": "gpt-4",
        "display_name": "OpenAI - GPT-4 (US)",
        "best_for": "Reliable edits",
        "provider_class": OpenAIProvider
    },
    
    # Anthropic Models
    "claude-3-5-sonnet": {
        "provider": "anthropic",
        "model_id": "claude-3-5-sonnet",
        "display_name": "Anthropic - Claude 3.5 Sonnet (US)",
        "best_for": "Structured analysis",
        "provider_class": AnthropicProvider
    },
    "claude-3-opus": {
        "provider": "anthropic",
        "model_id": "claude-3-opus",
        "display_name": "Anthropic - Claude 3 Opus (US)",
        "best_for": "Complex reasoning",
        "provider_class": AnthropicProvider
    },
    "claude-3-haiku": {
        "provider": "anthropic",
        "model_id": "claude-3-haiku-20240307",
        "display_name": "Anthropic - Claude 3 Haiku (US)",
        "best_for": "Rapid drafts",
        "provider_class": AnthropicProvider
    }
}

# Default models for different use cases
DEFAULT_MODELS = {
    "general": "infomaniak-mistral-small",  # Default for general use
    "summary": "infomaniak-mistral-small",   # Default for summaries
    "preloaded": "infomaniak-mistral-small"  # Default for preloaded content
}


def get_provider_for_model(model_key: str, api_key: Optional[str] = None) -> AIProvider:
    """Get the appropriate provider instance for a model."""
    if model_key not in MODEL_REGISTRY:
        # Fallback: try to infer from model key
        if model_key.startswith("gpt-") or model_key.startswith("o1-"):
            return OpenAIProvider(api_key)
        elif "infomaniak" in model_key.lower():
            return InfomaniakProvider(api_key)
        elif "mistral" in model_key.lower() or "pixtral" in model_key.lower():
            return MistralProvider(api_key)
        elif "claude" in model_key.lower():
            return AnthropicProvider(api_key)
        else:
            # Default to OpenAI for backward compatibility
            return OpenAIProvider(api_key)
    
    provider_class = MODEL_REGISTRY[model_key]["provider_class"]
    return provider_class(api_key)


def get_model_id(model_key: str) -> str:
    """Get the actual model ID to send to the API."""
    if model_key in MODEL_REGISTRY:
        return MODEL_REGISTRY[model_key]["model_id"]
    return model_key  # Return as-is if not in registry


def get_default_model(use_case: str = "general") -> str:
    """Get the default model for a specific use case."""
    return DEFAULT_MODELS.get(use_case, DEFAULT_MODELS["general"])


def get_available_models() -> Dict[str, Dict[str, any]]:
    """Get all available models grouped by provider."""
    models_by_provider = {}
    for model_key, model_info in MODEL_REGISTRY.items():
        provider = model_info["provider"]
        if provider not in models_by_provider:
            models_by_provider[provider] = []
        models_by_provider[provider].append({
            "key": model_key,
            "display_name": model_info["display_name"],
            "model_id": model_info["model_id"]
        })
    return models_by_provider

