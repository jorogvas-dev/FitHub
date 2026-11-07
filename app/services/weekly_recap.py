"""Bulk, read-only weekly summaries of the signed-in user's existing records."""
from collections import defaultdict
from datetime import datetime, time, timedelta, timezone
from itertools import groupby
from zoneinfo import ZoneInfo

from app.routes.plans import query
from . import personal_records
from .workout_progress import stable_id


def week_bounds(today, zone, selection='this'):
    """Local Monday-to-Monday bounds; convert each midnight separately for DST."""
    start = today - timedelta(days=today.weekday() + (7 if selection == 'last' else 0))
    end = start + timedelta(days=7)
    local_zone = ZoneInfo(zone)
    return dict(start=start, end=end, last_day=end-timedelta(days=1),
                start_utc=datetime.combine(start, time.min, local_zone).astimezone(timezone.utc),
                end_utc=datetime.combine(end, time.min, local_zone).astimezone(timezone.utc),
                selection=selection, timezone=zone)


def weekly_recap(conn, user_id, today, zone, selection='this'):
    recap = week_bounds(today, zone, selection)
    # A LEFT JOIN keeps completed workouts with no logged sets in the count.
    logs = query(conn, '''
        SELECT s.session_id,s.finished_at,l.set_id,l.exercise_id,l.set_number,
               l.weight_kg,l.reps,e.tracking_type
        FROM workout_session s LEFT JOIN set_log l USING(session_id)
        LEFT JOIN exercise e USING(exercise_id)
        WHERE s.user_id=%s AND s.finished_at>=%s AND s.finished_at<%s
        ORDER BY s.finished_at,s.session_id,l.set_number,l.set_id
    ''', (user_id, recap['start_utc'], recap['end_utc']))
    identifiers = sorted({row['exercise_id'] for row in logs
                          if row['tracking_type'] == 'reps' and stable_id(row) is not None})
    earlier = query(conn, '''
        SELECT l.exercise_id,l.weight_kg,max(l.reps) AS reps
        FROM set_log l JOIN workout_session s USING(session_id)
        JOIN exercise e USING(exercise_id)
        WHERE s.user_id=%s AND s.finished_at<%s AND e.tracking_type='reps'
          AND l.exercise_id=ANY(%s)
        GROUP BY l.exercise_id,l.weight_kg
    ''', (user_id, recap['start_utc'], identifiers)) if identifiers else []
    previous = defaultdict(dict)
    for row in earlier:
        previous[row['exercise_id']][row['weight_kg']] = row['reps']
    completed = new_records = 0
    for _, entries in groupby(logs, key=lambda row: row['session_id']):
        completed += 1
        supported = [row for row in entries
                     if row['tracking_type'] == 'reps' and stable_id(row) is not None]
        badges = personal_records.badges_from_sets(supported, previous)
        # Count record categories, including multiple reasons on the same set.
        new_records += sum(len(reasons) for reasons in badges.values())
        # Update only after awarding the whole session; never compare within it.
        for row in supported:
            baseline = previous[row['exercise_id']]
            load = row['weight_kg']
            baseline[load] = max(baseline.get(load, row['reps']), row['reps'])
    # Dated measurements already represent local calendar dates (at most seven).
    weights = query(conn, '''
        SELECT measured_on,weight_kg FROM body_measurement
        WHERE user_id=%s AND measured_on>=%s AND measured_on<%s
        ORDER BY measured_on,measurement_id
    ''', (user_id, recap['start'], recap['end']))
    return dict(recap, completed=completed, new_records=new_records,
                first_weight=weights[0] if weights else None,
                last_weight=weights[-1] if weights else None,
                weight_count=len(weights),
                weight_change=weights[-1]['weight_kg']-weights[0]['weight_kg'] if len(weights)>1 else None)
