"""
tg-xui-manager  —  Telegram bot to manage 3x-ui panel outbounds.

Commands
--------
/fill N        Fill slots out01..out0N with healthy, non-duplicate candidates.
/replace 1,5   Replace specific slots directly (no health check).
/checkall      Health-check all managed slots; replace only the failed ones.
/status        List all managed slots (address / port / protocol).
/setup         Interactive wizard to change panel URL, username, and password.
"""

import logging
import re
import sys

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

import config
import converter
import healthcheck
import merger
import panel_client
import scraper

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    level=logging.INFO,
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Auth guard
# ---------------------------------------------------------------------------

def _allowed(update: Update) -> bool:
    return update.effective_user.id in config.ALLOWED_USERS


async def _reject(update: Update) -> None:
    """Silently ignore messages from unauthorised users."""
    logger.warning(
        "Unauthorised access attempt from user_id=%s", update.effective_user.id
    )


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _slot_tag(n: int) -> str:
    return merger.slot_tag(n)


def _managed_slots(xray_cfg: dict) -> list[dict]:
    """Return outbounds whose tag matches the managed pattern (out01, out02, …)."""
    pattern = re.compile(
        rf"^{re.escape(config.SLOT_TAG_PREFIX)}\d{{{config.SLOT_TAG_DIGITS}}}$"
    )
    return [ob for ob in xray_cfg.get("outbounds", []) if pattern.match(ob.get("tag", ""))]


def _current_addresses(xray_cfg: dict) -> set[str]:
    """Collect all addresses currently used by managed outbounds (string match, no DNS)."""
    addrs: set[str] = set()
    for ob in _managed_slots(xray_cfg):
        proto = ob.get("protocol", "")
        try:
            if proto in ("vless", "vmess"):
                addr = ob["settings"]["vnext"][0]["address"]
            elif proto in ("trojan", "shadowsocks"):
                addr = ob["settings"]["servers"][0]["address"]
            else:
                continue
            addrs.add(addr)
        except (KeyError, IndexError):
            pass
    return addrs


def _address_of(outbound: dict) -> str | None:
    return outbound.get("_meta", {}).get("address")


def _fetch_next_candidate(
    existing_addresses: set[str],
    already_used_in_this_run: set[str],
    candidates: list[str],
    candidate_cursor: list[int],   # mutable int wrapper
) -> dict | None:
    """
    Walk the candidate list from cursor, parse + health-check each one,
    skip duplicates (address string match), return the first good one or None.
    """
    while candidate_cursor[0] < len(candidates):
        link = candidates[candidate_cursor[0]]
        candidate_cursor[0] += 1
        ob = converter.parse_link(link)
        if ob is None:
            continue
        addr = _address_of(ob)
        if addr and (addr in existing_addresses or addr in already_used_in_this_run):
            logger.info("Skipping duplicate address: %s", addr)
            continue
        if not healthcheck.is_healthy(ob):
            continue
        if addr:
            already_used_in_this_run.add(addr)
        return ob
    return None


def _get_panel() -> panel_client.PanelClient:
    return panel_client.PanelClient()


# ---------------------------------------------------------------------------
# /fill N
# ---------------------------------------------------------------------------

