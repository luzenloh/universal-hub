from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.db.models import Token


def main_menu_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Начать смену", callback_data="shift:start")
    return builder.as_markup()


def active_token_keyboard(profile_id: str | None = None) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if profile_id:
        builder.button(text="🚀 Запустить профиль", callback_data="shift:launch")
    builder.button(text="Освободить токен", callback_data="shift:release")
    builder.adjust(1)
    return builder.as_markup()


def token_info_keyboard(token_id: int, is_admin: bool = False) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if is_admin:
        builder.button(text="🔴 Принудительно освободить", callback_data=f"shift:force_release:{token_id}")
    builder.button(text="← Назад к списку", callback_data="shift:start")
    builder.adjust(1)
    return builder.as_markup()


def token_list_keyboard(tokens: list[Token]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for token in tokens:
        if token.is_free:
            builder.button(text=f"✅ {token.name}", callback_data=f"shift:take:{token.id}")
        else:
            builder.button(text=f"❌ {token.name}", callback_data=f"shift:info:{token.id}")
    builder.adjust(1)
    return builder.as_markup()
