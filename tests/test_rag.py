from agents.rag import get_or_create_vector_store


def test_existing_vector_store_uses_configured_embedding_builder(
    monkeypatch, tmp_path
) -> None:
    persist_directory = tmp_path / "chroma.db"
    persist_directory.mkdir()
    build_calls: list[bool] = []
    configured_model = object()
    chroma_calls: list[dict] = []

    def fake_build_embedding_model():
        build_calls.append(True)
        return configured_model

    def fake_chroma(**kwargs):
        chroma_calls.append(kwargs)
        return object()

    monkeypatch.setattr(
        "agents.rag.build_embedding_model_from_env", fake_build_embedding_model
    )
    monkeypatch.setattr("agents.rag.Chroma", fake_chroma)

    get_or_create_vector_store(str(tmp_path / "cookbook"), str(persist_directory))

    assert build_calls == [True]
    assert chroma_calls == [
        {
            "persist_directory": str(persist_directory),
            "embedding_function": configured_model,
        }
    ]
