from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.db.models import Folder, Token


def main_menu_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Начать смену", callback_data="shift:folders")
    return builder.as_markup()


def active_folder_keyboard(folder_id: int | None = None, count: int | None = None) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if folder_id is not None and count is not None:
        label = f"Открыть M1…M{count}"
        builder.button(text=f"🖥 {label}", callback_data=f"shift:launch_rest:{folder_id}:{count}")
    builder.button(text="Завершить смену", callback_data="shift:release")
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


def count_picker_keyboard(folder_id: int, n: int, max_n: int) -> InlineKeyboardMarkup:
    """<< < N > >> style picker + separate launch button."""
    builder = InlineKeyboardBuilder()

    builder.button(text="«", callback_data=f"shift:count:{folder_id}:1")
    builder.button(text="‹", callback_data=f"shift:count:{folder_id}:{max(1, n - 1)}")
    builder.button(text=str(n), callback_data="shift:noop")  # display only
    builder.button(text="›", callback_data=f"shift:count:{folder_id}:{min(max_n, n + 1)}")
    builder.button(text="»", callback_data=f"shift:count:{folder_id}:{max_n}")
    builder.button(text="🚀 Запустить", callback_data=f"shift:launch_folder:{folder_id}:{n}")
    builder.button(text="← Назад", callback_data="shift:folders")
    builder.adjust(5, 1, 1)
    return builder.as_markup()


def folder_info_keyboard(folder_id: int, is_admin: bool = False) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if is_admin:
        builder.button(text="🔴 Принудительно освободить", callback_data=f"shift:force_folder:{folder_id}")
    builder.button(text="← Назад", callback_data="shift:folders")
    builder.adjust(1)
    return builder.as_markup()


# ── Legacy (token-based, kept for compatibility) ───────────────────────────────

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
    builder.button(text="← Назад", callback_data="shift:start")
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
