"""Smoke test: the ollama SDK is importable and exposes the expected surface."""


def test_ollama_imports():
    import ollama
    assert hasattr(ollama, "Client")
    assert hasattr(ollama, "AsyncClient")


def test_ollama_response_error_class():
    from ollama import ResponseError
    assert issubclass(ResponseError, Exception)
