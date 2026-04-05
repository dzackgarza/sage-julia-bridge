set shell := ["bash", "-lc"]

install:
    sage -python -m pip install -e .

test:
    sage -python -m unittest discover -s tests

lint:
    uv run --with ruff ruff check src tests

fmt:
    uv run --with ruff ruff format src tests

build:
    uv build
