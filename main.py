"""
🌑 NEON NOIR — Telegram Music Bot
Style : sᴍᴀʟʟ-ᴄᴀᴘs font, box-card UI, instant play, queue, inline control panel
Env   : API_ID, API_HASH, BOT_TOKEN, STRING_SESSION
"""
import os
import time
import asyncio
import logging

from pyrogram import Client, filters, idle
from pyrogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup as Markup,
    InlineKeyboardButton as Btn,
)
from pyrogram.errors import MessageNotModified
from pytgcalls import PyTgCalls, filters as fl
from pytgcalls.types import MediaStream, AudioQuality
from yukiapi import YukiAPI

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
log = logging.getLogger("neon-noir")

API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
BOT_TOKEN = os.environ["BOT_TOKEN"]
STRING_SESSION = os.environ["STRING_SESSION"]

bot = Client("music_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
user = Client("music_user", api_id=API_ID, api_hash=API_HASH, session_string=STRING_SESSION)
call_py = PyTgCalls(user)

BOT_USERNAME = ""

# ───────────────────────── FONT + HELPERS ─────────────────────────
_SC = str.maketrans("abcdefghijklmnopqrstuvwxyz", "ᴀʙᴄᴅᴇꜰɢʜɪᴊᴋʟᴍɴᴏᴘǫʀsᴛᴜᴠᴡxʏᴢ")


def sc(text: str) -> str:
    """small-caps font"""
    return text.lower().translate(_SC)


def clean(t: str) -> str:
    for ch in "*_`[]":
        t = t.replace(ch, "")
    return t.strip()


def to_sec(d) -> int:
    try:
        if isinstance(d, (int, float)):
            return int(d)
        parts = [int(x) for x in str(d).split(":")]
        s = 0
        for p in parts:
            s = s * 60 + p
        return s
    except Exception:
        return 0


def fmt(s: int) -> str:
    s = max(0, int(s))
    h, r = divmod(s, 3600)
    m, s = divmod(r, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def bar(el: int, total: int, w: int = 14) -> str:
    if total <= 0:
        return "●" + "─" * (w - 1)
    pos = min(w - 1, int(w * el / total))
    return "━" * pos + "●" + "─" * (w - 1 - pos)


# ───────────────────────── STATE ─────────────────────────
STATE: dict[int, dict] = {}


def S(chat_id: int) -> dict:
    return STATE.setdefault(chat_id, {"cur": None, "q": []})


def elapsed(cur: dict) -> int:
    if cur["paused"]:
        return int(cur["pause_at"] - cur["start"])
    return int(time.time() - cur["start"])


# ───────────────────────── UI BUILDERS ─────────────────────────
def card(chat_id: int) -> str:
    cur = S(chat_id)["cur"]
    if not cur:
        return f"╭──────────────────╮\n   💤 {sc('nothing playing')}\n╰──────────────────╯"
    el, tot = elapsed(cur), cur["sec"]
    state = "⏸ " + sc("paused") if cur["paused"] else "▶️ " + sc("now playing")
    return (
        "╭──────────────────╮\n"
        f"   {state}\n"
        "╰──────────────────╯\n\n"
        f"🎧 **{clean(cur['title'])}**\n\n"
        f"`{bar(el, tot)}`\n"
        f"`{fmt(el)}` ⁄ `{fmt(tot)}`\n\n"
        f"👤 {sc('requested by')} **{clean(cur['by'])}**\n"
        f"📡 {sc('queue')} : `{len(S(chat_id)['q'])}`"
    )


def panel(chat_id: int) -> Markup:
    cur = S(chat_id)["cur"]
    paused = bool(cur and cur["paused"])
    return Markup([
        [
            Btn("▶️ Resume" if paused else "⏸ Pause", callback_data="nn:toggle"),
            Btn("⏭ Skip", callback_data="nn:skip"),
            Btn("⏹ Stop", callback_data="nn:stop"),
        ],
        [
            Btn("🔄 Refresh", callback_data="nn:refresh"),
            Btn("📜 Queue", callback_data="nn:queue"),
        ],
        [Btn("🔗 Source", url=cur["link"])] if cur and cur.get("link") else [],
    ][: 3 if cur and cur.get("link") else 2])


def queue_text(chat_id: int) -> str:
    st = S(chat_id)
    if not st["cur"] and not st["q"]:
        return f"📭 {sc('queue is empty')}"
    out = [f"📜 **{sc('queue')}**\n"]
    if st["cur"]:
        out.append(f"▶️ **{clean(st['cur']['title'])}**")
    for i, it in enumerate(st["q"][:10], 1):
        out.append(f"`{i:02d}` {clean(it['title'])} · `{it['duration']}`")
    if len(st["q"]) > 10:
        out.append(f"… +{len(st['q']) - 10} {sc('more')}")
    return "\n".join(out)


# ───────────────────────── CORE ─────────────────────────
async def fetch(query: str, by: str):
    async with YukiAPI() as yuki:
        results = await yuki.search(query, limit=1)
        if not results:
            return None
        t = results[0]
        url = await yuki.get_stream(t.title, type="audio")
    return {
        "title": t.title,
        "duration": t.duration,
        "sec": to_sec(t.duration),
        "link": getattr(t, "link", None),
        "url": url,
        "by": by,
    }


async def start_item(chat_id: int, item: dict):
    await call_py.play(
        chat_id,
        MediaStream(item["url"], AudioQuality.HIGH, video_flags=MediaStream.Flags.IGNORE),
    )
    now = time.time()
    item.update(start=now, paused=False, pause_at=0)
    S(chat_id)["cur"] = item


async def next_track(chat_id: int, announce: bool = True):
    st = S(chat_id)
    if st["q"]:
        nxt = st["q"].pop(0)
        try:
            await start_item(chat_id, nxt)
            if announce:
                await bot.send_message(chat_id, card(chat_id), reply_markup=panel(chat_id))
            return
        except Exception:
            log.exception("next_track failed")
    st["cur"] = None
    try:
        await call_py.leave_call(chat_id)
    except Exception:
        pass
    if announce:
        await bot.send_message(chat_id, f"🌙 {sc('queue finished — left voice chat')}")


@call_py.on_update(fl.stream_end())
async def on_stream_end(_, update):
    await next_track(update.chat_id)


async def safe_edit(msg: Message, text: str, markup=None):
    try:
        await msg.edit_text(text, reply_markup=markup, disable_web_page_preview=True)
    except MessageNotModified:
        pass


# ───────────────────────── COMMANDS ─────────────────────────
@bot.on_message(filters.command("start") & filters.private)
async def start_cmd(_, m: Message):
    kb = Markup([
        [Btn("➕ Add me to your group",
             url=f"https://t.me/{BOT_USERNAME}?startgroup=true&admin=manage_video_chats")],
        [Btn("📖 Commands", callback_data="nn:help")],
    ])
    await m.reply(
        "╭──────────────────╮\n"
        f"   🌑 **{sc('neon noir music')}**\n"
        "╰──────────────────╯\n\n"
        f"✨ {sc('crystal clear voice chat music')}\n"
        f"⚡ {sc('instant play · queue · live controls')}\n\n"
        f"👇 {sc('add me, make me admin, start a voice chat, then')} `/play`",
        reply_markup=kb,
    )


HELP = (
    f"📖 **{sc('commands')}**\n\n"
    f"`/play <song>` — {sc('play / add to queue')}\n"
    f"`/skip` — {sc('next track')}\n"
    f"`/pause` · `/resume`\n"
    f"`/queue` — {sc('show queue')}\n"
    f"`/stop` — {sc('leave voice chat')}\n"
    f"`/song <song>` — {sc('download audio (dm)')}"
)


@bot.on_message(filters.command("help"))
async def help_cmd(_, m: Message):
    await m.reply(HELP)


@bot.on_message(filters.command("play") & filters.group)
async def play_cmd(_, m: Message):
    q = " ".join(m.command[1:]).strip()
    if not q:
        return await m.reply(f"💡 {sc('usage')} : `/play <song name>`")
    s = await m.reply(f"🔎 {sc('searching')} **{clean(q)}** …")
    try:
        by = m.from_user.first_name if m.from_user else "Anonymous"
        item = await fetch(q, by)
        if not item:
            return await s.edit(f"❌ {sc('no result found')}")
        st = S(m.chat.id)
        if st["cur"]:
            st["q"].append(item)
            return await s.edit(
                f"➕ **{sc('added to queue')}**\n\n🎧 **{clean(item['title'])}**\n"
                f"⏱ `{item['duration']}` · {sc('position')} `#{len(st['q'])}`"
            )
        await start_item(m.chat.id, item)
        await s.edit(card(m.chat.id), reply_markup=panel(m.chat.id), disable_web_page_preview=True)
    except Exception as e:
        log.exception("play")
        await s.edit(f"❌ `{str(e)[:200]}`")


@bot.on_message(filters.command("skip") & filters.group)
async def skip_cmd(_, m: Message):
    if not S(m.chat.id)["cur"]:
        return await m.reply(f"💤 {sc('nothing is playing')}")
    await next_track(m.chat.id)


@bot.on_message(filters.command("pause") & filters.group)
async def pause_cmd(_, m: Message):
    cur = S(m.chat.id)["cur"]
    try:
        await call_py.pause(m.chat.id)
        if cur and not cur["paused"]:
            cur.update(paused=True, pause_at=time.time())
        await m.reply(f"⏸ {sc('paused')}")
    except Exception as e:
        await m.reply(f"❌ `{str(e)[:150]}`")


@bot.on_message(filters.command("resume") & filters.group)
async def resume_cmd(_, m: Message):
    cur = S(m.chat.id)["cur"]
    try:
        await call_py.resume(m.chat.id)
        if cur and cur["paused"]:
            cur["start"] += time.time() - cur["pause_at"]
            cur["paused"] = False
        await m.reply(f"▶️ {sc('resumed')}")
    except Exception as e:
        await m.reply(f"❌ `{str(e)[:150]}`")


@bot.on_message(filters.command("stop") & filters.group)
async def stop_cmd(_, m: Message):
    st = S(m.chat.id)
    st["q"].clear()
    st["cur"] = None
    try:
        await call_py.leave_call(m.chat.id)
        await m.reply(f"⏹ {sc('left voice chat')}")
    except Exception as e:
        await m.reply(f"❌ `{str(e)[:150]}`")


@bot.on_message(filters.command("queue") & filters.group)
async def queue_cmd(_, m: Message):
    await m.reply(queue_text(m.chat.id))


@bot.on_message(filters.command("song") & filters.private)
async def song_cmd(_, m: Message):
    q = " ".join(m.command[1:]).strip()
    if not q:
        return await m.reply(f"💡 {sc('usage')} : `/song <song name>`")
    s = await m.reply(f"⬇️ {sc('downloading')} **{clean(q)}** …")
    path = None
    try:
        os.makedirs("downloads", exist_ok=True)
        async with YukiAPI() as yuki:
            path = await yuki.download(q, type="audio", output_dir="downloads")
        await m.reply_audio(path, caption=f"🎧 **{clean(q)}**\n\n⚡ {sc('powered by yukiapi')}")
        await s.delete()
    except Exception as e:
        log.exception("song")
        await s.edit(f"❌ `{str(e)[:200]}`")
    finally:
        if path and os.path.exists(path):
            os.remove(path)


# ───────────────────────── BUTTON HANDLER ─────────────────────────
@bot.on_callback_query(filters.regex(r"^nn:"))
async def buttons(_, cq: CallbackQuery):
    action = cq.data.split(":", 1)[1]
    chat_id = cq.message.chat.id
    st = S(chat_id)
    cur = st["cur"]

    if action == "help":
        return await cq.message.reply(HELP) and await cq.answer()

    if not cur and action not in ("queue",):
        return await cq.answer("Nothing is playing 💤", show_alert=True)

    try:
        if action == "toggle":
            if cur["paused"]:
                await call_py.resume(chat_id)
                cur["start"] += time.time() - cur["pause_at"]
                cur["paused"] = False
                await cq.answer("▶️ Resumed")
            else:
                await call_py.pause(chat_id)
                cur.update(paused=True, pause_at=time.time())
                await cq.answer("⏸ Paused")
        elif action == "skip":
            await cq.answer("⏭ Skipping…")
            return await next_track(chat_id)
        elif action == "stop":
            st["q"].clear()
            st["cur"] = None
            await call_py.leave_call(chat_id)
            await cq.answer("⏹ Stopped")
            return await safe_edit(cq.message, f"🌙 {sc('stopped · left voice chat')}")
        elif action == "queue":
            return await cq.answer(
                queue_text(chat_id).replace("*", "").replace("`", "")[:190], show_alert=True
            )
        elif action == "refresh":
            await cq.answer("🔄")
        await safe_edit(cq.message, card(chat_id), panel(chat_id))
    except Exception as e:
        await cq.answer(str(e)[:150], show_alert=True)


# ───────────────────────── MAIN ─────────────────────────
async def main():
    global BOT_USERNAME
    log.info("🚀 Starting Neon Noir…")
    await bot.start()
    BOT_USERNAME = (await bot.get_me()).username
    await user.start()
    await call_py.start()
    log.info("✅ Online as @%s", BOT_USERNAME)
    await idle()
    await bot.stop()
    await user.stop()


if __name__ == "__main__":
    asyncio.run(main())
