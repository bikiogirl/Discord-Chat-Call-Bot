# ==========================================
#   Discord Music Bot
#   Created & Maintained by Bikio
#   GitHub: https://github.com/bikiogirl
# ==========================================
"""
Discord Leveling Bot  (discord.py 2.x)
- Chat levels (messages) + Voice levels (time in call)
- /top_chat, /top_voice, /top (overall), /profile, /setchannel
Install:  pip install -U discord.py
Run:      DISCORD_TOKEN=your_token python level_bot.py
"""
import os
import sqlite3
import time

import discord
from discord import app_commands
from discord.ext import commands, tasks

# ───────────────────────── CONFIG ─────────────────────────
TOKEN = ""
DB_FILE = "levels.db"

# Difficulty: total needed for level L = BASE * L ** EXPONENT
# Higher EXPONENT = harder levels. Higher BASE = harder from the start.
EXPONENT = 1.8
CHAT_BASE = 10     # messages     (L1 = 10, L10 = ~630, L50 = ~11,500)
VOICE_BASE = 5     # minutes      (L1 = 5,  L10 = ~315, L50 = ~5,700)
MESSAGE_COOLDOWN = 3  # seconds between counted messages (anti-spam)

COLOR = 0x5865F2
GOLD = 0xF1C40F
MEDALS = ["🥇", "🥈", "🥉"]

# ───────────────────────── DATABASE ─────────────────────────
db = sqlite3.connect(DB_FILE)
db.row_factory = sqlite3.Row
db.executescript("""
CREATE TABLE IF NOT EXISTS users (
    guild_id INTEGER, user_id INTEGER,
    messages INTEGER DEFAULT 0,
    voice_seconds INTEGER DEFAULT 0,
    chat_level INTEGER DEFAULT 0,
    voice_level INTEGER DEFAULT 0,
    PRIMARY KEY (guild_id, user_id)
);
CREATE TABLE IF NOT EXISTS settings (
    guild_id INTEGER PRIMARY KEY,
    levelup_channel INTEGER
);
""")
db.commit()


def get_user(guild_id: int, user_id: int) -> sqlite3.Row:
    db.execute("INSERT OR IGNORE INTO users (guild_id, user_id) VALUES (?, ?)", (guild_id, user_id))
    return db.execute("SELECT * FROM users WHERE guild_id=? AND user_id=?", (guild_id, user_id)).fetchone()


# ───────────────────────── LEVEL MATH ─────────────────────────
def needed(level: int, base: float) -> int:
    """Total amount required to REACH `level`."""
    return int(base * level ** EXPONENT)


def level_from(total: int, base: float) -> int:
    lvl = int((total / base) ** (1 / EXPONENT)) if total > 0 else 0
    while needed(lvl + 1, base) <= total:
        lvl += 1
    while lvl > 0 and needed(lvl, base) > total:
        lvl -= 1
    return lvl


def progress_bar(total: int, base: float, size: int = 12) -> str:
    lvl = level_from(total, base)
    cur, nxt = needed(lvl, base), needed(lvl + 1, base)
    pct = (total - cur) / max(nxt - cur, 1)
    filled = int(pct * size)
    return f"{'🟦' * filled}{'⬛' * (size - filled)} {int(pct * 100)}%"


def fmt_time(seconds: int) -> str:
    h, rem = divmod(seconds, 3600)
    m = rem // 60
    return f"{h}h {m}m"


def overall_points(row) -> int:
    return row["messages"] + row["voice_seconds"] // 60


# ───────────────────────── BOT ─────────────────────────
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.voice_states = True


class LevelBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)
        self.cooldowns: dict[tuple[int, int], float] = {}

    async def setup_hook(self):
        await self.tree.sync()
        self.voice_tick.start()

    async def announce(self, guild: discord.Guild, member: discord.abc.User, level: int,
                       kind: str, fallback: discord.abc.Messageable | None = None):
        row = db.execute("SELECT levelup_channel FROM settings WHERE guild_id=?", (guild.id,)).fetchone()
        channel = guild.get_channel(row["levelup_channel"]) if row and row["levelup_channel"] else fallback
        if channel is None:
            return
        embed = discord.Embed(
            description=f"✨ Well done {member.mention}, you reached Level {level}! ✨",
            color=GOLD,
        )
        embed.set_author(name=f"{kind} Level Up", icon_url=member.display_avatar.url)
        embed.set_thumbnail(url=member.display_avatar.url)
        try:
            await channel.send(content=member.mention, embed=embed)
        except discord.HTTPException:
            pass

    # ── voice time tracking: +60s every minute for everyone in a call ──
    @tasks.loop(seconds=60)
    async def voice_tick(self):
        for guild in self.guilds:
            for vc in guild.voice_channels:
                if vc == guild.afk_channel:
                    continue
                humans = [m for m in vc.members if not m.bot]
                for m in humans:
                    if m.voice and (m.voice.self_deaf or m.voice.deaf):
                        continue
                    db.execute(
                        "INSERT OR IGNORE INTO users (guild_id, user_id) VALUES (?, ?)", (guild.id, m.id))
                    db.execute(
                        "UPDATE users SET voice_seconds = voice_seconds + 60 WHERE guild_id=? AND user_id=?",
                        (guild.id, m.id))
                    row = get_user(guild.id, m.id)
                    new_lvl = level_from(row["voice_seconds"] // 60, VOICE_BASE)
                    if new_lvl > row["voice_level"]:
                        db.execute("UPDATE users SET voice_level=? WHERE guild_id=? AND user_id=?",
                                   (new_lvl, guild.id, m.id))
                        await self.announce(guild, m, new_lvl, "Voice")
        db.commit()

    @voice_tick.before_loop
    async def before_tick(self):
        await self.wait_until_ready()


bot = LevelBot()


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.guild:
        return
    key = (message.guild.id, message.author.id)
    now = time.time()
    if now - bot.cooldowns.get(key, 0) < MESSAGE_COOLDOWN:
        return
    bot.cooldowns[key] = now

    get_user(*key)
    db.execute("UPDATE users SET messages = messages + 1 WHERE guild_id=? AND user_id=?", key)
    row = get_user(*key)
    new_lvl = level_from(row["messages"], CHAT_BASE)
    if new_lvl > row["chat_level"]:
        db.execute("UPDATE users SET chat_level=? WHERE guild_id=? AND user_id=?", (new_lvl, *key))
        db.commit()
        await bot.announce(message.guild, message.author, new_lvl, "Chat", fallback=message.channel)
    db.commit()


# ───────────────────────── LEADERBOARD ─────────────────────────
def build_board(guild: discord.Guild, title: str, color: int, rows, line_fn) -> discord.Embed:
    embed = discord.Embed(title=title, color=color)
    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)
    if not rows:
        embed.description = "No data yet. Start chatting!"
        return embed
    lines = []
    for i, r in enumerate(rows):
        rank = MEDALS[i] if i < 3 else f"`#{i + 1}`"
        lines.append(f"{rank} <@{r['user_id']}>\n┗ {line_fn(r)}")
    embed.description = "\n\n".join(lines)
    embed.set_footer(text=f"{guild.name} • Top 10")
    return embed


