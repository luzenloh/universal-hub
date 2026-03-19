from __future__ import annotations

"""Interactive weekly shift schedule handler.

Callback data format:
  sched:view                           — refresh main calendar view
  sched:day:{YYYY-MM-DD}               — open day config
  sched:shift:{YYYY-MM-DD}:{shift}     — set shift type (day/night/off)
  sched:dir:{YYYY-MM-DD}:{shift}:{dir} — set direction, go back to main
  sched:template                       — copy last week's schedule into draft
  sched:team                           — show team schedule
  sched:submit                         — save draft to DB
"""

import json
import logging
from datetime import date, timedelta

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from hub.db.repository import ScheduleRepository

logger = logging.getLogger(__name__)
router = Router()

# ── In-memory drafts ─────────────────────────────────────────────────────────
# user_id → {date_iso: {"shift": "day"|"night"|"off", "direction": str|None}}
_drafts: dict[int, dict[str, dict[str, str | None]]] = {}

# ── Constants ─────────────────────────────────────────────────────────────────
SHIFT_EMOJI = {"day": "☀️", "night": "🌙", "off": "—"}
SHIFT_LABEL = {"day": "День (9–21)", "night": "Ночь (21–9)", "off": "Выходной"}
DIRECTIONS = {"pay_out": "PAY_OUT", "pay_in": "PAY_IN", "matching": "MATCHING"}
DIR_SHORT = {"pay_out": "PO", "pay_in": "PI", "matching": "MA"}

RU_WEEKDAYS_SHORT = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
RU_WEEKDAYS_FULL = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
RU_MONTHS = ["", "янв", "фев", "мар", "апр", "май", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]
RU_MONTHS_GEN = [
    "", "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]


# ── Date helpers ──────────────────────────────────────────────────────────────

def _next_monday() -> date:
    """Always returns the Monday of NEXT week."""
    today = date.today()
    this_monday = today - timedelta(days=today.weekday())
    return this_monday + timedelta(weeks=1)


def _week_dates(week_start: date) -> list[date]:
    return [week_start + timedelta(days=i) for i in range(7)]


def _display_name(user: Message | CallbackQuery) -> str:
    from_user = user.from_user  # type: ignore[union-attr]
    if from_user.first_name:
        return from_user.first_name
    if from_user.username:
        return f"@{from_user.username}"
    return str(from_user.id)


# ── Draft management ──────────────────────────────────────────────────────────

def _blank_draft(week_start: date) -> dict[str, dict[str, str | None]]:
    return {d.isoformat(): {"shift": "off", "direction": None} for d in _week_dates(week_start)}


async def _ensure_draft(user_id: int, session: AsyncSession) -> dict[str, dict[str, str | None]]:
    """Load draft from memory; fall back to saved schedule or blank."""
    if user_id in _drafts:
        return _drafts[user_id]
    week_start = _next_monday()
    repo = ScheduleRepository(session)
    existing = await repo.get(user_id, week_start)
    if existing:
        _drafts[user_id] = json.loads(existing.days)
    else:
        _drafts[user_id] = _blank_draft(week_start)
    return _drafts[user_id]


# ── View builders ─────────────────────────────────────────────────────────────

def _build_main_view(user_id: int, week_start: date) -> tuple[str, InlineKeyboardMarkup]:
    draft = _drafts.get(user_id, _blank_draft(week_start))
    dates = _week_dates(week_start)

    start_label = f"{dates[0].day} {RU_MONTHS[dates[0].month]}"
    end_label = f"{dates[-1].day} {RU_MONTHS[dates[-1].month]}"
    text = f"📅 <b>График смен: {start_label}–{end_label} {dates[0].year}</b>\n"
    text += "─" * 32 + "\n"

    for d in dates:
        ds = d.isoformat()
        day_data = draft.get(ds, {"shift": "off", "direction": None})
        shift = day_data.get("shift") or "off"
        direction = day_data.get("direction")
        wd = RU_WEEKDAYS_SHORT[d.weekday()]

        if shift == "off":
            text += f"{wd} {d.day:02d}  —\n"
        else:
            dir_str = DIRECTIONS.get(direction or "", "не выбрано")
            text += f"{wd} {d.day:02d}  {SHIFT_EMOJI[shift]}  {dir_str}\n"

    text += "─" * 32 + "\n"
    text += "Нажми на день для изменения:"

    builder = InlineKeyboardBuilder()
    for d in dates:
        ds = d.isoformat()
        day_data = draft.get(ds, {"shift": "off", "direction": None})
        shift = day_data.get("shift") or "off"
        wd = RU_WEEKDAYS_SHORT[d.weekday()]
        builder.button(
            text=f"{SHIFT_EMOJI[shift]} {wd} {d.day}",
            callback_data=f"sched:day:{ds}",
        )
    builder.button(text="📋 Как прошлую неделю", callback_data="sched:template")
    builder.button(text="👥 Команда", callback_data="sched:team")
    builder.button(text="✅ Сохранить", callback_data="sched:submit")
    # 7 day buttons (4+3) + 2 utility buttons + 1 submit
    builder.adjust(4, 3, 2, 1)
    return text, builder.as_markup()


def _build_day_view(d: date, day_data: dict[str, str | None]) -> tuple[str, InlineKeyboardMarkup]:
    current_shift = day_data.get("shift") or "off"
    current_dir = day_data.get("direction")

    wd_full = RU_WEEKDAYS_FULL[d.weekday()]
    text = f"📅 <b>{wd_full}, {d.day} {RU_MONTHS_GEN[d.month]}</b>\n\n"

    if current_shift == "off":
        text += "Текущее: —\n\n"
    else:
        dir_str = DIRECTIONS.get(current_dir or "", "не выбрано")
        text += f"Текущее: {SHIFT_EMOJI[current_shift]} {SHIFT_LABEL[current_shift]} — {dir_str}\n\n"

    text += "Выбери тип смены:"

    ds = d.isoformat()
    builder = InlineKeyboardBuilder()

    # Row 1: shift type buttons
    for shift_key in ("day", "night", "off"):
        label = f"{SHIFT_EMOJI[shift_key]} {SHIFT_LABEL[shift_key]}"
        if shift_key == current_shift:
            label = "✓ " + label
        builder.button(text=label, callback_data=f"sched:shift:{ds}:{shift_key}")

    if current_shift != "off":
        text += "\n\nНаправление:"
        # Row 2: direction buttons
        for dir_key, dir_name in DIRECTIONS.items():
            label = dir_name
            if dir_key == current_dir:
                label = "✓ " + label
            builder.button(text=label, callback_data=f"sched:dir:{ds}:{current_shift}:{dir_key}")
        # Row 3: back — add BEFORE adjust so adjust covers all 7 buttons
        builder.button(text="◀ Назад", callback_data="sched:view")
        builder.adjust(3, 3, 1)
    else:
        # Row 2: back — add BEFORE adjust
        builder.button(text="◀ Назад", callback_data="sched:view")
        builder.adjust(3, 1)

    return text, builder.as_markup()


# ── Safe edit helper ──────────────────────────────────────────────────────────

async def _edit(callback: CallbackQuery, text: str, kb: InlineKeyboardMarkup) -> None:
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)  # type: ignore[union-attr]
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise


