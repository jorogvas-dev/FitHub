"""Exact manual nutrition arithmetic and user-local diary dates (no prescriptions)."""
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from functools import lru_cache
import re
from zoneinfo import ZoneInfo, available_timezones

NUTRIENTS = (('kcal', 'Calories', 'kcal'), ('protein', 'Protein', 'g'),
             ('carbs', 'Carbohydrate', 'g'), ('fat', 'Fat', 'g'))
MEALS = (('breakfast', 'Breakfast'), ('lunch', 'Lunch'), ('dinner', 'Dinner'), ('snack', 'Snacks'))
DEFAULT_TIMEZONE = 'Europe/Sofia'
MIN_DATE = date(1900, 1, 1)
MAX_DATE = date(2100, 12, 31)
HUNDRED = Decimal('100')
CENT = Decimal('0.01')


@lru_cache(maxsize=1)
def supported_timezones():
    return tuple(sorted(available_timezones()))


def utc_now():
    return datetime.now(timezone.utc)


def user_today(zone, now=None):
    instant = utc_now() if now is None else now
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ValueError('An aware instant is required for a user-local date.')
    return instant.astimezone(ZoneInfo(zone)).date()


def diary_date(value):
    try:
        if not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', value):
            raise ValueError
        result = date.fromisoformat(value)
        if not MIN_DATE <= result <= MAX_DATE:
            raise ValueError
        return result
    except (ValueError, TypeError):
        raise ValueError('Choose a valid diary date between 1900-01-01 and 2100-12-31 (YYYY-MM-DD).') from None


def amount(value):
    if value is None:
        return 'Unknown'
    rounded = value.quantize(CENT, rounding=ROUND_HALF_UP)
    if value != 0 and rounded == 0:
        return '<0.01' if value > 0 else '-<0.01'
    return format(rounded, '.2f')


def entry_totals(entry):
    return {key: None if entry[key+'_100g'] is None else entry[key+'_100g'] * entry['grams'] / HUNDRED
            for key, _, _ in NUTRIENTS}


def daily_totals(entries, settings):
    # Sum exact rate * grams / 100; formatting is the only rounding step.
    values = [entry_totals(entry) for entry in entries]
    result = {}
    for key, label, unit in NUTRIENTS:
        known = [value[key] for value in values if value[key] is not None]
        missing = len(entries) - len(known)
        subtotal = sum(known, Decimal('0'))
        target = settings.get('target_'+key)
        complete = missing == 0
        remaining = target - subtotal if target is not None and complete else None
        result[key] = dict(label=label, unit=unit, target=target, subtotal=subtotal,
                           total=subtotal if complete else None, complete=complete,
                           known_count=len(known), missing=missing, remaining=remaining,
                           over=subtotal-target if target is not None and subtotal > target else None)
    return result


def target_comparison(settings):
    if any(settings.get('target_'+key) is None for key, _, _ in NUTRIENTS):
        return None
    estimated = settings['target_protein']*4 + settings['target_carbs']*4 + settings['target_fat']*9
    difference = settings['target_kcal'] - estimated
    return dict(estimated=estimated, difference=difference, magnitude=abs(difference),
                direction='higher' if difference > 0 else 'lower')
