"""Date-only scheduling and owned, timezone-aware completion events in bulk."""
from calendar import Calendar
from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo
import hashlib
import json
from app.routes.plans import query
from .workout_progress import read_snapshot


def fingerprint(snapshot):
    return hashlib.sha256(json.dumps(snapshot,sort_keys=True).encode()).hexdigest()


def month_bounds(value):
    start=value.replace(day=1)
    end=date(start.year+1,1,1) if start.month==12 else date(start.year,start.month+1,1)
    previous=date(start.year-1,12,1) if start.month==1 else date(start.year,start.month-1,1)
    return start,end,previous


def status(row,today):
    if row.get('cancelled_at'):return 'Cancelled'
    if row.get('finished_at'):return 'Completed'
    if row['scheduled_on']<today:return 'Not completed'
    if row.get('session_id'):return 'In progress'
    return 'Scheduled'


def upcoming(conn,user_id,today):
    rows=query(conn,'''SELECT w.*,s.finished_at,d.day_id AS available_day FROM workout_schedule w
        LEFT JOIN workout_session s ON s.session_id=w.session_id AND s.user_id=w.user_id
        LEFT JOIN workout_day d ON d.day_id=w.day_id
        JOIN app_user u ON u.user_id=w.user_id
        WHERE w.user_id=%s AND w.scheduled_on>=%s AND w.cancelled_at IS NULL AND s.finished_at IS NULL
        ORDER BY w.scheduled_on,w.schedule_id LIMIT 3''',(user_id,today))
    for row in rows:row['status']=status(row,today)
    return rows


def month_view(conn,user_id,month,today,zone):
    start,end,previous=month_bounds(month)
    zoneinfo=ZoneInfo(zone)
    lower=datetime.combine(start,time.min,zoneinfo).astimezone(timezone.utc)
    upper=datetime.combine(end,time.min,zoneinfo).astimezone(timezone.utc)
    planned=query(conn,'''SELECT w.*,s.finished_at,d.day_id AS available_day FROM workout_schedule w
        LEFT JOIN workout_session s ON s.session_id=w.session_id AND s.user_id=w.user_id
        LEFT JOIN workout_day d ON d.day_id=w.day_id
        WHERE w.user_id=%s AND w.scheduled_on>=%s AND w.scheduled_on<%s
        ORDER BY w.scheduled_on,w.schedule_id''',(user_id,start,end))
    completed=query(conn,'''SELECT s.*,d.name AS day_name,p.name AS plan_name,w.schedule_id,w.scheduled_on
        FROM workout_session s
        LEFT JOIN workout_day d ON d.day_id=s.day_id
        LEFT JOIN workout_plan p ON p.plan_id=d.plan_id AND p.owner_id=s.user_id
        LEFT JOIN workout_schedule w ON w.session_id=s.session_id AND w.user_id=s.user_id
        WHERE s.user_id=%s AND s.finished_at>=%s AND s.finished_at<%s
        ORDER BY s.finished_at,s.session_id''',(user_id,lower,upper))
    events=[]
    for row in planned:
        if row['finished_at']:continue  # Completed once, on its actual local completion date.
        events.append(dict(date=row['scheduled_on'],name=row['prescription']['day_name'],
            plan_name=row['prescription']['plan_name'],status=status(row,today),schedule_id=row['schedule_id'],
            session_id=row['session_id'],deleted=row['available_day'] is None,sort=(0,row['schedule_id'])))
    for row in completed:
        snapshot=read_snapshot(row['notes']) or {}
        local=row['finished_at'].astimezone(zoneinfo)
        events.append(dict(date=local.date(),name=snapshot.get('day_name',row['day_name'] or 'Workout'),
            plan_name=snapshot.get('plan_name',row['plan_name'] or 'Original plan unavailable'),status='Completed',
            schedule_id=row['schedule_id'],session_id=row['session_id'],scheduled_on=row['scheduled_on'],
            finished_local=local,sort=(1,local.timestamp(),row['session_id'])))
    events.sort(key=lambda row:(row['date'],row['sort']))
    grouped={}
    for event in events:grouped.setdefault(event['date'],[]).append(event)
    return dict(start=start,end=end,previous=previous if previous.year>=1900 else None,
        next=end if end.year<=2100 else None,weeks=Calendar(firstweekday=0).monthdatescalendar(start.year,start.month),
        events=events,grouped=grouped,today=today,timezone=zone)
