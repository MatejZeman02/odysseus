"""Tests for endpoint_resolver — request header construction."""
from src.endpoint_resolver import build_headers


class TestBuildHeaders:
    def test_no_key(self):
        assert build_headers(None, "https://api.openai.com/v1") == {}

    def test_openai_bearer(self):
        assert build_headers("sk-abc", "https://api.openai.com/v1") == {"Authorization": "Bearer sk-abc"}

    def test_anthropic_headers(self):
        assert build_headers("sk-ant-abc", "https://api.anthropic.com") == {"x-api-key": "sk-ant-abc", "anthropic-version": "2023-06-01"}

    def test_empty_key(self):
        assert build_headers("", "https://api.openai.com/v1") == {}

    def test_fal_key_and_pasted_env_prefix(self):
        assert build_headers("FAL_KEY=secret", "https://fal.run/openrouter/router/openai/v1") == {
            "Authorization": "Key secret"
        }

    def test_fal_lookalike_does_not_receive_key_auth(self):
        assert build_headers("secret", "https://fal.run.evil.example/v1") == {
            "Authorization": "Bearer secret"
        }
