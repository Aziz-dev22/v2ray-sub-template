import asyncio
import logging

import uvicorn

import config
import database as db
import bot as bot_module
import webhook_server

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
logger = logging.getLogger("main")


async def run_bot(application):
    await application.initialize()
    await application.start()
    await application.updater.start_polling()
    logger.info("Telegram bot polling started")


async def run_webhook_server():
    if not config.WEBHOOK_PUBLIC_URL:
        logger.info(
            "WEBHOOK_PUBLIC_URL خالیه؛ سرور webhook روی پورت %s بالا میاد ولی هنوز "
            "نزد irMarket ثبت نشده (از پنل ادمین می‌تونی ثبتش کنی).",
            config.WEBHOOK_PORT,
        )
    uv_config = uvicorn.Config(
        webhook_server.app,
        host=config.WEBHOOK_HOST,
        port=config.WEBHOOK_PORT,
        log_level="info",
    )
    server = uvicorn.Server(uv_config)
    await server.serve()


async def main():
    problems = config.validate()
    for p in problems:
        logger.warning(p)

    await db.init_db()

    application = bot_module.build_application()
    webhook_server.set_bot(application.bot)

    await run_bot(application)
    try:
        await run_webhook_server()
    finally:
        await application.updater.stop()
        await application.stop()
        await application.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
