"""
tg-xui-manager  —  Telegram bot to manage 3x-ui panel outbounds.

Commands
--------
/start /help   Show available commands.
/fill N        Fill slots out01..out0N with healthy, non-duplicate candidates.
/replace 1,5   Replace specific slots directly.
/checkall      Check all managed slots; replace the failed ones.
/status        List all managed slots (address / port / protocol).
/setup         Interactive wizard to change panel URL, username, and password.

Candidate evaluation pipeline:
  1. Parse all candidate links (converter.py)
  2. If XRAY_CHECK_ENABLED: test XRAY_WORKERS candidates in parallel via Xray binary
     (ipcheck.py), collect exit IPs, deduplicate.
  3. If XRAY_CHECK_ENABLED is False: fall back to serial TCP latency check.
"""

import logging
import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from telegram import BotCommand, Update
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
import ipcheck
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

HELP_TEXT = (
    "\U0001f916 *tg\\-xui\\-manager* \u2014 3x\\-ui outbound manager\n"
    "\n"
    "*Commands:*\n"
    "\n"
    "/status\n"
    "\u2514 List all managed slots\n"
    "\n"
    "/checkall\n"
    "\u2514 Check all slots and replace failed ones\n"
    "\n"
    "/fill \u2014 Fill slots with healthy configs\n"
    "\u2514 Example: `/fill 10`\n"
    "\n"
    "/replace \u2014 Force\\-replace specific slot numbers\n"
    "\u2514 Example: `/replace 3,7` or `/replace 1 5 8`\n"
    "\n"
    "/setup \u2014 Change panel credentials\n"
    "\n"
    "/help \u2014 Show this message\n"
)

BOT_COMMANDS = [
    BotCommand("start",    "Show help and available commands"),
    BotCommand("help",     "Show help and available commands"),
    BotCommand("status",   "List all managed slots"),
    BotCommand("checkall", "Check all slots and replace failed ones"),
    BotCommand("fill",     "Fill N slots with healthy configs \u2014 e.g. /fill 10"),
    BotCommand("replace",  "Replace specific slots \u2014 e.g. /replace 1,5,8"),
    BotCommand("setup",    "Change panel credentials"),
]

# ---------------------------------------------------------------------------
# Auth guard
# ---------------------------------------------------------------------------

def _allowed(update: Update) -> bool:
    return update.effective_user.id in config.ALLOWED_USERS


async def _reject(update: Update) -> None:
    logger.warning("Unauthorised access attempt from user_id=%s", update.effective_user.id)


# ---------------------------------------------------------------------------
# /start  /help
# ---------------------------------------------------------------------------

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _allowed(update):
        return await _reject(update)
    await update.message.reply_text(HELP_TEXT, parse_mode="MarkdownV2")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _allowed(update):
        return await _reject(update)
    await update.message.reply_text(HELP_TEXT, parse_mode="MarkdownV2")


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _slot_tag(n: int) -> str:
    return merger.slot_tag(n)


def _managed_slots(xray_cfg: dict) -> list[dict]:
    pattern = re.compile(
        rf"^{re.escape(config.SLOT_TAG_PREFIX)}\d{{{config.SLOT_TAG_DIGITS}}}$"
    )
    return [ob for ob in xray_cfg.get("outbounds", []) if pattern.match(ob.get("tag", ""))]


def _current_ips(xray_cfg: dict) -> set[str]:
    """Collect addresses of existing managed slots for dedup."""
    addrs: set[str] = set()
    for ob in _managed_slots(xray_cfg):
        proto = ob.get("protocol", "")
        try:
            if proto == "vless":
                addrs.add(ob["settings"]["address"])
            elif proto == "vmess":
                addrs.add(ob["settings"]["vnext"][0]["address"])
            elif proto in ("trojan", "shadowsocks"):
                addrs.add(ob["settings"]["servers"][0]["address"])
        except (KeyError, IndexError):
            pass
    return addrs


def _address_of(outbound: dict) -> str | None:
    return outbound.get("_meta", {}).get("address")


# ---------------------------------------------------------------------------
# Parallel candidate fetching (Xray mode)
# ---------------------------------------------------------------------------

