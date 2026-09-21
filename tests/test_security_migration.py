"""Offline compatibility checks for the coordinated LangChain security upgrade."""
import json
import socket

import httpx
from langchain_core.messages import HumanMessage
from pydantic import BaseModel
import pytest


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "false")

    def blocked(*args, **kwargs):
        raise AssertionError("Migration tests must not contact external services")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)


@pytest.mark.parametrize("provider,model", [
    ("OpenAI", "gpt-4.1"),
    ("Anthropic", "claude-sonnet-4-20250514"),
    ("DeepSeek", "deepseek-chat"),
    ("Google", "gemini-2.5-flash"),
    ("Groq", "llama-3.3-70b-versatile"),
    ("xAI", "grok-3"),
    ("GigaChat", "GigaChat"),
    ("OpenRouter", "test/model"),
    ("local", "local-test-model"),
])
def test_existing_provider_factories_construct_offline(provider, model, monkeypatch):
    from src.llm.models import ModelProvider, get_model

    for key in ["GIGACHAT_USER", "GIGACHAT_PASSWORD", "OPENAI_API_BASE"]:
        monkeypatch.delenv(key, raising=False)
    credentials = {name + "_API_KEY": "synthetic-offline-test" for name in ["OPENAI", "ANTHROPIC", "DEEPSEEK", "GOOGLE", "GROQ", "XAI", "GIGACHAT", "OPENROUTER"]}
    if provider == "local":
        provider = "LlamaCpp" if hasattr(ModelProvider, "LLAMACPP") else "Ollama"
    client = get_model(model, ModelProvider(provider), credentials)
    assert callable(client.invoke)


def test_local_json_mode_uses_chat_completions_and_parses_schema(monkeypatch):
    from src.llm import models

    class Result(BaseModel):
        status: str
        count: int

    requests = []

    def respond(request):
        requests.append(request)
        body = json.loads(request.content)
        assert request.url.path == "/v1/chat/completions"
        assert body["model"] == "local-test-model"
        assert body["response_format"] == {"type": "json_object"}
        return httpx.Response(200, json={
            "id": "test-completion", "object": "chat.completion", "created": 1,
            "model": "local-test-model",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": '{"status":"ok","count":2}'}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        })

    transport = httpx.MockTransport(respond)
    real_client = models.ChatOpenAI
    with httpx.Client(transport=transport) as client:
        monkeypatch.setattr(models, "ChatOpenAI", lambda **kwargs: real_client(**kwargs, http_client=client))
        monkeypatch.setenv("OPENAI_API_BASE", "http://127.0.0.1:8080/v1")
        model = models.get_model("local-test-model", models.ModelProvider.OPENAI, {"OPENAI_API_KEY": "synthetic-offline-test"})
        result = model.with_structured_output(Result, method="json_mode").invoke([HumanMessage(content="Return JSON with status and count")])
    assert result == Result(status="ok", count=2)
    assert len(requests) == 1


def test_application_graph_fanout_fanin_preserves_state(monkeypatch):
    import src.main as application

    def analyst_a(state):
        return {"data": {"a": True}}

    def analyst_b(state):
        return {"data": {"b": True}}

    def risk(state):
        assert state["data"]["a"] and state["data"]["b"]
        return {"data": {"risk_checked": True}}

    def portfolio(state):
        assert state["data"]["risk_checked"]
        return {"messages": [HumanMessage(content='{"TEST":{"action":"hold"}}')]}

    monkeypatch.setattr(application, "get_analyst_nodes", lambda: {"a": ("analyst_a", analyst_a), "b": ("analyst_b", analyst_b)})
    monkeypatch.setattr(application, "risk_management_agent", risk)
    monkeypatch.setattr(application, "portfolio_management_agent", portfolio)
    graph = application.create_workflow(["a", "b"]).compile()
    state = graph.invoke({"messages": [HumanMessage(content="synthetic")], "data": {}, "metadata": {"source": "test"}})
    assert json.loads(state["messages"][-1].content) == {"TEST": {"action": "hold"}}
    assert state["metadata"] == {"source": "test"}


def test_deserializer_does_not_resolve_environment_secret_by_default(monkeypatch):
    from langchain_core.load import dumps, loads

    monkeypatch.setenv("MIGRATION_TEST_SECRET", "synthetic-do-not-resolve")
    payload = {"lc": 1, "type": "secret", "id": ["MIGRATION_TEST_SECRET"]}
    assert loads(json.dumps(payload)) is None
    assert loads(dumps(payload)) == payload
