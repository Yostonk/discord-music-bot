import os
import asyncio

import discord
from discord.ext import commands
from discord import app_commands
from dotenv import load_dotenv
import yt_dlp

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="m?", intents=intents)


async def search_yt(query, ydl_opts):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: _extract(query, ydl_opts))

def _extract(query, ydl_opts):
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        return ydl.extract_info(query, download=False)

@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"{bot.user} is ready!")

@bot.tree.command(name="ping", description="Check the bot's latency")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message(f"{bot.latency * 1000:.0f} ms")

@bot.tree.command(name="play", description="Play a song or Add it to the queue")
@app_commands.describe(song="The name or URL of the song to play")
async def play(interaction: discord.Interaction, song: str):

    # Check if the command is used in a guild
    if not isinstance(interaction.user, discord.Member) or interaction.guild is None:
        await interaction.response.send_message("Use this command in a server.", ephemeral=True)
        return
    
    # Check if the user is in a voice channel
    if interaction.user.voice is None or interaction.user.voice.channel is None:
        await interaction.response.send_message("Join a voice channel first.", ephemeral=True)
        return

    await interaction.response.defer()

    if "youtu.be" in song:
        song = song.replace("youtu.be/", "youtube.com/watch?v=").split("?si")[0]

    voice_channel = interaction.user.voice.channel
    voice_client = voice_channel.guild.voice_client

    if voice_client is None:
        voice_client = await voice_channel.connect()
    elif voice_client.channel != voice_channel and isinstance(voice_client, discord.VoiceClient):
        await voice_client.move_to(voice_channel)

    ydl_options = {
        "format": "bestaudio[abr<=192k]/best",
        "extractor_args": {
            "youtube": {"skip": ["dash", "hls"]}}
    }
    query = "ytsearch1: " + song
    results = await search_yt(query, ydl_options)
    tracks = results.get("entries", [])

    if not tracks:
        await interaction.followup.send("No results found.")
        return
    
    first_track = tracks[0]
    audio_url = first_track["url"]
    title = first_track.get("title", "Untitled")

    source = discord.FFmpegOpusAudio(
        audio_url,
        executable="/usr/bin/ffmpeg",
        before_options="-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
        options="-vn",
    )

    await interaction.followup.send(f"Playing: {title}", ephemeral=False)
    voice_client.play(source) #type: ignore



if TOKEN is None:
    print("No Token is provided.")
    exit(1)

if __name__ == "__main__":
    bot.run(TOKEN)