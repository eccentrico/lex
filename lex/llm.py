"""OpenAI-compatible client. DeepSeek today; any base_url tomorrow."""
import os

from openai import OpenAI


def make_client() -> OpenAI:
    return OpenAI(api_key=os.environ["OPENAI_API_KEY"],
                  base_url=os.environ.get("OPENAI_BASE_URL",
                                          "https://api.deepseek.com"))


def default_model() -> str:
    return os.environ.get("LEX_MODEL", "deepseek-v4-flash")
