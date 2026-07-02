from .base import Provider
from .redfin import RedfinProvider
from .zillow_rapidapi import ZillowRapidAPIProvider


def make_provider(name: str, *, rapidapi_key: str = "") -> Provider:
    """Build the provider named in criteria.yaml. Keeping construction here
    means the pipeline stays ignorant of provider-specific arguments."""
    if name == "zillow_rapidapi":
        return ZillowRapidAPIProvider(api_key=rapidapi_key)
    if name == "redfin":
        return RedfinProvider()
    raise ValueError(f"Unknown provider {name!r}. Known: zillow_rapidapi, redfin")


__all__ = ["Provider", "RedfinProvider", "ZillowRapidAPIProvider", "make_provider"]