def _check_one(ob: dict, existing_keys: set[str]) -> tuple[dict, str] | None:
    """
    Worker: run ipcheck on a single outbound.
    Returns (outbound, exit_ip) if healthy and not in existing_keys, else None.
    existing_keys is read-only — used_this_run dedup is done in the caller.
    """
    exit_ip = ipcheck.check_outbound(ob)
    if exit_ip is None:
        return None
    if exit_ip in existing_keys:
        logger.info("Skipping duplicate exit IP (existing slot): %s", exit_ip)
        return None
    return (ob, exit_ip)


def _collect_candidates_parallel(
    candidates: list[str],
    needed: int,
    existing_keys: set[str],
) -> list[tuple[dict, str]]:
    """
    Parse and test candidates in parallel.
    Returns up to `needed` (outbound, exit_ip) tuples, deduplicated by exit IP.
    """
    workers = getattr(config, "XRAY_WORKERS", 5)
    results: list[tuple[dict, str]] = []
    seen_ips: set[str] = set()
    lock = threading.Lock()

    parsed: list[dict] = []
    for link in candidates:
        ob = converter.parse_link(link)
        if ob is not None:
            parsed.append(ob)

    if not parsed:
        return []

    batch_size = workers * 3
    idx = 0

    with ThreadPoolExecutor(max_workers=workers) as executor:
        while idx < len(parsed) and len(results) < needed:
            batch = parsed[idx: idx + batch_size]
            idx += batch_size

            futures = {executor.submit(_check_one, ob, existing_keys): ob for ob in batch}

            for future in as_completed(futures):
                if len(results) >= needed:
                    for f in futures:
                        f.cancel()
                    break
                result = future.result()
                if result is None:
                    continue
                ob, exit_ip = result
                with lock:
                    if exit_ip in seen_ips:
                        logger.info("Skipping duplicate exit IP (this run): %s", exit_ip)
                        continue
                    seen_ips.add(exit_ip)
                    results.append((ob, exit_ip))

    return results[:needed]


# ---------------------------------------------------------------------------
# Serial candidate fetching (TCP fallback mode)
# ---------------------------------------------------------------------------

