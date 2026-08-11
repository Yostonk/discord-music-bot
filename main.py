import logging
import os
import shutil
import subprocess
import tempfile
import threading

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("bot")

# Intents control what data your bot receives from Discord.
intents = discord.Intents.default()
intents.message_content = True  # required for reading message text
#intents.members = True          # required for member join/leave events

bot = commands.Bot(command_prefix="!", intents=intents, help_command=commands.DefaultHelpCommand())

# ---------------------------------------------------------------------------
# yt-dlp / ffmpeg audio streaming
# ---------------------------------------------------------------------------

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def find_binary(name: str, local_relpath: str) -> str | None:
    """
    Resolve an executable, trying (in order):
      1. PATH lookup (shutil.which(name))
      2. A path relative to this script's directory (works no matter what
         the current working directory is when the bot is launched)

    NOTE: shutil.which(x) does NOT search PATH when x contains a path
    separator (e.g. "yt-dlp/yt-dlp.exe") — it only checks that exact
    relative/absolute path against the current working directory. That
    makes lookups fragile if the bot is ever started from a different cwd
    (a service, a different terminal, an IDE run config, etc.), so we do
    the PATH search and the local-folder search explicitly and separately.
    """
    local_path = os.path.join(BASE_DIR, local_relpath)
    if os.path.isfile(local_path):
        return local_path

    on_path = shutil.which(name)
    if on_path:
        return on_path

    return None


YTDLP_BIN = find_binary("yt-dlp", os.path.join("yt-dlp", "yt-dlp.exe"))
FFMPEG_BIN = find_binary("ffmpeg", os.path.join("ffmpeg", "ffmpeg.exe"))


class PipedAudioSource(discord.FFmpegPCMAudio):
    """
    Runs yt-dlp as a subprocess that writes the raw media stream to stdout,
    and feeds that stdout directly into ffmpeg's stdin (no temp files).
    """

    def __init__(self, url: str):
        if not YTDLP_BIN:
            raise RuntimeError("yt-dlp executable not found on PATH.")
        if not FFMPEG_BIN:
            raise RuntimeError("ffmpeg executable not found on PATH.")

        # 1) yt-dlp: resolve the URL/search query and stream best audio to stdout ("-")
        ytdlp_cmd = [
            YTDLP_BIN,
            "-f", "bestaudio/best",
            "--no-playlist",
            "-o", "-",           # write media to stdout
            "-q",                 # quiet
            "--no-warnings",
            "--no-config",
            url,
        ]

        # bufsize left at the default (buffered). bufsize=0 returns a raw,
        # unbuffered io.FileIO object instead of a BufferedIOBase, which is
        # both a typing mismatch and prone to choppy reads feeding ffmpeg.
        self._ytdlp_proc = subprocess.Popen(
            ytdlp_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        if self._ytdlp_proc.stdout is None:
            raise RuntimeError("Failed to open yt-dlp stdout pipe.")

        # Drain yt-dlp's stderr on a background thread so we actually see
        # extractor errors, throttling, geo-blocks, etc. instead of silently
        # getting an empty stream and "no sound, no error".
        self._stderr_lines: list[str] = []
        self._stderr_thread = threading.Thread(
            target=self._drain_stderr, daemon=True
        )
        self._stderr_thread.start()

        # 2) ffmpeg: read raw media from stdin (yt-dlp's stdout) and decode to PCM.
        # NOTE: no -reconnect flags here — those only apply when ffmpeg itself
        # opens a network URL (http/https protocol). Here ffmpeg's input is a
        # pipe (yt-dlp's stdout), and the pipe protocol doesn't support
        # -reconnect at all — passing it makes ffmpeg fail immediately with
        # "Option reconnect not found. Error opening input file -." yt-dlp is
        # the one doing the network fetch, so it's the one responsible for
        # retries/reconnects (it does this by default).
        ffmpeg_options = "-vn"  # no video

        # discord.py sends ffmpeg's stderr to devnull by default, which means
        # ffmpeg crashes/errors are completely invisible. Redirect it into a
        # temp file (the OS writes to it directly, so there's no risk of a
        # blocked pipe) so we can read it back after playback ends.
        self._ffmpeg_stderr_file = tempfile.TemporaryFile()

        super().__init__(
            source=self._ytdlp_proc.stdout,
            pipe=True,
            executable=FFMPEG_BIN,
            options=ffmpeg_options,
            stderr=self._ffmpeg_stderr_file,
        )

    def _drain_stderr(self):
        assert self._ytdlp_proc.stderr is not None
        for raw_line in self._ytdlp_proc.stderr:
            line = raw_line.decode(errors="replace").rstrip()
            if line:
                self._stderr_lines.append(line)
                logger.warning(f"[yt-dlp] {line}")

    def yt_dlp_error_output(self) -> str:
        return "\n".join(self._stderr_lines[-10:])

    def ffmpeg_error_output(self) -> str:
        try:
            self._ffmpeg_stderr_file.seek(0)
            data = self._ffmpeg_stderr_file.read()
            return data.decode(errors="replace").strip()
        except Exception:
            return ""

    def cleanup(self):
        super().cleanup()
        if self._ytdlp_proc and self._ytdlp_proc.poll() is None:
            self._ytdlp_proc.kill()
            self._ytdlp_proc.wait()
        try:
            self._ffmpeg_stderr_file.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------

@bot.event
async def on_ready():
    if bot.user is None:
        raise RuntimeError("Cannot get the bot")
    logger.info(f"Logged in as {bot.user} (ID: {bot.user.id})")
    if not YTDLP_BIN:
        logger.warning("yt-dlp not found — /play will fail until it's installed.")
    else:
        logger.info(f"Using yt-dlp: {YTDLP_BIN}")
    if not FFMPEG_BIN:
        logger.warning("ffmpeg not found — /play will fail until it's installed.")
    else:
        logger.info(f"Using ffmpeg: {FFMPEG_BIN}")
    try:
        synced = await bot.tree.sync()
        logger.info(f"Synced {len(synced)} slash command(s)")
    except discord.app_commands.CommandSyncFailure as e:
        logger.error(f"Failed to sync slash commands: {e}")


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return  # ignore unknown commands
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"Missing argument: `{error.param.name}`")
        return
    logger.exception("Unhandled command error", exc_info=error)
    await ctx.send("Something went wrong running that command.")


