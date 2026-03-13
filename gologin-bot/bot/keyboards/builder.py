from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.db.models import Token


def main_menu_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Начать смену", callback_data="shift:start")
    return builder.as_markup()


def active_token_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Освободить токен", callback_data="shift:release")
    return builder.as_markup()


def token_list_keyboard(tokens: list[Token]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for token in tokens:
        builder.button(text=token.name, callback_data=f"shift:take:{token.id}")
    builder.adjust(1)
    return builder.as_markup()
