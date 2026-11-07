"""Server-derived workout facts and constrained, advisory AI prioritization."""
from collections import defaultdict
from decimal import Decimal

from app.routes.plans import query
from . import personal_records, workout_ai
from .workout_generation import strict_json
from .workout_progress import read_snapshot, stable_id

EXPLANATIONS = {
    'consistency': 'Practise consistent repetitions at the same logged load before considering any load change.',
    'prescription': 'Stay within the saved rep range and keep the prescribed number of sets.',
}


def previous_sets(conn, user_id, session, identifiers):
    """One bulk query, before the selected workout, separated by set type."""
    if not identifiers:
        return []
    return query(conn, '''
        WITH latest AS (
          SELECT DISTINCT ON (l.exercise_id,(l.weight_kg IS NULL))
                 l.exercise_id,(l.weight_kg IS NULL) AS bodyweight,s.session_id,s.finished_at
          FROM set_log l JOIN workout_session s USING(session_id)
          WHERE s.user_id=%s AND s.finished_at IS NOT NULL
            AND (s.finished_at,s.session_id)<(%s,%s) AND l.exercise_id=ANY(%s)
          ORDER BY l.exercise_id,(l.weight_kg IS NULL),s.finished_at DESC,s.session_id DESC
        )
        SELECT l.set_id,l.exercise_id,l.set_number,l.weight_kg,l.reps,
               latest.session_id,latest.finished_at
        FROM latest JOIN set_log l ON l.session_id=latest.session_id
          AND l.exercise_id=latest.exercise_id AND (l.weight_kg IS NULL)=latest.bodyweight
        ORDER BY l.exercise_id,latest.bodyweight,l.set_number,l.set_id
    ''', (user_id,session['finished_at'],session['session_id'],identifiers))


def load_label(weight):
    return 'unweighted bodyweight' if weight is None else f'{weight} kg'


def build_facts(conn, user_id, session, logs, badges):
    if session['user_id'] != user_id or session['finished_at'] is None:
        raise ValueError('Only your own completed workouts can be reviewed.')
    # Defend the service boundary too, not just the route's ownership check.
    if any(row['session_id'] != session['session_id'] for row in logs):
        raise ValueError('Logs do not belong to the selected workout.')
    snapshot = read_snapshot(session['notes'])
    grouped = defaultdict(list)
    for row in logs:
        grouped[row['exercise_id']].append(row)
    earlier = defaultdict(list)
    for row in previous_sets(conn,user_id,session,sorted(grouped)):
        earlier[row['exercise_id']].append(row)
    facts, targets, exercises = [], [], []
    def fact(kind, text, exercise_id=None):
        facts.append(dict(id=f'fact_{len(facts)+1}',kind=kind,text=text,exercise_id=exercise_id))
    count = sum(len(reasons) for reasons in badges.values())
    fact('summary',f"{len(logs)} logged set{'s' if len(logs) != 1 else ''} across {len(grouped)} exercise{'s' if len(grouped) != 1 else ''}; {count} new record{'s' if count != 1 else ''} earned.")
    prescriptions = defaultdict(list)
    for item in snapshot['exercises'] if snapshot else []:
        if stable_id(item) is not None:
            prescriptions[item['exercise_id']].append(item)
    for identifier, rows in grouped.items():
        rows.sort(key=lambda row:(row['set_number'],row['set_id']))
        name = rows[0]['name']
        supported = rows[0]['tracking_type'] == 'reps'
        # IDs and numbers are serialized as data. No names or text from the model render.
        def packed(row):
            return dict(set_id=row['set_id'],set_number=row['set_number'],reps=row['reps'],
                        weight_kg=str(row['weight_kg']) if row['weight_kg'] is not None else None)
        prior = earlier[identifier]
        exercises.append(dict(exercise_id=identifier,name=name,tracking_type=rows[0]['tracking_type'],
            logged_sets=[packed(row) for row in rows],
            previous_sets=[dict(packed(row),session_id=row['session_id'],finished_at=row['finished_at'].isoformat()) for row in prior],
            records=[dict(set_id=row['set_id'],kind=reason['kind'],
                          weight_kg=str(reason['weight_kg']) if reason.get('weight_kg') is not None else None)
                     for row in rows for reason in badges.get(row['set_id'],[])]))
        if not supported:
            fact('unsupported',f'{name}: {len(rows)} legacy sets retained. Timed-duration comparisons and targets are unavailable.',identifier)
            continue
        for bodyweight in (False,True):
            current = [row for row in rows if (row['weight_kg'] is None) == bodyweight]
            if not current:
                continue
            previous = [row for row in prior if (row['weight_kg'] is None) == bodyweight]
            best = min(current,key=lambda row:(-(row['weight_kg'] or Decimal(0)),-row['reps'],row['set_number'],row['set_id']))
            fact('logged',f"{name}: {len(current)} {'unweighted' if bodyweight else 'weighted'} sets; best set {load_label(best['weight_kg'])} × {best['reps']} reps.",identifier)
            if not previous:
                fact('missing_history',f"{name}: no earlier comparable {'unweighted' if bodyweight else 'weighted'} completed session. No comparison or next-session target is available for these sets.",identifier)
                continue
            if not bodyweight:
                previous_best = min(previous,key=lambda row:(-row['weight_kg'],-row['reps'],row['set_number'],row['set_id']))
                change = best['weight_kg']-previous_best['weight_kg']
                fact('load_comparison',f"{name}: heaviest logged load {previous_best['weight_kg']} → {best['weight_kg']} kg ({change:+f} kg) versus {previous_best['finished_at'].strftime('%d %b %Y')}, session #{previous_best['session_id']}.",identifier)
            current_loads = {row['weight_kg'] for row in current}
            previous_loads = {row['weight_kg'] for row in previous}
            common = sorted(current_loads & previous_loads,key=lambda value:(value is None,value or Decimal(0)))
            if not common:
                fact('different_load',f'{name}: the previous comparable session has no matching logged load; rep changes and targets are omitted.',identifier)
            for load in common:
                now_reps = max(row['reps'] for row in current if row['weight_kg'] == load)
                before_reps = max(row['reps'] for row in previous if row['weight_kg'] == load)
                date = previous[0]['finished_at'].strftime('%d %b %Y')
                delta = now_reps-before_reps
                fact('comparison',f'{name} at {load_label(load)}: best reps {before_reps} → {now_reps} ({delta:+d}) versus {date}, session #{previous[0]["session_id"]}.',identifier)
        for row in rows:
            for reason in badges.get(row['set_id'],[]):
                label = {'heaviest':'heaviest load','bodyweight_reps':'unweighted reps','load_reps':'reps at the same load'}[reason['kind']]
                fact('record',f"{name}, set {row['set_number']}: new record for {label} ({load_label(row['weight_kg'])} × {row['reps']} reps).",identifier)
        options = prescriptions[identifier]
        prescription = options[0] if len(options) == 1 else None
        valid = prescription and prescription.get('tracking_type') == 'reps' and all(type(prescription.get(key)) is int for key in ('target_sets','reps_min','reps_max'))
        valid = valid and 1 <= prescription['target_sets'] <= 30 and 1 <= prescription['reps_min'] <= prescription['reps_max'] <= 1000
        if not valid:
            fact('missing_prescription',f'{name}: no single valid saved prescription. No next-session target is suggested.',identifier)
            continue
        # No extrapolated kg increments, mixed-load targets, inferred prescriptions,
        # timed/inactive targets, or progression without earlier same-load evidence.
        loads = {row['weight_kg'] for row in rows}
        if (not rows[0]['is_active'] or len(loads) != 1 or len(rows) != prescription['target_sets']
                or not all(prescription['reps_min'] <= row['reps'] <= prescription['reps_max'] for row in rows)):
            continue
        load = rows[0]['weight_kg']
        minimum = min(row['reps'] for row in rows)+1
        if minimum > prescription['reps_max'] or not any(row['weight_kg'] == load for row in prior):
            continue
        targets.append(dict(id=f'target_{len(targets)+1}',exercise_id=identifier,sets=prescription['target_sets'],
            weight_kg=str(load) if load is not None else None,reps_min=minimum,reps_max=prescription['reps_max'],
            text=f"{name}: optionally aim for {minimum}–{prescription['reps_max']} reps in each of {prescription['target_sets']} sets at {load_label(load)} next session, if comfortable.",
            explanation_ids=list(EXPLANATIONS)))
    # Include only the selected immutable snapshot, never today's edited plan.
    return dict(workout=dict(session_id=session['session_id'],finished_at=session['finished_at'].isoformat(),
                            prescription_snapshot=snapshot),exercises=exercises,facts=facts,targets=targets,
                explanations=EXPLANATIONS)