def _collect_candidates_serial(
    candidates: list[str],
    needed: int,
    existing_keys: set[str],
) -> list[dict]:
    """Serial TCP-based candidate collection (fallback when XRAY_CHECK_ENABLED=False)."""
    results: list[dict] = []
    used: set[str] = set()

    for link in candidates:
        if len(results) >= needed:
            break
        ob = converter.parse_link(link)
        if ob is None:
            continue
        addr = _address_of(ob)
        if addr and (addr in existing_keys or addr in used):
            logger.info("Skipping duplicate address: %s", addr)
            continue
        if not healthcheck.is_healthy(ob):
            continue
        if addr:
            used.add(addr)
        results.append(ob)

    return results


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

    use_xray = getattr(config, "XRAY_CHECK_ENABLED", True)
    workers = getattr(config, "XRAY_WORKERS", 5)
    mode_note = f"real connectivity check, {workers} parallel workers" if use_xray else "TCP latency check"
    await update.message.reply_text(f"\u23f3 Filling {n} slot(s) \u2014 {mode_note}\u2026")

    try:
        client = _get_panel()
        client.login()
        xray_cfg = client.get_xray_config()
    except Exception as e:
        await update.message.reply_text(f"\u274c Panel error: {e}")
        return

    existing_keys = _current_ips(xray_cfg)
    candidates = scraper.collect_candidate_configs(limit=config.CANDIDATES_TO_FETCH)
    if not candidates:
        await update.message.reply_text("\u274c Could not fetch candidates from source.")
        return

    if use_xray:
        found = _collect_candidates_parallel(candidates, needed=n, existing_keys=existing_keys)
        tag_to_outbound = {_slot_tag(i + 1): ob for i, (ob, _) in enumerate(found)}
        failed_slots = list(range(len(found) + 1, n + 1)) if len(found) < n else []
    else:
        found_obs = _collect_candidates_serial(candidates, needed=n, existing_keys=existing_keys)
        tag_to_outbound = {_slot_tag(i + 1): ob for i, ob in enumerate(found_obs)}
        failed_slots = list(range(len(found_obs) + 1, n + 1)) if len(found_obs) < n else []

    if tag_to_outbound:
        xray_cfg = merger.replace_outbounds(xray_cfg, tag_to_outbound)
        try:
            client.save_xray_config(xray_cfg)
        except Exception as e:
            await update.message.reply_text(f"\u274c Failed to save config: {e}")
            return

    lines = [f"\u2705 /fill {n} done."]
    for tag, ob in tag_to_outbound.items():
        meta = ob.get("_meta", {})
        lines.append(f"  {tag}  \u2192  {meta.get('address')}:{meta.get('port')}  [{ob.get('protocol')}]")
    if failed_slots:
        lines.append(f"\n\u26a0\ufe0f No healthy candidate found for slot(s): {failed_slots}")

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
        await update.message.reply_text("Usage: /replace <slot numbers>\nExample: /replace 1,5,8")
        return

    n = len(slot_numbers)
    use_xray = getattr(config, "XRAY_CHECK_ENABLED", True)
    workers = getattr(config, "XRAY_WORKERS", 5)
    mode_note = f"real connectivity check, {workers} parallel workers" if use_xray else "TCP latency check"
    await update.message.reply_text(f"\u23f3 Replacing slot(s) {slot_numbers} \u2014 {mode_note}\u2026")

    try:
        client = _get_panel()
        client.login()
        xray_cfg = client.get_xray_config()
    except Exception as e:
        await update.message.reply_text(f"\u274c Panel error: {e}")
        return

    existing_keys = _current_ips(xray_cfg)
    candidates = scraper.collect_candidate_configs(limit=config.CANDIDATES_TO_FETCH)
    if not candidates:
        await update.message.reply_text("\u274c Could not fetch candidates from source.")
        return

    if use_xray:
        found = _collect_candidates_parallel(candidates, needed=n, existing_keys=existing_keys)
        tag_to_outbound = {_slot_tag(slot_numbers[i]): ob for i, (ob, _) in enumerate(found)}
        failed_slots = [slot_numbers[i] for i in range(len(found), n)]
    else:
        found_obs = _collect_candidates_serial(candidates, needed=n, existing_keys=existing_keys)
        tag_to_outbound = {_slot_tag(slot_numbers[i]): ob for i, ob in enumerate(found_obs)}
        failed_slots = [slot_numbers[i] for i in range(len(found_obs), n)]

    if tag_to_outbound:
        xray_cfg = merger.replace_outbounds(xray_cfg, tag_to_outbound)
        try:
            client.save_xray_config(xray_cfg)
        except Exception as e:
            await update.message.reply_text(f"\u274c Failed to save config: {e}")
            return

    lines = ["\u2705 /replace done."]
    for tag, ob in tag_to_outbound.items():
        meta = ob.get("_meta", {})
        lines.append(f"  {tag}  \u2192  {meta.get('address')}:{meta.get('port')}  [{ob.get('protocol')}]")
    if failed_slots:
        lines.append(f"\n\u26a0\ufe0f No candidate found for slot(s): {failed_slots}")

    await update.message.reply_text("\n".join(lines))


# ---------------------------------------------------------------------------
# /checkall
# ---------------------------------------------------------------------------