# ---------------------------------------------------------------------------
# Prefix (text) commands  ->  usage: !ping
# ---------------------------------------------------------------------------

@bot.command(name="ping", help="Check the bot's latency")
async def ping(ctx: commands.Context):
    await ctx.send(f"🏓 Pong! Latency: {round(bot.latency * 1000)}ms")


@bot.command(name="say", help="Make the bot repeat a message")
async def say(ctx: commands.Context, *, message: str):
    await ctx.message.delete()
    await ctx.send(message)


# ---------------------------------------------------------------------------
# Slash commands  ->  usage: /hello
# ---------------------------------------------------------------------------

@bot.tree.command(name="hello", description="Say hello to the bot")
async def hello(interaction: discord.Interaction):
    await interaction.response.send_message(f"Hello, {interaction.user.mention}!")


@bot.tree.command(name="play", description="Play audio from a URL (or search query) in your voice channel")
@app_commands.describe(url="A YouTube/SoundCloud/etc. URL, or a search term")
async def play(interaction: discord.Interaction, url: str):
    # Must be a guild member in a voice channel
    if not isinstance(interaction.user, discord.Member) or interaction.user.voice is None \
            or interaction.user.voice.channel is None:
        await interaction.response.send_message("You need to be in a voice channel first.", ephemeral=True)
        return

    if interaction.guild is None:
        await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
        return

    await interaction.response.defer()  # yt-dlp resolution can take a moment

    voice_channel = interaction.user.voice.channel
    voice_client = interaction.guild.voice_client

    try:
        if voice_client is None:
            voice_client = await voice_channel.connect()
        elif isinstance(voice_client, discord.VoiceClient) and voice_client.channel != voice_channel:
            await voice_client.move_to(voice_channel)

        assert isinstance(voice_client, discord.VoiceClient)

        # Stop anything currently playing
        if voice_client.is_playing() or voice_client.is_paused():
            voice_client.stop()

        raw_source = PipedAudioSource(url)
        source = discord.PCMVolumeTransformer(raw_source, volume=0.5)

        def after_playback(error):
            if error:
                logger.error(f"Playback error: {error}")
            # Log both processes' stderr even on a "clean" exit — a fast or
            # crashed stream can look clean to discord.py while still having
            # printed the real reason to one of these.
            ytdlp_tail = raw_source.yt_dlp_error_output()
            if ytdlp_tail:
                logger.info(f"yt-dlp stderr tail for {url}:\n{ytdlp_tail}")
            ffmpeg_tail = raw_source.ffmpeg_error_output()
            if ffmpeg_tail:
                logger.info(f"ffmpeg stderr for {url}:\n{ffmpeg_tail}")

        voice_client.play(source, after=after_playback)
        await interaction.followup.send(f"▶️ Now playing: `{url}`")

    except Exception as e:
        logger.exception("Failed to play audio")
        await interaction.followup.send(f"❌ Couldn't play that: {e}")


@bot.tree.command(name="stop", description="Stop the current audio")
async def stop(interaction: discord.Interaction):
    voice_client = interaction.guild.voice_client if interaction.guild else None
    if isinstance(voice_client, discord.VoiceClient) and (voice_client.is_playing() or voice_client.is_paused()):
        voice_client.stop()
        await interaction.response.send_message("⏹️ Stopped.")
    else:
        await interaction.response.send_message("Nothing is playing.", ephemeral=True)


@bot.tree.command(name="leave", description="Disconnect the bot from voice")
async def leave(interaction: discord.Interaction):
    voice_client = interaction.guild.voice_client if interaction.guild else None
    if voice_client:
        await voice_client.disconnect(force=True)
        await interaction.response.send_message("👋 Disconnected.")
    else:
        await interaction.response.send_message("I'm not in a voice channel.", ephemeral=True)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main():
    async with bot:
        if not TOKEN:
            raise RuntimeError("DISCORD_TOKEN not found. Add it to a .env file or environment variables.")
        await bot.start(TOKEN)


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())