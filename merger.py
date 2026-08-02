"""
Merge outbound dicts into a full Xray config.

replace_outbounds() is the only public function here.
- If an outbound with the same tag already exists  -> replace it in-place.
- If it does not exist                             -> append it.

The rotation / state-tracking logic from xui-auto has been intentionally removed.
Slot selection is now fully driven by the bot commands (/fill, /replace, /checkall).
"""

import logging

import config

logger = logging.getLogger(__name__)


def slot_tag(index_1_based: int) -> str:
    """Return the canonical tag string for a 1-based slot index (e.g. 3 -> 'out03')."""
    return f"{config.SLOT_TAG_PREFIX}{index_1_based:0{config.SLOT_TAG_DIGITS}d}"


def replace_outbounds(xray_cfg: dict, tag_to_outbound: dict) -> dict:
    """
    xray_cfg       : full Xray config dict (must contain "outbounds" key)
    tag_to_outbound: mapping of tag -> outbound dict (with optional _meta key)

    Mutates and returns xray_cfg.
    """
    outbounds: list = xray_cfg.get("outbounds", [])
    existing_index: dict[str, int] = {
        ob.get("tag"): i for i, ob in enumerate(outbounds)
    }

    for tag, new_ob in tag_to_outbound.items():
        clean_ob = {k: v for k, v in new_ob.items() if k != "_meta"}
        clean_ob["tag"] = tag

        if tag in existing_index:
            outbounds[existing_index[tag]] = clean_ob
            logger.info("Replaced outbound  tag=%s", tag)
        else:
            outbounds.append(clean_ob)
            logger.info("Added new outbound  tag=%s", tag)

    xray_cfg["outbounds"] = outbounds
    return xray_cfg