def response_schema(data):
    ids = [row['id'] for row in data['facts']]
    targets = [row['id'] for row in data['targets']]
    return dict(type='object',additionalProperties=False,required=['highlights','suggestions'],properties=dict(
        highlights=dict(type='array',minItems=1,maxItems=6,items=dict(type='string',enum=ids)),
        suggestions=dict(type='array',maxItems=min(3,len(targets)),items=dict(type='object',additionalProperties=False,
            required=['target_id','explanation_id'],properties=dict(
                target_id=dict(type='string',enum=targets or ['no_eligible_targets']),
                explanation_id=dict(type='string',enum=list(EXPLANATIONS)))))))


def validate_response(raw, data):
    value = strict_json(raw)
    if not isinstance(value,dict) or set(value) != {'highlights','suggestions'}:
        raise ValueError('Invalid review structure.')
    highlights, suggestions = value['highlights'], value['suggestions']
    facts = {row['id']:row for row in data['facts']}
    targets = {row['id']:row for row in data['targets']}
    if (not isinstance(highlights,list) or not 1 <= len(highlights) <= 6
            or any(type(key) is not str or key not in facts for key in highlights)
            or len(set(highlights)) != len(highlights)
            or not isinstance(suggestions,list) or len(suggestions)>3):
        raise ValueError('Invalid review references.')
    chosen, seen = [], set()
    for item in suggestions:
        if not isinstance(item,dict) or set(item) != {'target_id','explanation_id'}:
            raise ValueError('Invalid target structure.')
        target, reason = item['target_id'], item['explanation_id']
        if (type(target) is not str or target not in targets or target in seen
                or type(reason) is not str or reason not in targets[target]['explanation_ids']):
            raise ValueError('Invalid target reference.')
        seen.add(target)
        chosen.append(dict(targets[target],explanation=EXPLANATIONS[reason]))
    return dict(mode='ai',highlights=[facts[key] for key in highlights],suggestions=chosen)


def generate_review(data):
    try:
        return validate_response(workout_ai.review(data,response_schema(data)),data)
    except (ValueError,TypeError,KeyError,RecursionError):
        return dict(mode='fallback',highlights=data['facts'][:4],suggestions=[])
