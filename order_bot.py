# -*- coding: utf-8 -*-
"""
Бот Beauty Supply Moscow (версия под бесплатный хостинг Render.com):
1) Принимает заказы из мини-приложения (POST /api/order) и шлёт их вам в личку.
2) Пересылает вам сообщения от клиентов; ваш "Ответить" на пересланное
   сообщение уходит обратно клиенту.

Работает через вебхук (Telegram сам стучится на ваш сервер), а не через
постоянное подключение — это подходит под бесплатный тариф Render.

Настройки задаются через переменные окружения (Environment Variables
в панели Render), а не прямо в коде — так секреты не попадут в GitHub.
"""

import os
import re
import hashlib
import hmac
from urllib.parse import parse_qsl

from aiohttp import web
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

# ==================== НАСТРОЙКИ (из переменных окружения) ====================
BOT_TOKEN = os.environ["BOT_TOKEN"]                        # токен от @BotFather
OWNER_CHAT_ID = int(os.environ.get("OWNER_CHAT_ID", "0"))  # ваш telegram id
BASE_WEBHOOK_URL = os.environ.get("BASE_WEBHOOK_URL", "")  # https://ваш-сервис.onrender.com
PORT = int(os.environ.get("PORT", 8080))
WEBHOOK_PATH = "/webhook"
# ===============================================================================

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


def validate_init_data(init_data: str) -> bool:
    """Проверяет, что заказ пришёл именно из вашего Telegram Mini App."""
    if not init_data:
        return False
    try:
        parsed = dict(parse_qsl(init_data))
        received_hash = parsed.pop("hash", "")
        data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
        secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        calculated_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
        return calculated_hash == received_hash
    except Exception:
        return False


# ==================== TELEGRAM-ЧАСТЬ ====================

@dp.message(Command("start"))
async def start_handler(message: types.Message):
    print("Ваш telegram id:", message.from_user.id)  # смотрите в логах Render один раз
    if message.from_user.id == OWNER_CHAT_ID:
        await message.answer("Бот запущен. Сюда будут приходить заказы и сообщения клиентов.")
    else:
        await message.answer("Здравствуйте! Напишите ваш вопрос — ответим в ближайшее время 🌸")


CLIENT_ID_PATTERN = re.compile(r"— id (\d+)")


@dp.message()
async def relay(message: types.Message):
    if message.from_user.id == OWNER_CHAT_ID:
        # Владелец отвечает клиенту через "Ответить" на пересланное сообщение.
        # id клиента достаём прямо из текста пересланного сообщения (не из памяти,
        # чтобы это переживало перезапуск бесплатного сервиса на Render).
        if message.reply_to_message and message.reply_to_message.text:
            m = CLIENT_ID_PATTERN.search(message.reply_to_message.text)
            if m:
                client_id = int(m.group(1))
                try:
                    await bot.send_message(client_id, message.text or "")
                    await message.answer("✅ Отправлено клиенту")
                except Exception:
                    await message.answer("⚠️ Не удалось отправить — возможно, клиент заблокировал бота")
            else:
                await message.answer("Не нашёл id клиента в этом сообщении — отвечайте именно на пересланное сообщение клиента (там, где написано «— id ...»).")
        else:
            await message.answer("Чтобы ответить клиенту — сделайте «Ответить» (Reply) прямо на его пересланное сообщение.")
        return

    sender = message.from_user
    header = f"✉️ {sender.full_name}"
    if sender.username:
        header += f" (@{sender.username})"
    header += f" — id {sender.id}\n\n"

    await bot.send_message(OWNER_CHAT_ID, header + (message.text or "[не текстовое сообщение]"))
    await message.answer("Спасибо! Мы получили ваше сообщение и скоро ответим 🌸")


# ==================== HTTP API ДЛЯ MINI APP ====================

@web.middleware
async def cors_middleware(request, handler):
    if request.method == "OPTIONS":
        resp = web.Response()
    else:
        resp = await handler(request)
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    resp.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
    return resp


async def handle_order(request: web.Request):
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "bad json"}, status=400)

    items = data.get("items", [])
    note = (data.get("note") or "").strip()
    user = data.get("user", {})
    init_data = data.get("initData", "")

    if not items:
        return web.json_response({"ok": False, "error": "empty cart"}, status=400)

    if not validate_init_data(init_data):
        return web.json_response({"ok": False, "error": "invalid init data"}, status=403)

    total = sum(i["price"] * i["qty"] for i in items)

    text = "🛍 Новый заказ из мини-приложения\n"
    text += f"От: {user.get('first_name', '')}"
    if user.get("username"):
        text += f" (@{user['username']})"
    text += f" — id {user.get('id')}\n\n"
    for i in items:
        text += f"• {i['name']} — {i['qty']} шт × {i['price']}₽ = {i['qty'] * i['price']}₽\n"
    text += f"\nИтого: {total}₽"
    if note:
        text += f"\n\nКомментарий: {note}"

    await bot.send_message(OWNER_CHAT_ID, text)
    return web.json_response({"ok": True})


async def healthz(request: web.Request):
    """Используется UptimeRobot, чтобы сервис не 'засыпал' на бесплатном тарифе."""
    return web.Response(text="ok")


# ==================== ЗАПУСК ====================

async def on_startup(bot: Bot):
    if BASE_WEBHOOK_URL:
        await bot.set_webhook(f"{BASE_WEBHOOK_URL}{WEBHOOK_PATH}")


def main():
    dp.startup.register(on_startup)

    app = web.Application(middlewares=[cors_middleware])
    app.router.add_post("/api/order", handle_order)
    app.router.add_route("OPTIONS", "/api/order", handle_order)
    app.router.add_get("/healthz", healthz)

    SimpleRequestHandler(dispatcher=dp, bot=bot).register(app, path=WEBHOOK_PATH)
    setup_application(app, dp, bot=bot)

    web.run_app(app, host="0.0.0.0", port=PORT)


if __name__ == "__main__":
    main()
