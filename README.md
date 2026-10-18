# agent-factory-2

Gemini + LangChain-family (Python) spec-to-code agent factory, no AWS. Design in docs/SPEC.md; environment notes in docs/environment.md.

Build: `uv sync --python 3.12 --frozen && uv run ruff check . && uv run mypy && uv run pytest -q`
