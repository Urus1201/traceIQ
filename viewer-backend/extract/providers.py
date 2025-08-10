from __future__ import annotations
import json, os
from typing import Dict
from openai import AzureOpenAI
from extract.llm_fallback import LLMProvider

def _best_effort_json(text: str) -> Dict:
    try:
        return json.loads(text)
    except Exception:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(text[start:end+1])
            except Exception:
                return {}
        return {}

class AzureOAIProvider(LLMProvider):
    """
    Env:
      AZURE_OPENAI_ENDPOINT      e.g. https://<your-resource>.openai.azure.com/
      AZURE_OPENAI_API_KEY       your key
      AZURE_OPENAI_API_VERSION   e.g. 2024-10-21 (latest GA at time of writing)
      AZURE_OPENAI_DEPLOYMENT    your deployment name, e.g. 'o3-pro'
    """
    def __init__(self):
        endpoint = os.environ["AZURE_OPENAI_ENDPOINT"]
        api_key = os.environ["AZURE_OPENAI_API_KEY"]
        api_version = os.getenv("AZURE_OPENAI_API_VERSION", "2024-12-01-preview")
        self.deployment = os.environ.get("AZURE_OPENAI_DEPLOYMENT", "o3")
        self.client = AzureOpenAI(
            api_key=api_key,
            api_version=api_version,
            azure_endpoint=endpoint,
        )

    def infer(self, prompt: str) -> dict:
        # Use the Responses API (required for Omni/o3 deployments in Azure). The deployment name is passed as `model`.
        resp = self.client.responses.create(
            model=self.deployment,
            input=prompt,
            max_output_tokens=2000,
        )
        # `output_text` is provided by the SDK for Responses API
        text = getattr(resp, "output_text", None) or str(resp)
        return _best_effort_json(text)

def build_provider_from_env():
    # If Azure env is present, use it; otherwise return None and you’ll run baseline-only.
    if os.getenv("AZURE_OPENAI_ENDPOINT") and os.getenv("AZURE_OPENAI_API_KEY"):
        return AzureOAIProvider()
    return None
