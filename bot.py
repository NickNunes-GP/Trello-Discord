import discord
from discord.ext import commands
import asyncio
import aiohttp
import json
import os
from datetime import datetime
import hashlib
import hmac
from aiohttp import web
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Bot configuration
TOKEN = os.environ.get('DISCORD_TOKEN')
TRELLO_API_KEY = os.environ.get('TRELLO_API_KEY')
TRELLO_TOKEN = os.environ.get('TRELLO_TOKEN')
TRELLO_WEBHOOK_SECRET = os.environ.get('TRELLO_WEBHOOK_SECRET')
DISCORD_CHANNEL_ID = int(os.environ.get('DISCORD_CHANNEL_ID', 0))
WEBHOOK_URL = os.environ.get('WEBHOOK_URL')  # Your public webhook URL
PORT = int(os.environ.get('PORT', 8080))

# Bot setup
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

class TrelloWebhookHandler:
    def __init__(self, bot):
        self.bot = bot
        self.app = web.Application()
        self.app.router.add_post('/webhook', self.handle_webhook)
        self.app.router.add_get('/health', self.health_check)
        
    async def health_check(self, request):
        return web.Response(text="Bot is running!")
    
    async def handle_webhook(self, request):
        try:
            # Verify webhook signature
            if not await self.verify_webhook_signature(request):
                return web.Response(status=401, text="Unauthorized")
            
            # Parse webhook data
            data = await request.json()
            await self.process_trello_event(data)
            
            return web.Response(text="OK")
        except Exception as e:
            logger.error(f"Error handling webhook: {e}")
            return web.Response(status=500, text="Internal Server Error")
    
    async def verify_webhook_signature(self, request):
        if not TRELLO_WEBHOOK_SECRET:
            return True  # Skip verification if no secret is set
        
        # Get the signature from headers
        signature = request.headers.get('X-Trello-Webhook')
        if not signature:
            return False
        
        # Get request body
        body = await request.read()
        
        # Calculate expected signature
        expected_signature = hmac.new(
            TRELLO_WEBHOOK_SECRET.encode(),
            body,
            hashlib.sha1
        ).hexdigest()
        
        return hmac.compare_digest(signature, expected_signature)
    
    async def process_trello_event(self, data):
        try:
            action = data.get('action', {})
            action_type = action.get('type')
            
            # Process different types of Trello events
            if action_type in ['createCard', 'updateCard', 'deleteCard', 'commentCard']:
                await self.send_discord_message(action)
        except Exception as e:
            logger.error(f"Error processing Trello event: {e}")
    
    async def send_discord_message(self, action):
        try:
            channel = self.bot.get_channel(DISCORD_CHANNEL_ID)
            if not channel:
                logger.error(f"Channel {DISCORD_CHANNEL_ID} not found")
                return
            
            embed = self.create_embed(action)
            await channel.send(embed=embed)
        except Exception as e:
            logger.error(f"Error sending Discord message: {e}")
    
    def create_embed(self, action):
        action_type = action.get('type')
        member = action.get('memberCreator', {})
        date = action.get('date', '')
        
        # Create embed based on action type
        if action_type == 'createCard':
            card = action.get('data', {}).get('card', {})
            list_info = action.get('data', {}).get('list', {})
            
            embed = discord.Embed(
                title="🆕 New Card Created",
                description=f"**{card.get('name', 'Unknown')}**",
                color=0x00ff00,
                timestamp=datetime.fromisoformat(date.replace('Z', '+00:00'))
            )
            embed.add_field(name="List", value=list_info.get('name', 'Unknown'), inline=True)
            embed.add_field(name="Creator", value=member.get('fullName', 'Unknown'), inline=True)
            
        elif action_type == 'updateCard':
            card = action.get('data', {}).get('card', {})
            old_data = action.get('data', {}).get('old', {})
            
            embed = discord.Embed(
                title="📝 Card Updated",
                description=f"**{card.get('name', 'Unknown')}**",
                color=0xffaa00,
                timestamp=datetime.fromisoformat(date.replace('Z', '+00:00'))
            )
            
            # Check what was updated
            if 'name' in old_data:
                embed.add_field(name="Name Changed", value=f"From: {old_data['name']}\nTo: {card.get('name')}", inline=False)
            if 'desc' in old_data:
                embed.add_field(name="Description Updated", value="Description was modified", inline=False)
            if 'pos' in old_data:
                embed.add_field(name="Position Changed", value="Card was moved", inline=False)
            
            embed.add_field(name="Updated by", value=member.get('fullName', 'Unknown'), inline=True)
            
        elif action_type == 'deleteCard':
            card = action.get('data', {}).get('card', {})
            
            embed = discord.Embed(
                title="🗑️ Card Deleted",
                description=f"**{card.get('name', 'Unknown')}**",
                color=0xff0000,
                timestamp=datetime.fromisoformat(date.replace('Z', '+00:00'))
            )
            embed.add_field(name="Deleted by", value=member.get('fullName', 'Unknown'), inline=True)
            
        elif action_type == 'commentCard':
            card = action.get('data', {}).get('card', {})
            text = action.get('data', {}).get('text', '')
            
            embed = discord.Embed(
                title="💬 New Comment",
                description=f"**{card.get('name', 'Unknown')}**",
                color=0x0099ff,
                timestamp=datetime.fromisoformat(date.replace('Z', '+00:00'))
            )
            embed.add_field(name="Comment", value=text[:1000] + "..." if len(text) > 1000 else text, inline=False)
            embed.add_field(name="Comment by", value=member.get('fullName', 'Unknown'), inline=True)
        
        else:
            embed = discord.Embed(
                title="🔄 Trello Update",
                description=f"Action: {action_type}",
                color=0x666666,
                timestamp=datetime.fromisoformat(date.replace('Z', '+00:00'))
            )
            embed.add_field(name="User", value=member.get('fullName', 'Unknown'), inline=True)
        
        return embed

