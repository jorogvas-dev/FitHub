"""Derived records for supported, owned completed logs. No stored PR flags or name matching."""
from collections import defaultdict

from app.routes.plans import query
from .workout_progress import stable_id


def workout_badges(conn,user_id,session,logs):
    if session['finished_at'] is None or session['user_id']!=user_id:
        return {}
    eligible=[row for row in logs if row.get('tracking_type')=='reps' and stable_id(row) is not None
              and row['session_id']==session['session_id']]
    identifiers=sorted({row['exercise_id'] for row in eligible})
    if not identifiers:
        return {}
    # All exercises/loads are fetched together. Same-session sets never supply baselines.
    earlier=query(conn, '''
        SELECT l.exercise_id,l.weight_kg,max(l.reps) AS reps
        FROM set_log l JOIN workout_session s USING(session_id) JOIN exercise e USING(exercise_id)
        WHERE s.user_id=%s AND s.finished_at IS NOT NULL AND e.tracking_type='reps'
          AND (s.finished_at,s.session_id)<(%s,%s) AND l.exercise_id=ANY(%s)
        GROUP BY l.exercise_id,l.weight_kg
    ''',(user_id,session['finished_at'],session['session_id'],identifiers))
    previous=defaultdict(dict)
    for row in earlier:
        previous[row['exercise_id']][row['weight_kg']]=row['reps']
    return badges_from_sets(eligible,previous)


def badges_from_sets(eligible,previous):
    """Award one best set per category, against earlier sessions only.

    Callers supply supported, owned logs from one completed session and a
    per-exercise/per-load baseline. Weekly recaps reuse this without per-session SQL.
    """
    current=defaultdict(list)
    for row in eligible:
        current[row['exercise_id']].append(row)
    badges={}
    def award(row,kind,**extra):
        badges.setdefault(row['set_id'],[]).append(dict(kind=kind,**extra))
    for identifier,sets in current.items():
        baseline=previous.get(identifier,{})
        weighted=[row for row in sets if row['weight_kg'] is not None]
        previous_loads=[load for load in baseline if load is not None]
        if weighted and previous_loads:
            winner=min(weighted,key=lambda row:(-row['weight_kg'],-row['reps'],row['set_number'],row['set_id']))
            if winner['weight_kg']>max(previous_loads):
                award(winner,'heaviest')
        loads=defaultdict(list)
        for row in sets:
            loads[row['weight_kg']].append(row)
        for load,candidates in loads.items():
            if load not in baseline:  # First result at this exact load/type is a baseline.
                continue
            winner=min(candidates,key=lambda row:(-row['reps'],row['set_number'],row['set_id']))
            if winner['reps']>baseline[load]:
                award(winner,'bodyweight_reps' if load is None else 'load_reps',weight_kg=load)
    return badges


def exercise_records(conn,user_id,exercise_id):
    # Per-load maxima require every logged set, not just each session's heaviest point.
    # Exact ties retain the first achieved result in deterministic completion/set order.
    rows=query(conn, '''
        SELECT DISTINCT ON (l.weight_kg) l.set_id,l.set_number,l.weight_kg,l.reps,
               s.session_id,s.finished_at
        FROM set_log l JOIN workout_session s USING(session_id) JOIN exercise e USING(exercise_id)
        WHERE s.user_id=%s AND s.finished_at IS NOT NULL AND l.exercise_id=%s AND e.tracking_type='reps'
        ORDER BY l.weight_kg DESC NULLS LAST,l.reps DESC,s.finished_at,s.session_id,l.set_number,l.set_id
    ''',(user_id,exercise_id))
    weighted=[row for row in rows if row['weight_kg'] is not None]
    return dict(heaviest=weighted[0] if weighted else None,by_load=weighted,
                bodyweight=next((row for row in rows if row['weight_kg'] is None),None))
