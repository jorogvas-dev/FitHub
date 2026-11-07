"""Read-only workout progress from owned, completed session logs; no name matching."""
import json
from decimal import Decimal

from app.routes.plans import query

SNAPSHOT_PREFIX = 'FitHub workout snapshot v1\n'


def read_snapshot(notes):
    if isinstance(notes,str) and notes.startswith(SNAPSHOT_PREFIX):
        try:
            value=json.loads(notes[len(SNAPSHOT_PREFIX):])
            if (isinstance(value,dict) and isinstance(value.get('day_name'),str)
                    and isinstance(value.get('plan_name'),str) and isinstance(value.get('exercises'),list)):
                return value
        except (ValueError,TypeError,RecursionError):
            pass
    return None


def stable_id(exercise):
    value=exercise.get('exercise_id') if isinstance(exercise,dict) else None
    return value if type(value) is int and 1<=value<=9223372036854775807 else None


def display_cards(exercises, logs):
    """Keep unidentified prescriptions separate from identified logs, even at equal names."""
    cards=[]
    identified={}
    for index,exercise in enumerate(exercises):
        if not isinstance(exercise,dict):
            continue
        identifier=stable_id(exercise)
        if identifier is not None and identifier in identified:
            identified[identifier]['targets'].append(exercise)
            continue
        display={**exercise,'exercise_id':identifier,'_card_key':identifier if identifier is not None else 'legacy-'+str(index)}
        display.setdefault('name','Exercise name unavailable')
        card=dict(exercise=display,targets=[exercise],entries=[])
        cards.append(card)
        if identifier is not None:
            identified[identifier]=card
    for entry in logs:
        identifier=entry['exercise_id']
        if identifier not in identified:
            card=dict(exercise={**entry,'_card_key':identifier},targets=[],entries=[])
            identified[identifier]=card
            cards.append(card)
        identified[identifier]['entries'].append(entry)
    for card in cards:
        card['entries'].sort(key=lambda row:(row['set_number'],row['set_id']))
    return cards


def last_session_sets(conn,user_id,exercise_ids):
    identifiers=sorted(set(exercise_ids))
    if not identifiers:
        return {}
    rows=query(conn, '''
        WITH latest AS (
          SELECT DISTINCT ON (l.exercise_id) l.exercise_id,s.session_id,s.finished_at
          FROM workout_session s JOIN set_log l USING(session_id)
          WHERE s.user_id=%s AND s.finished_at IS NOT NULL AND l.exercise_id=ANY(%s)
          ORDER BY l.exercise_id,s.finished_at DESC,s.session_id DESC
        )
        SELECT l.exercise_id,l.set_id,l.set_number,l.weight_kg,l.reps,
               latest.session_id,latest.finished_at
        FROM latest JOIN set_log l ON l.session_id=latest.session_id AND l.exercise_id=latest.exercise_id
        ORDER BY l.exercise_id,l.set_number,l.set_id
    ''',(user_id,identifiers))
    result={}
    for row in rows:
        reference=result.setdefault(row['exercise_id'],dict(session_id=row['session_id'],finished_at=row['finished_at'],sets=[]))
        reference['sets'].append(row)
    return result


def exercise_choices(conn,user_id):
    return query(conn, '''SELECT DISTINCT e.exercise_id,e.name FROM exercise e
        JOIN set_log l USING(exercise_id) JOIN workout_session s USING(session_id)
        WHERE s.user_id=%s AND s.finished_at IS NOT NULL AND e.tracking_type='reps'
        ORDER BY e.name,e.exercise_id''',(user_id,))


def session_points(conn,user_id,exercise_id):
    rows=query(conn, '''
        WITH ranked AS (
          SELECT s.session_id,s.started_at,s.finished_at,s.notes,s.day_id,
                 l.set_id,l.set_number,l.weight_kg,l.reps,
                 row_number() OVER (PARTITION BY s.session_id
                   ORDER BY l.weight_kg DESC NULLS LAST,l.reps DESC,l.set_number,l.set_id) AS ranking
          FROM workout_session s JOIN set_log l USING(session_id)
          JOIN exercise e USING(exercise_id)
          WHERE s.user_id=%s AND s.finished_at IS NOT NULL AND l.exercise_id=%s AND e.tracking_type='reps'
        )
        SELECT r.*,CASE WHEN p.owner_id=%s THEN d.name END AS day_name,
               CASE WHEN p.owner_id=%s THEN p.name END AS plan_name
        FROM ranked r LEFT JOIN workout_day d USING(day_id) LEFT JOIN workout_plan p USING(plan_id)
        WHERE r.ranking=1 ORDER BY r.finished_at,r.session_id
    ''',(user_id,exercise_id,user_id,user_id))
    for row in rows:
        snapshot=read_snapshot(row['notes'])
        row['day_name']=snapshot['day_name'] if snapshot else row['day_name'] or 'Workout'
        row['plan_name']=snapshot['plan_name'] if snapshot else row['plan_name'] or 'Original training day unavailable'
    return rows


def chart_tracks(rows):
    """One point per session. Bodyweight reps never masquerade as zero kilograms."""
    if not rows:
        return dict(tracks=[],height=0)
    kinds=[kind for kind in ('weight','bodyweight') if any((row['weight_kg'] is None)==(kind=='bodyweight') for row in rows)]
    tracks=[]
    for kind in kinds:
        top=40+len(tracks)*210
        bottom=top+140
        matching=[row for row in rows if (row['weight_kg'] is None)==(kind=='bodyweight')]
        maximum=max(Decimal(1),max(row['reps'] if kind=='bodyweight' else row['weight_kg'] for row in matching))
        points=[]
        segments=[]
        segment=[]
        for index,row in enumerate(rows):
            if (row['weight_kg'] is None)!=(kind=='bodyweight'):
                if len(segment)>1:
                    segments.append(' '.join(segment))
                segment=[]
                continue
            value=row['reps'] if kind=='bodyweight' else row['weight_kg']
            x=235 if len(rows)==1 else 90+Decimal(290)*index/(len(rows)-1)
            y=bottom-Decimal(140)*value/maximum
            point=dict(x=f'{x:.2f}',y=f'{y:.2f}',row=row,number=index+1)
            points.append(point)
            segment.append(point['x']+','+point['y'])
        if len(segment)>1:
            segments.append(' '.join(segment))
        tracks.append(dict(label='Recorded weight (kg)' if kind=='weight' else 'Bodyweight sets (reps)',
                           top=top,bottom=bottom,maximum=str(maximum),points=points,segments=segments))
    return dict(tracks=tracks,height=len(tracks)*210+25)