# Initialize webhook handler
webhook_handler = TrelloWebhookHandler(bot)

@bot.event
async def on_ready():
    logger.info(f'{bot.user} has connected to Discord!')
    logger.info(f'Bot is in {len(bot.guilds)} guilds')

@bot.command(name='setup_webhook')
@commands.has_permissions(administrator=True)
async def setup_webhook(ctx, board_id: str):
    """Setup Trello webhook for a specific board"""
    try:
        async with aiohttp.ClientSession() as session:
            webhook_data = {
                'key': TRELLO_API_KEY,
                'token': TRELLO_TOKEN,
                'callbackURL': f"{WEBHOOK_URL}/webhook",
                'idModel': board_id,
                'description': 'Discord Bot Webhook'
            }
            
            async with session.post('https://api.trello.com/1/webhooks', data=webhook_data) as response:
                if response.status == 200:
                    result = await response.json()
                    await ctx.send(f"✅ Webhook created successfully! ID: {result['id']}")
                else:
                    await ctx.send(f"❌ Failed to create webhook. Status: {response.status}")
    except Exception as e:
        await ctx.send(f"❌ Error: {str(e)}")

@bot.command(name='list_webhooks')
@commands.has_permissions(administrator=True)
async def list_webhooks(ctx):
    """List all active webhooks"""
    try:
        async with aiohttp.ClientSession() as session:
            url = f"https://api.trello.com/1/tokens/{TRELLO_TOKEN}/webhooks"
            params = {'key': TRELLO_API_KEY}
            
            async with session.get(url, params=params) as response:
                if response.status == 200:
                    webhooks = await response.json()
                    if webhooks:
                        webhook_list = "\n".join([f"ID: {w['id']}, Board: {w['idModel']}" for w in webhooks])
                        await ctx.send(f"📋 Active webhooks:\n```{webhook_list}```")
                    else:
                        await ctx.send("📋 No webhooks found.")
                else:
                    await ctx.send(f"❌ Failed to fetch webhooks. Status: {response.status}")
    except Exception as e:
        await ctx.send(f"❌ Error: {str(e)}")

@bot.command(name='delete_webhook')
@commands.has_permissions(administrator=True)
async def delete_webhook(ctx, webhook_id: str):
    """Delete a specific webhook"""
    try:
        async with aiohttp.ClientSession() as session:
            url = f"https://api.trello.com/1/webhooks/{webhook_id}"
            params = {'key': TRELLO_API_KEY, 'token': TRELLO_TOKEN}
            
            async with session.delete(url, params=params) as response:
                if response.status == 200:
                    await ctx.send(f"✅ Webhook {webhook_id} deleted successfully!")
                else:
                    await ctx.send(f"❌ Failed to delete webhook. Status: {response.status}")
    except Exception as e:
        await ctx.send(f"❌ Error: {str(e)}")

@bot.command(name='test_channel')
async def test_channel(ctx):
    """Test if the bot can send messages to the configured channel"""
    try:
        channel = bot.get_channel(DISCORD_CHANNEL_ID)
        if channel:
            await channel.send("🧪 Test message from Trello bot!")
            await ctx.send(f"✅ Test message sent to {channel.name}")
        else:
            await ctx.send(f"❌ Channel with ID {DISCORD_CHANNEL_ID} not found")
    except Exception as e:
        await ctx.send(f"❌ Error: {str(e)}")

async def start_webhook_server():
    """Start the webhook server"""
    runner = web.AppRunner(webhook_handler.app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()
    logger.info(f"Webhook server started on port {PORT}")

async def main():
    """Main function to run both bot and webhook server"""
    # Start webhook server
    await start_webhook_server()
    
    # Start Discord bot
    await bot.start(TOKEN)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}")