"""Generate and validate store-compliant App Store and Google Play screenshots."""

from apppreview.generate import generate
from apppreview.graphic import generate_graphic
from apppreview.specs import Target, get_target, load_targets
from apppreview.validate import identify, validate_images

__version__ = "0.2.0"

__all__ = [
    "Target",
    "__version__",
    "generate",
    "generate_graphic",
    "get_target",
    "identify",
    "load_targets",
    "validate_images",
]
