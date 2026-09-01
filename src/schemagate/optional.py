import importlib
from types import ModuleType

from schemagate.errors import MissingDependencyError

# Which extra installs what. Kept here rather than spelled into each error
# message so that renaming an extra cannot leave a message telling someone to
# install something that no longer exists.
EXTRAS: dict[str, str] = {
    "PIL": "images",
    "pillow_heif": "images",
    "pdf_inspector": "pdf",
    "pypdfium2": "ocr",
    "onnxruntime": "ocr",
    "asyncpg": "postgres",
    "fastapi": "server",
    "uvicorn": "server",
    "anthropic": "anthropic",
    "openai": "openai",
    "ollama": "ollama",
}


def require(name: str) -> ModuleType:
    """Import an optional dependency, or say which extra installs it.

    The alternative is a bare ImportError naming a package the operator never
    chose and cannot map back to anything in this project.
    """
    try:
        return importlib.import_module(name)
    except ImportError as error:
        extra = EXTRAS.get(name.split(".")[0])
        install = f"pip install 'schemagate[{extra}]'" if extra else f"pip install {name}"
        raise MissingDependencyError(
            f"This needs {name}, which is not installed. Run `{install}`, "
            f"or `pip install 'schemagate[all]'` for everything."
        ) from error