@bot.tree.command(name="top_chat", description="Top 10 chat levels")
async def top_chat(interaction: discord.Interaction):
    rows = db.execute(
        "SELECT * FROM users WHERE guild_id=? AND messages>0 ORDER BY messages DESC LIMIT 10",
        (interaction.guild_id,)).fetchall()
    embed = build_board(
        interaction.guild, "💬 Chat Leaderboard", COLOR, rows,
        lambda r: f"**Level {level_from(r['messages'], CHAT_BASE)}** • {r['messages']:,} messages")
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="top_voice", description="Top 10 voice levels")
async def top_voice(interaction: discord.Interaction):
    rows = db.execute(
        "SELECT * FROM users WHERE guild_id=? AND voice_seconds>0 ORDER BY voice_seconds DESC LIMIT 10",
        (interaction.guild_id,)).fetchall()
    embed = build_board(
        interaction.guild, "🎙️ Voice Leaderboard", 0x2ECC71, rows,
        lambda r: f"**Level {level_from(r['voice_seconds'] // 60, VOICE_BASE)}** • {fmt_time(r['voice_seconds'])} in call")
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="top", description="Top 10 overall (chat + voice combined)")
async def top(interaction: discord.Interaction):
    rows = db.execute(
        "SELECT * FROM users WHERE guild_id=? ORDER BY (messages + voice_seconds/60) DESC LIMIT 10",
        (interaction.guild_id,)).fetchall()
    rows = [r for r in rows if overall_points(r) > 0]
    embed = build_board(
        interaction.guild, "🏆 Overall Leaderboard", GOLD, rows,
        lambda r: (f"**Level {level_from(overall_points(r), CHAT_BASE)}** • "
                   f"💬 {r['messages']:,} msgs • 🎙️ {fmt_time(r['voice_seconds'])}"))
    await interaction.response.send_message(embed=embed)


# ───────────────────────── PROFILE ─────────────────────────
@bot.tree.command(name="profile", description="Show a member's levels and stats")
@app_commands.describe(member="Member to look up (default: you)")
async def profile(interaction: discord.Interaction, member: discord.Member | None = None):
    member = member or interaction.user
    row = get_user(interaction.guild_id, member.id)
    db.commit()

    chat_lvl = level_from(row["messages"], CHAT_BASE)
    voice_min = row["voice_seconds"] // 60
    voice_lvl = level_from(voice_min, VOICE_BASE)
    points = overall_points(row)
    overall_lvl = level_from(points, CHAT_BASE)

    rank = db.execute(
        "SELECT COUNT(*) + 1 AS r FROM users WHERE guild_id=? AND (messages + voice_seconds/60) > ?",
        (interaction.guild_id, points)).fetchone()["r"]

    embed = discord.Embed(title=f"{member.display_name}'s Profile", color=member.color or discord.Color(COLOR))
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(
        name="💬 Chat",
        value=(f"**Level {chat_lvl}**\n{row['messages']:,} messages\n"
               f"{progress_bar(row['messages'], CHAT_BASE)}\n"
               f"Next: {needed(chat_lvl + 1, CHAT_BASE):,} msgs"),
        inline=True)
    embed.add_field(
        name="🎙️ Voice",
        value=(f"**Level {voice_lvl}**\n{fmt_time(row['voice_seconds'])} in call\n"
               f"{progress_bar(voice_min, VOICE_BASE)}\n"
               f"Next: {fmt_time(needed(voice_lvl + 1, VOICE_BASE) * 60)}"),
        inline=True)
    embed.add_field(
        name="🏆 Overall",
        value=f"**Level {overall_lvl}** • Rank **#{rank}**",
        inline=False)
    embed.set_footer(text=interaction.guild.name)
    await interaction.response.send_message(embed=embed)


# ───────────────────────── ADMIN ─────────────────────────
@bot.tree.command(name="setchannel", description="Set the channel for level-up notifications")
@app_commands.describe(channel="Channel where level-up messages will be sent")
@app_commands.checks.has_permissions(manage_guild=True)
async def setchannel(interaction: discord.Interaction, channel: discord.TextChannel):
    db.execute(
        "INSERT INTO settings (guild_id, levelup_channel) VALUES (?, ?) "
        "ON CONFLICT(guild_id) DO UPDATE SET levelup_channel=excluded.levelup_channel",
        (interaction.guild_id, channel.id))
    db.commit()
    await interaction.response.send_message(f"✅ Level-up messages will be sent in {channel.mention}", ephemeral=True)


@setchannel.error
async def setchannel_error(interaction: discord.Interaction, error):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("❌ You need **Manage Server** permission.", ephemeral=True)


bot.run('')