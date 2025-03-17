from dotenv import dotenv_values

config = dotenv_values(".env")

BOT_TOKEN = config.get("BOT_TOKEN")
OPENAI_API_KEY = config.get("OPENAI_API_KEY")
ADMIN_ID = config.get("ADMIN_ID")
MONGO_USERNAME = config.get("MONGO_USERNAME")
MONGO_PASSWORD = config.get("MONGO_PASSWORD")