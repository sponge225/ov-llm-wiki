"""Wiki MVP generation package."""

from .config import WikiMVPConfig, WikiMVPGenerationLimits


def __getattr__(name: str):
    if name == "WikiMVPPipeline":
        from .pipeline import WikiMVPPipeline

        return WikiMVPPipeline
    raise AttributeError(name)

__all__ = ["WikiMVPConfig", "WikiMVPGenerationLimits", "WikiMVPPipeline"]