# ── Command handler ───────────────────────────────────────────────────────────

@router.message(Command("schedule"))
async def cmd_schedule(message: Message, session: AsyncSession) -> None:
    try:
        await message.delete()
    except Exception:
        pass

    user_id = message.from_user.id  # type: ignore[union-attr]
    week_start = _next_monday()
    await _ensure_draft(user_id, session)
    text, kb = _build_main_view(user_id, week_start)
    await message.answer(text, parse_mode="HTML", reply_markup=kb)


# ── Callback handlers ─────────────────────────────────────────────────────────

@router.callback_query(F.data == "sched:view")
async def sched_view(callback: CallbackQuery, session: AsyncSession) -> None:
    user_id = callback.from_user.id
    week_start = _next_monday()
    await _ensure_draft(user_id, session)
    text, kb = _build_main_view(user_id, week_start)
    await _edit(callback, text, kb)
    await callback.answer()


@router.callback_query(F.data.startswith("sched:day:"))
async def sched_day(callback: CallbackQuery, session: AsyncSession) -> None:
    user_id = callback.from_user.id
    date_str = callback.data.split(":", 2)[2]  # "YYYY-MM-DD"
    d = date.fromisoformat(date_str)
    draft = await _ensure_draft(user_id, session)
    day_data = draft.get(date_str, {"shift": "off", "direction": None})
    text, kb = _build_day_view(d, day_data)
    await _edit(callback, text, kb)
    await callback.answer()


@router.callback_query(F.data.startswith("sched:shift:"))
async def sched_shift(callback: CallbackQuery, session: AsyncSession) -> None:
    user_id = callback.from_user.id
    _, _, date_str, shift = callback.data.split(":")
    d = date.fromisoformat(date_str)
    draft = await _ensure_draft(user_id, session)

    if shift == "off":
        draft[date_str] = {"shift": "off", "direction": None}
        week_start = _next_monday()
        text, kb = _build_main_view(user_id, week_start)
    else:
        # Preserve existing direction if already set
        current_dir = draft.get(date_str, {}).get("direction")
        draft[date_str] = {"shift": shift, "direction": current_dir}
        text, kb = _build_day_view(d, draft[date_str])

    await _edit(callback, text, kb)
    await callback.answer()