async def cmd_fill(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _allowed(update):
        return await _reject(update)

    args = context.args
    if not args or not args[0].isdigit():
        await update.message.reply_text("Usage: /fill <N>\nExample: /fill 5")
        return

    n = int(args[0])
    if n < 1:
        await update.message.reply_text("N must be >= 1.")
        return

    await update.message.reply_text(f"⏳ Filling {n} slot(s)…")

    try:
        client = _get_panel()
        client.login()
        xray_cfg = client.get_xray_config()
    except Exception as e:
        await update.message.reply_text(f"❌ Panel error: {e}")
        return

    existing_addresses = _current_addresses(xray_cfg)
    candidates = scraper.collect_candidate_configs(limit=config.CANDIDATES_TO_FETCH)
    if not candidates:
        await update.message.reply_text("❌ Could not fetch candidates from source.")
        return

    cursor = [0]
    used_this_run: set[str] = set()
    tag_to_outbound: dict[str, dict] = {}
    failed_slots: list[int] = []

    for i in range(1, n + 1):
        ob = _fetch_next_candidate(existing_addresses, used_this_run, candidates, cursor)
        if ob is None:
            failed_slots.append(i)
        else:
            tag_to_outbound[_slot_tag(i)] = ob

    if tag_to_outbound:
        xray_cfg = merger.replace_outbounds(xray_cfg, tag_to_outbound)
        try:
            client.save_xray_config(xray_cfg)
        except Exception as e:
            await update.message.reply_text(f"❌ Failed to save config: {e}")
            return

    lines = [f"✅ /fill {n} done."]
    for tag in tag_to_outbound:
        ob = tag_to_outbound[tag]
        meta = ob.get("_meta", {})
        lines.append(
            f"  {tag}  →  {meta.get('address')}:{meta.get('port')}  [{ob.get('protocol')}]"
        )
    if failed_slots:
        lines.append(f"\n⚠️ No healthy candidate found for slot(s): {failed_slots}")

    await update.message.reply_text("\n".join(lines))


# ---------------------------------------------------------------------------
# /replace 1,5,8
# ---------------------------------------------------------------------------

async def cmd_replace(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _allowed(update):
        return await _reject(update)

    raw = " ".join(context.args)
    slot_numbers = [int(x) for x in re.findall(r"\d+", raw)]
    if not slot_numbers:
        await update.message.reply_text(
            "Usage: /replace <slot numbers>\nExample: /replace 1,5,8"
        )
        return

    await update.message.reply_text(f"⏳ Replacing slot(s): {slot_numbers}…")

    try:
        client = _get_panel()
        client.login()
        xray_cfg = client.get_xray_config()
    except Exception as e:
        await update.message.reply_text(f"❌ Panel error: {e}")
        return

    existing_addresses = _current_addresses(xray_cfg)
    candidates = scraper.collect_candidate_configs(limit=config.CANDIDATES_TO_FETCH)
    if not candidates:
        await update.message.reply_text("❌ Could not fetch candidates from source.")
        return

    cursor = [0]
    used_this_run: set[str] = set()
    tag_to_outbound: dict[str, dict] = {}
    failed_slots: list[int] = []

    for i in slot_numbers:
        ob = _fetch_next_candidate(existing_addresses, used_this_run, candidates, cursor)
        if ob is None:
            failed_slots.append(i)
        else:
            tag_to_outbound[_slot_tag(i)] = ob

    if tag_to_outbound:
        xray_cfg = merger.replace_outbounds(xray_cfg, tag_to_outbound)
        try:
            client.save_xray_config(xray_cfg)
        except Exception as e:
            await update.message.reply_text(f"❌ Failed to save config: {e}")
            return

    lines = [f"✅ /replace done."]
    for tag in tag_to_outbound:
        ob = tag_to_outbound[tag]
        meta = ob.get("_meta", {})
        lines.append(
            f"  {tag}  →  {meta.get('address')}:{meta.get('port')}  [{ob.get('protocol')}]"
        )
    if failed_slots:
        lines.append(f"\n⚠️ No candidate found for slot(s): {failed_slots}")

    await update.message.reply_text("\n".join(lines))


# ---------------------------------------------------------------------------
# /checkall
# ---------------------------------------------------------------------------

async def cmd_checkall(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _allowed(update):
        return await _reject(update)

    await update.message.reply_text("⏳ Running health check on all managed slots…")

    try:
        client = _get_panel()
        client.login()
        xray_cfg = client.get_xray_config()
    except Exception as e:
        await update.message.reply_text(f"❌ Panel error: {e}")
        return

    managed = _managed_slots(xray_cfg)
    if not managed:
        await update.message.reply_text("No managed slots found on panel.")
        return

    failed_tags: list[str] = []
    for ob in managed:
        tag = ob.get("tag", "")
        # Build a minimal _meta for health check from existing outbound
        proto = ob.get("protocol", "")
        try:
            if proto in ("vless", "vmess"):
                addr = ob["settings"]["vnext"][0]["address"]
                port = ob["settings"]["vnext"][0]["port"]
            elif proto in ("trojan", "shadowsocks"):
                addr = ob["settings"]["servers"][0]["address"]
                port = ob["settings"]["servers"][0]["port"]
            else:
                failed_tags.append(tag)
                continue
        except (KeyError, IndexError):
            failed_tags.append(tag)
            continue

        synthetic = {"_meta": {"address": addr, "port": port}}
        if not healthcheck.is_healthy(synthetic):
            failed_tags.append(tag)

    if not failed_tags:
        await update.message.reply_text("✅ All slots are healthy. Nothing to replace.")
        return

    await update.message.reply_text(
        f"⚠️ {len(failed_tags)} slot(s) failed: {failed_tags}\nFetching replacements…"
    )

    existing_addresses = _current_addresses(xray_cfg)
    candidates = scraper.collect_candidate_configs(limit=config.CANDIDATES_TO_FETCH)
    if not candidates:
        await update.message.reply_text("❌ Could not fetch candidates from source.")
        return

    cursor = [0]
    used_this_run: set[str] = set()
    tag_to_outbound: dict[str, dict] = {}
    still_failed: list[str] = []

    for tag in failed_tags:
        ob = _fetch_next_candidate(existing_addresses, used_this_run, candidates, cursor)
        if ob is None:
            still_failed.append(tag)
        else:
            tag_to_outbound[tag] = ob

    if tag_to_outbound:
        xray_cfg = merger.replace_outbounds(xray_cfg, tag_to_outbound)
        try:
            client.save_xray_config(xray_cfg)
        except Exception as e:
            await update.message.reply_text(f"❌ Failed to save config: {e}")
            return

    lines = [f"✅ /checkall done."]
    for tag, ob in tag_to_outbound.items():
        meta = ob.get("_meta", {})
        lines.append(
            f"  {tag}  →  {meta.get('address')}:{meta.get('port')}  [{ob.get('protocol')}]"
        )
    if still_failed:
        lines.append(f"\n⚠️ Could not find replacement for: {still_failed}")

    await update.message.reply_text("\n".join(lines))


# ---------------------------------------------------------------------------
# /status
# ---------------------------------------------------------------------------

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _allowed(update):
        return await _reject(update)

    try:
        client = _get_panel()
        client.login()
        xray_cfg = client.get_xray_config()
    except Exception as e:
        await update.message.reply_text(f"❌ Panel error: {e}")
        return

    managed = sorted(_managed_slots(xray_cfg), key=lambda ob: ob.get("tag", ""))
    if not managed:
        await update.message.reply_text("No managed slots found on panel.")
        return

    lines = [f"📋 {len(managed)} managed slot(s):\n"]
    for ob in managed:
        tag = ob.get("tag", "?")
        proto = ob.get("protocol", "?")
        try:
            if proto in ("vless", "vmess"):
                addr = ob["settings"]["vnext"][0]["address"]
                port = ob["settings"]["vnext"][0]["port"]
            elif proto in ("trojan", "shadowsocks"):
                addr = ob["settings"]["servers"][0]["address"]
                port = ob["settings"]["servers"][0]["port"]
            else:
                addr = port = "?"
        except (KeyError, IndexError):
            addr = port = "?"
        lines.append(f"  {tag}  {addr}:{port}  [{proto}]")

    await update.message.reply_text("\n".join(lines))


# ---------------------------------------------------------------------------
# /setup  (multi-step ConversationHandler)
# ---------------------------------------------------------------------------

SETUP_URL, SETUP_USER, SETUP_PASS, SETUP_CONFIRM = range(4)
_setup_data: dict[int, dict] = {}   # keyed by user_id


async def cmd_setup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not _allowed(update):
        await _reject(update)
        return ConversationHandler.END

    await update.message.reply_text(
        "⚙️ Panel setup wizard\n\n"
        "Step 1/3 — Enter the panel base URL (up to but NOT including /panel/xray).\n"
        "Example: https://example.com:2053/mywebpath\n\n"
        "Send /cancel to abort."
    )
    return SETUP_URL


async def setup_url(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    url = update.message.text.strip().rstrip("/")
    _setup_data[update.effective_user.id] = {"url": url}
    await update.message.reply_text("Step 2/3 — Enter the panel username:")
    return SETUP_USER


async def setup_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    _setup_data[update.effective_user.id]["username"] = update.message.text.strip()
    await update.message.reply_text("Step 3/3 — Enter the panel password:")
    return SETUP_PASS


async def setup_pass(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    _setup_data[update.effective_user.id]["password"] = update.message.text.strip()
    data = _setup_data[update.effective_user.id]
    await update.message.reply_text(
        f"🔍 Confirm new settings?\n\n"
        f"  URL:      {data['url']}\n"
        f"  Username: {data['username']}\n"
        f"  Password: {'*' * len(data['password'])}\n\n"
        "Reply yes to save, anything else to cancel."
    )
    return SETUP_CONFIRM


async def setup_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    uid = update.effective_user.id
    if update.message.text.strip().lower() != "yes":
        await update.message.reply_text("❌ Setup cancelled.")
        _setup_data.pop(uid, None)
        return ConversationHandler.END

    data = _setup_data.pop(uid, {})

    # Test the credentials before writing
    try:
        test_client = panel_client.PanelClient(
            base_url=data["url"],
            username=data["username"],
            password=data["password"],
        )
        test_client.login()
    except Exception as e:
        await update.message.reply_text(
            f"❌ Could not login with the provided credentials:\n{e}\n\nSetup aborted."
        )
        return ConversationHandler.END

    # Persist to config.py at runtime (in-memory only — survives until restart)
    # For persistence across restarts, write to config.py on disk.
    config.PANEL_BASE_URL = data["url"]
    config.PANEL_USERNAME = data["username"]
    config.PANEL_PASSWORD = data["password"]

    # Also write to config.py so they survive a service restart
    try:
        _write_config(data["url"], data["username"], data["password"])
        await update.message.reply_text("✅ Panel credentials saved and verified.")
    except Exception as e:
        await update.message.reply_text(
            f"✅ Credentials applied for this session, but could not write to config.py:\n{e}"
        )

    return ConversationHandler.END


def _write_config(url: str, username: str, password: str) -> None:
    """Overwrite PANEL_BASE_URL / PANEL_USERNAME / PANEL_PASSWORD in config.py."""
    import os
    path = os.path.join(os.path.dirname(__file__), "config.py")
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()

    def _replace_value(src: str, key: str, new_val: str) -> str:
        return re.sub(
            rf'^({re.escape(key)}\s*=\s*)["\'].*?["\']',
            rf'\g<1>"{new_val}"',
            src,
            flags=re.MULTILINE,
        )

    text = _replace_value(text, "PANEL_BASE_URL", url)
    text = _replace_value(text, "PANEL_USERNAME", username)
    text = _replace_value(text, "PANEL_PASSWORD", password)

    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    os.chmod(path, 0o600)


async def setup_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    _setup_data.pop(update.effective_user.id, None)
    await update.message.reply_text("❌ Setup cancelled.")
    return ConversationHandler.END


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    app = Application.builder().token(config.BOT_TOKEN).build()

    setup_conv = ConversationHandler(
        entry_points=[CommandHandler("setup", cmd_setup)],
        states={
            SETUP_URL:     [MessageHandler(filters.TEXT & ~filters.COMMAND, setup_url)],
            SETUP_USER:    [MessageHandler(filters.TEXT & ~filters.COMMAND, setup_user)],
            SETUP_PASS:    [MessageHandler(filters.TEXT & ~filters.COMMAND, setup_pass)],
            SETUP_CONFIRM: [MessageHandler(filters.TEXT & ~filters.COMMAND, setup_confirm)],
        },
        fallbacks=[CommandHandler("cancel", setup_cancel)],
    )

    app.add_handler(CommandHandler("fill", cmd_fill))
    app.add_handler(CommandHandler("replace", cmd_replace))
    app.add_handler(CommandHandler("checkall", cmd_checkall))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(setup_conv)

    logger.info("Bot started. Polling…")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
