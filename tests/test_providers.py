import configparser
import httpx
import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from openai import APIStatusError

from arxiv_assistant.filters.filter_gpt import call_model, call_parsed_model, call_provider, parse_abstract_response, parse_title_response


def completion(content, model):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        model=model,
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, model_extra={}),
    )


def provider(name, model, responses, max_requests=-1, query_cnt=0):
    create = Mock(side_effect=responses)
    return {
        "name": name,
        "model": model,
        "limit_per_minute": -1,
        "max_requests": max_requests,
        "client": SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))),
        "last_query_time": None,
        "query_cnt": query_cnt,
        "models_used": [],
    }


class ProviderCascadeTests(unittest.TestCase):
    def test_request_budget_advances_to_next_provider(self):
        exhausted = provider("google", "gemini", [], max_requests=1, query_cnt=1)
        fallback_completion = completion("[]", "deepseek-chat")
        fallback = provider("deepseek", "deepseek-chat", [fallback_completion])
        providers = [exhausted, fallback]

        result, used_provider = call_model("system", "user", providers)

        self.assertIs(result, fallback_completion)
        self.assertIs(used_provider, fallback)
        self.assertEqual(providers, [fallback])

    def test_authentication_error_advances_to_next_provider(self):
        response = httpx.Response(401, request=httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions"))
        unauthorized = APIStatusError("Missing Authentication", response=response, body={"error": {"message": "Missing Authentication"}})
        openrouter = provider("openrouter", "openrouter/free", [unauthorized])
        fallback_completion = completion("[]", "deepseek-chat")
        deepseek = provider("deepseek", "deepseek-chat", [fallback_completion])
        providers = [openrouter, deepseek]

        result, used_provider = call_model("system", "user", providers)

        self.assertIs(result, fallback_completion)
        self.assertIs(used_provider, deepseek)
        self.assertEqual(providers, [deepseek])

    def test_invalid_response_advances_and_records_routed_models(self):
        openrouter = provider("openrouter", "openrouter/free", [completion("not json", "qwen/qwen3.8-27b:free")])
        deepseek_completion = completion('["1234.5678"]', "deepseek-chat")
        deepseek = provider("deepseek", "deepseek-chat", [deepseek_completion])
        providers = [openrouter, deepseek]

        result, used_provider, parsed, attempts = call_parsed_model(
            "system", "user", providers, lambda response: parse_title_response(response, {"1234.5678"}),
        )

        self.assertIs(result, deepseek_completion)
        self.assertIs(used_provider, deepseek)
        self.assertEqual(parsed, {"1234.5678"})
        self.assertEqual([attempt_provider["name"] for _, attempt_provider in attempts], ["openrouter", "deepseek"])
        self.assertEqual(openrouter["models_used"], ["qwen/qwen3.8-27b:free"])
        self.assertEqual(deepseek["models_used"], ["deepseek-chat"])

    def test_abstract_response_must_cover_every_expected_paper(self):
        config = configparser.ConfigParser()
        config.read_dict({"OUTPUT": {"debug_messages": "false"}})
        first = {"ARXIVID": "1", "COMMENT": "", "RELEVANCE": 5, "NOVELTY": 4}
        second = {"ARXIVID": "2", "COMMENT": "match", "RELEVANCE": 8, "NOVELTY": 7}
        complete = completion("\n".join(map(json.dumps, (first, second))), "model")
        incomplete = completion(json.dumps(first), "model")

        self.assertEqual(parse_abstract_response(complete, {"1", "2"}, config), [first, second])
        self.assertIsNone(parse_abstract_response(incomplete, {"1", "2"}, config))

    def test_call_provider_records_actual_model(self):
        routed_completion = completion("[]", "google/gemma-4-31b-it:free")
        openrouter = provider("openrouter", "openrouter/free", [routed_completion])

        self.assertIs(call_provider("system", "user", openrouter), routed_completion)
        self.assertEqual(openrouter["models_used"], ["google/gemma-4-31b-it:free"])


if __name__ == "__main__":
    unittest.main()
