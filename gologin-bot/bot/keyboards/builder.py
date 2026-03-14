from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.db.models import Folder, Token


def main_menu_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Начать смену", callback_data="shift:folders")
    return builder.as_markup()


def active_folder_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Освободить папку", callback_data="shift:release")
    builder.adjust(1)
    return builder.as_markup()


# Legacy — still used for token-based flow if needed
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


def folder_list_keyboard(folders: list[Folder]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for folder in folders:
        if folder.is_free:
            builder.button(text=f"✅ {folder.name}", callback_data=f"shift:folder:{folder.id}")
        else:
            builder.button(text=f"❌ {folder.name}", callback_data=f"shift:folder_info:{folder.id}")
    builder.adjust(1)
    return builder.as_markup()


def profile_count_keyboard(folder_id: int, max_count: int) -> InlineKeyboardMarkup:
    """Buttons 1..max_count to select how many M-profiles to launch."""
    builder = InlineKeyboardBuilder()
    for n in range(1, min(max_count, 15) + 1):
        builder.button(text=str(n), callback_data=f"shift:launch_folder:{folder_id}:{n}")
    builder.adjust(5)
    builder.row()
    builder.button(text="← Назад", callback_data="shift:folders")
    return builder.as_markup()


def folder_info_keyboard(folder_id: int, is_admin: bool = False) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if is_admin:
        builder.button(text="🔴 Принудительно освободить", callback_data=f"shift:force_folder:{folder_id}")
    builder.button(text="← Назад к списку", callback_data="shift:folders")
    builder.adjust(1)
    return builder.as_markup()
