"""Visualizador de barras elétricas georreferenciadas."""

from .circuit_colors import (
    contrast_ratio_with_white,
    generate_circuit_palette,
    normalize_hex_color,
)
from .model import (
    BarRecord,
    CircuitCatalogModel,
    CircuitDefinition,
    CircuitMembership,
    Bounds,
    CircuitModel,
    CircuitVisibilityController,
    FeatureSelection,
    LineNetworkModel,
    NetworkTopology,
    SegmentRecord,
    StaticPointIndex,
    StaticSegmentIndex,
    SwitchModel,
    SwitchRecord,
    UtmCrs,
)

__all__ = [
    "BarRecord",
    "CircuitCatalogModel",
    "CircuitDefinition",
    "CircuitMembership",
    "Bounds",
    "CircuitModel",
    "CircuitVisibilityController",
    "contrast_ratio_with_white",
    "FeatureSelection",
    "generate_circuit_palette",
    "LineNetworkModel",
    "NetworkTopology",
    "normalize_hex_color",
    "SegmentRecord",
    "StaticPointIndex",
    "StaticSegmentIndex",
    "SwitchModel",
    "SwitchRecord",
    "UtmCrs",
]

__version__ = "0.1.0"
