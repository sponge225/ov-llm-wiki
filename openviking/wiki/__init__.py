"""Wiki generation package."""

from .config import WikiConfig, WikiGenerationLimits


def __getattr__(name: str):
    if name == "WikiPipeline":
        from .pipeline import WikiPipeline

        return WikiPipeline
    raise AttributeError(name)

__all__ = ["WikiConfig", "WikiGenerationLimits", "WikiPipeline"]