@router.callback_query(F.data.startswith("sched:dir:"))
async def sched_dir(callback: CallbackQuery, session: AsyncSession) -> None:
    user_id = callback.from_user.id
    parts = callback.data.split(":")  # ["sched", "dir", "YYYY-MM-DD", "shift", "direction"]
    date_str, shift, direction = parts[2], parts[3], parts[4]
    draft = await _ensure_draft(user_id, session)
    draft[date_str] = {"shift": shift, "direction": direction}
    week_start = _next_monday()
    text, kb = _build_main_view(user_id, week_start)
    await _edit(callback, text, kb)
    await callback.answer()


@router.callback_query(F.data == "sched:template")
async def sched_template(callback: CallbackQuery, session: AsyncSession) -> None:
    user_id = callback.from_user.id
    week_start = _next_monday()
    last_week_start = week_start - timedelta(weeks=1)

    repo = ScheduleRepository(session)
    last_week = await repo.get(user_id, last_week_start)

    if last_week is None:
        await callback.answer("Расписание за прошлую неделю не найдено", show_alert=True)
        return

    last_days: dict = json.loads(last_week.days)
    curr_dates = _week_dates(week_start)
    last_dates = _week_dates(last_week_start)

    new_draft: dict[str, dict[str, str | None]] = {}
    for curr_d, last_d in zip(curr_dates, last_dates):
        last_day = last_days.get(last_d.isoformat(), {"shift": "off", "direction": None})
        new_draft[curr_d.isoformat()] = {"shift": last_day.get("shift") or "off", "direction": last_day.get("direction")}

    _drafts[user_id] = new_draft
    text, kb = _build_main_view(user_id, week_start)
    await _edit(callback, text, kb)
    await callback.answer("Скопировано с прошлой недели")


@router.callback_query(F.data == "sched:team")
async def sched_team(callback: CallbackQuery, session: AsyncSession) -> None:
    week_start = _next_monday()
    dates = _week_dates(week_start)
    repo = ScheduleRepository(session)
    schedules = await repo.get_team(week_start)

    start_label = f"{dates[0].day} {RU_MONTHS[dates[0].month]}"
    end_label = f"{dates[-1].day} {RU_MONTHS[dates[-1].month]}"
    text = f"👥 <b>Команда: {start_label}–{end_label} {dates[0].year}</b>\n"
    text += "─" * 32 + "\n"

    if not schedules:
        text += "\n<i>Никто ещё не сдал расписание на эту неделю</i>"
    else:
        for s in schedules:
            days_data: dict = json.loads(s.days)
            name = s.display_name or "User"
            text += f"\n<b>{name}</b>\n"
            for d in dates:
                day_d = days_data.get(d.isoformat(), {"shift": "off"})
                shift = day_d.get("shift") or "off"
                wd = RU_WEEKDAYS_SHORT[d.weekday()]
                if shift == "off":
                    text += f"  {wd} {d.day:02d}  —\n"
                else:
                    dir_str = DIRECTIONS.get(day_d.get("direction") or "", "?")
                    text += f"  {wd} {d.day:02d}  {SHIFT_EMOJI[shift]}  {dir_str}\n"

    builder = InlineKeyboardBuilder()
    builder.button(text="◀ Назад", callback_data="sched:view")
    await _edit(callback, text, builder.as_markup())
    await callback.answer()


@router.callback_query(F.data == "sched:submit")
async def sched_submit(callback: CallbackQuery, session: AsyncSession) -> None:
    user_id = callback.from_user.id
    week_start = _next_monday()
    draft = await _ensure_draft(user_id, session)

    from_user = callback.from_user
    if from_user.first_name:
        display_name = from_user.first_name
    elif from_user.username:
        display_name = f"@{from_user.username}"
    else:
        display_name = str(user_id)

    repo = ScheduleRepository(session)
    await repo.upsert(
        telegram_id=user_id,
        display_name=display_name,
        week_start=week_start,
        days=draft,
    )

    # Remove draft from memory after saving
    _drafts.pop(user_id, None)
    # Reload from DB so the view reflects saved state
    await _ensure_draft(user_id, session)

    text, kb = _build_main_view(user_id, week_start)
    await _edit(callback, text, kb)
    await callback.answer("✅ Расписание сохранено!")

    # Reminder: if tomorrow is the first day of the new week and there's a shift
    today = date.today()
    tomorrow = today + timedelta(days=1)
    tomorrow_str = tomorrow.isoformat()
    if today.weekday() == 6 and tomorrow_str in draft and draft[tomorrow_str].get("shift") != "off":
        shift_name = draft[tomorrow_str].get("shift", "")
        try:
            await callback.bot.send_message(  # type: ignore[union-attr]
                user_id,
                f"⏰ Завтра начинается твоя смена ({shift_name}). Не забудь запустить агента!",
            )
        except Exception:
            pass
