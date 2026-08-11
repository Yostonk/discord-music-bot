import logging
import os

import discord
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
# Events
# ---------------------------------------------------------------------------

@bot.event
async def on_ready():
    if bot.user is None:
        raise RuntimeError("Cannot get the bot")
    logger.info(f"Logged in as {bot.user} (ID: {bot.user.id})")
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