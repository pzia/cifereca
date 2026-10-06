"""Entry points for independently resolvable Core041 ``cifereca.device.profile/1`` plugins."""
from cifereca_epub_optimizer.builtin_profiles import (
    GENERIC_EINK_PLUGIN,
    KOBO_CLARA_BW_PLUGIN,
    XTEINK_X3_PLUGIN,
    XTEINK_X4_PLUGIN,
)

__all__ = ["XTEINK_X3_PLUGIN", "XTEINK_X4_PLUGIN", "KOBO_CLARA_BW_PLUGIN", "GENERIC_EINK_PLUGIN"]