async def cmd_checkall(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _allowed(update):
        return await _reject(update)

    use_xray = getattr(config, "XRAY_CHECK_ENABLED", True)
    workers = getattr(config, "XRAY_WORKERS", 5)
    mode_note = f"real connectivity check, {workers} parallel workers" if use_xray else "TCP latency check"
    await update.message.reply_text(f"\u23f3 Checking all managed slots ({mode_note})\u2026")

    try:
        client = _get_panel()
        client.login()
        xray_cfg = client.get_xray_config()
    except Exception as e:
        await update.message.reply_text(f"\u274c Panel error: {e}")
        return

    managed = _managed_slots(xray_cfg)
    if not managed:
        await update.message.reply_text("No managed slots found on panel.")
        return

    failed_tags: list[str] = []
    # IPs of healthy slots (used to avoid picking same IP as replacement)
    healthy_ips: set[str] = set()
    # IPs of failed slots (also excluded from replacements to avoid re-picking same server)
    failed_ips: set[str] = set()

    if use_xray:
        slot_obs: list[tuple[str, dict]] = []
        for ob in managed:
            tag = ob.get("tag", "")
            proto = ob.get("protocol", "")
            try:
                if proto == "vless":
                    addr, port = ob["settings"]["address"], ob["settings"]["port"]
                elif proto == "vmess":
                    addr, port = ob["settings"]["vnext"][0]["address"], ob["settings"]["vnext"][0]["port"]
                elif proto in ("trojan", "shadowsocks"):
                    addr, port = ob["settings"]["servers"][0]["address"], ob["settings"]["servers"][0]["port"]
                else:
                    failed_tags.append(tag)
                    continue
            except (KeyError, IndexError):
                failed_tags.append(tag)
                continue
            check_ob = dict(ob)
            check_ob["_meta"] = {"address": addr, "port": port}
            slot_obs.append((tag, check_ob))

        with ThreadPoolExecutor(max_workers=workers) as executor:
            future_to_tag = {executor.submit(ipcheck.check_outbound, ob): tag for tag, ob in slot_obs}
            for future in as_completed(future_to_tag):
                tag = future_to_tag[future]
                exit_ip = future.result()
                if exit_ip is None:
                    failed_tags.append(tag)
                else:
                    healthy_ips.add(exit_ip)

        # Get exit IPs of failed slots so replacements don't pick the same servers
        failed_slot_obs = [ob for tag, ob in slot_obs if tag in failed_tags]
        if failed_slot_obs:
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = [executor.submit(ipcheck.check_outbound, ob) for ob in failed_slot_obs]
                for future in as_completed(futures):
                    ip = future.result()
                    if ip:
                        failed_ips.add(ip)
    else:
        for ob in managed:
            tag = ob.get("tag", "")
            proto = ob.get("protocol", "")
            try:
                if proto == "vless":
                    addr, port = ob["settings"]["address"], ob["settings"]["port"]
                elif proto == "vmess":
                    addr, port = ob["settings"]["vnext"][0]["address"], ob["settings"]["vnext"][0]["port"]
                elif proto in ("trojan", "shadowsocks"):
                    addr, port = ob["settings"]["servers"][0]["address"], ob["settings"]["servers"][0]["port"]
                else:
                    failed_tags.append(tag)
                    continue
            except (KeyError, IndexError):
                failed_tags.append(tag)
                continue
            if not healthcheck.is_healthy({"_meta": {"address": addr, "port": port}}):
                failed_tags.append(tag)

    if not failed_tags:
        await update.message.reply_text("\u2705 All slots are healthy. Nothing to replace.")
        return

    await update.message.reply_text(
        f"\u26a0\ufe0f {len(failed_tags)} slot(s) failed: {failed_tags}\nFetching replacements\u2026"
    )

    # existing_keys = healthy slot IPs + failed slot IPs (avoid re-picking same servers)
    existing_keys = healthy_ips | failed_ips
    candidates = scraper.collect_candidate_configs(limit=config.CANDIDATES_TO_FETCH)
    if not candidates:
        await update.message.reply_text("\u274c Could not fetch candidates from source.")
        return

    n = len(failed_tags)
    if use_xray:
        found = _collect_candidates_parallel(candidates, needed=n, existing_keys=existing_keys)
        tag_to_outbound = {failed_tags[i]: ob for i, (ob, _) in enumerate(found)}
        still_failed = [failed_tags[i] for i in range(len(found), n)]
    else:
        found_obs = _collect_candidates_serial(candidates, needed=n, existing_keys=existing_keys)
        tag_to_outbound = {failed_tags[i]: ob for i, ob in enumerate(found_obs)}
        still_failed = [failed_tags[i] for i in range(len(found_obs), n)]

    if tag_to_outbound:
        xray_cfg = merger.replace_outbounds(xray_cfg, tag_to_outbound)
        try:
            client.save_xray_config(xray_cfg)
        except Exception as e:
            await update.message.reply_text(f"\u274c Failed to save config: {e}")
            return

    lines = ["\u2705 /checkall done."]
    for tag, ob in tag_to_outbound.items():
        meta = ob.get("_meta", {})
        lines.append(f"  {tag}  \u2192  {meta.get('address')}:{meta.get('port')}  [{ob.get('protocol')}]")
    if still_failed:
        lines.append(f"\n\u26a0\ufe0f Could not find replacement for: {still_failed}")

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
        await update.message.reply_text(f"\u274c Panel error: {e}")
        return

    managed = sorted(_managed_slots(xray_cfg), key=lambda ob: ob.get("tag", ""))
    if not managed:
        await update.message.reply_text("No managed slots found on panel.")
        return

    lines = [f"\U0001f4cb {len(managed)} managed slot(s):\n"]
    for ob in managed:
        tag = ob.get("tag", "?")
        proto = ob.get("protocol", "?")
        try:
            if proto == "vless":
                addr = ob["settings"]["address"]
                port = ob["settings"]["port"]
            elif proto == "vmess":
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
_setup_data: dict[int, dict] = {}


async def cmd_setup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not _allowed(update):
        await _reject(update)
        return ConversationHandler.END

    await update.message.reply_text(
        "\u2699\ufe0f Panel setup wizard\n\n"
        "Step 1/3 \u2014 Enter the panel base URL.\n\n"
        "\u2705 Correct format:\n"
        "https://example.com:2053/mywebpath\n\n"
        "\u274c Do NOT include /panel or /panel/xray at the end.\n\n"
        "Send /cancel to abort."
    )
    return SETUP_URL


async def setup_url(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    url = update.message.text.strip().rstrip("/")
    if "/panel" in url:
        await update.message.reply_text(
            "\u26a0\ufe0f The URL should not include /panel or /panel/xray.\n"
            "Please re-enter the URL stopping before /panel:"
        )
        return SETUP_URL
    _setup_data[update.effective_user.id] = {"url": url}
    await update.message.reply_text("Step 2/3 \u2014 Enter the panel username:")
    return SETUP_USER


async def setup_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    _setup_data[update.effective_user.id]["username"] = update.message.text.strip()
    await update.message.reply_text("Step 3/3 \u2014 Enter the panel password:")
    return SETUP_PASS


async def setup_pass(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    _setup_data[update.effective_user.id]["password"] = update.message.text.strip()
    data = _setup_data[update.effective_user.id]
    await update.message.reply_text(
        f"\U0001f50d Confirm new settings?\n\n"
        f"  URL:      {data['url']}\n"
        f"  Username: {data['username']}\n"
        f"  Password: {'*' * len(data['password'])}\n\n"
        "Reply yes to save, anything else to cancel."
    )
    return SETUP_CONFIRM


async def setup_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    uid = update.effective_user.id
    if update.message.text.strip().lower() != "yes":
        await update.message.reply_text("\u274c Setup cancelled.")
        _setup_data.pop(uid, None)
        return ConversationHandler.END

    data = _setup_data.pop(uid, {})

    await update.message.reply_text("\u23f3 Testing credentials\u2026")
    try:
        test_client = panel_client.PanelClient(
            base_url=data["url"],
            username=data["username"],
            password=data["password"],
        )
        test_client.login()
    except Exception as e:
        await update.message.reply_text(
            f"\u274c Could not login with the provided credentials:\n{e}\n\nSetup aborted."
        )
        return ConversationHandler.END

    config.PANEL_BASE_URL = data["url"]
    config.PANEL_USERNAME = data["username"]
    config.PANEL_PASSWORD = data["password"]

    try:
        _write_config(data["url"], data["username"], data["password"])
        await update.message.reply_text("\u2705 Panel credentials saved and verified.")
    except Exception as e:
        await update.message.reply_text(
            f"\u2705 Credentials applied for this session, but could not write to config.py:\n{e}"
        )

    return ConversationHandler.END


def _write_config(url: str, username: str, password: str) -> None:
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
    await update.message.reply_text("\u274c Setup cancelled.")
    return ConversationHandler.END


# ---------------------------------------------------------------------------
# Post-init: register command menu with Telegram
# ---------------------------------------------------------------------------

async def post_init(app: Application) -> None:
    await app.bot.set_my_commands(BOT_COMMANDS)
    logger.info("Bot command menu registered with Telegram.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    app = (
        Application.builder()
        .token(config.BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

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

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("fill", cmd_fill))
    app.add_handler(CommandHandler("replace", cmd_replace))
    app.add_handler(CommandHandler("checkall", cmd_checkall))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(setup_conv)

    logger.info("Bot started. Polling\u2026")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
