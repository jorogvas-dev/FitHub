"""Bounded catalogue selection and validation independent of the inference provider."""
import json
import math
import re

MAX_CANDIDATES = 48
MAX_EXERCISES = 8


class DuplicateExerciseError(ValueError):
    """Keep editor wording while supplying precise, trusted correction feedback."""
    def __init__(self, day_number, exercise_id):
        self.day_number = day_number
        self.exercise_id = exercise_id
        super().__init__('An exercise may only appear once within a training day.')


EQUIPMENT = (
    ('dumbbells', 'Dumbbells'), ('barbell', 'Barbell'), ('ez_bar', 'EZ bar'),
    ('bench', 'Flat bench'), ('incline_bench', 'Incline bench'), ('bar', 'Low bar'),
    ('pull_up_bar', 'Pull-up bar'), ('parallel_bars', 'Parallel bars'),
    ('machines', 'Gym resistance machines'), ('cables', 'Cable machine'),
    ('box', 'Stable box'), ('wall', 'Wall'), ('rings', 'Rings'),
)
# Unknown equipment descriptions are excluded rather than guessed.
REQUIREMENTS = {
    'bodyweight': set(), 'barbell': {'barbell'}, 'ez bar': {'ez_bar'},
    'dumbbell': {'dumbbells'}, 'dumbbells': {'dumbbells'}, 'bench': {'bench'},
    'incline bench': {'incline_bench'}, 'bar': {'bar'}, 'pull-up bar': {'pull_up_bar'},
    'parallel bars': {'parallel_bars'}, 'rings': {'rings'}, 'wall': {'wall'},
    'barbell and bench': {'barbell', 'bench'},
    'barbell and incline bench': {'barbell', 'incline_bench'},
    'dumbbell and bench': {'dumbbells', 'bench'},
    'dumbbells and bench': {'dumbbells', 'bench'},
    'dumbbells and incline bench': {'dumbbells', 'incline_bench'},
    'dumbbells and box': {'dumbbells', 'box'}, 'cable machine': {'cables'},
    **{name: {'machines'} for name in ('assistance machine', 'leg press machine',
       'leg extension machine', 'leg curl machine', 'chest press machine',
       'pec deck machine', 'smith machine', 'calf raise machine', 'shoulder press machine')},
}
MOVEMENTS = (
    ('squat', 'Squats and leg presses/extensions', r'squat|leg press|leg extension'),
    ('lunge', 'Lunges and step-ups', r'lunge|step-up'),
    ('hinge', 'Hip hinges, deadlifts and bridges', r'deadlift|hip thrust|glute bridge|back extension'),
    ('overhead', 'Overhead movements', r'overhead|shoulder press|pike push-up|pull-up|chin-up|pulldown|hanging'),
    ('press', 'Chest presses, push-ups, dips and chest flyes', r'bench press|chest press|push-up|\bdip\b|cable fly|^pec deck'),
    ('vertical_pull', 'Pull-ups, chin-ups and pulldowns', r'pull-up|chin-up|pulldown'),
    ('row', 'Rows and reverse flyes', r'\brow\b|rear delt|reverse pec deck|face pull'),
    ('curl', 'Arm curls', r'barbell curl|hammer curl|biceps? curl'),
    ('triceps', 'Triceps extensions', r'triceps|skull crusher'),
    ('core', 'Core exercises', r'dead bug|bird dog|crunch|knee raise|leg raise|plank|hollow'),
    ('calves', 'Calf raises', r'calf raise'),
)


def integer(value, label, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f'{label} must be a whole number between {low} and {high}.')
    return value


def form_integer(value, label, low, high):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]{1,9}', value):
        raise ValueError(f'{label} must be a whole number between {low} and {high}.')
    return integer(int(value), label, low, high)


def preferences_input(form):
    equipment = form.getlist('equipment')
    avoid = form.getlist('avoid_movements')
    if any(item not in dict(EQUIPMENT) for item in equipment):
        raise ValueError('Choose equipment from the listed options.')
    if any(item not in {key for key, _, _ in MOVEMENTS} for item in avoid):
        raise ValueError('Choose movements to avoid from the listed options.')
    return dict(days_per_week=form_integer(form.get('days_per_week'), 'Training days', 1, 7),
                minutes_per_session=form_integer(form.get('minutes_per_session'), 'Session minutes', 15, 180),
                equipment=sorted(set(equipment)), avoid_movements=sorted(set(avoid)))


def eligible(exercise, preferences):
    if not exercise['is_active'] or exercise['tracking_type'] != 'reps':
        return False
    required = REQUIREMENTS.get(exercise['equipment'].strip().casefold())
    if required is None:
        return False
    required = set(required)
    name = exercise['name'].casefold()
    if 'bench press' in name:
        required.add('incline_bench' if 'incline' in name else 'bench')
    if not required.issubset(set(preferences['equipment'])):
        return False
    patterns = {key for key, _, pattern in MOVEMENTS if re.search(pattern, name)}
    # A custom movement without a recognized pattern needs manual review when exclusions exist.
    if preferences['avoid_movements'] and not patterns:
        return False
    return not patterns.intersection(preferences['avoid_movements'])


def candidates(catalogue, preferences):
    return [row for row in catalogue if eligible(row, preferences)][:MAX_CANDIDATES]


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('AI returned duplicate JSON fields.')
            result[key] = value
        return result
    def constant(value):
        raise ValueError('AI returned a non-finite number.')
    if not isinstance(raw, str) or len(raw.encode('utf-8')) > 32768:
        raise ValueError('AI response is empty or too large.')
    try:
        return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
    except (json.JSONDecodeError, RecursionError) as exc:
        raise ValueError('AI returned invalid structured JSON. No plan was created.') from exc


def fields(value, names):
    if not isinstance(value, dict) or set(value) != set(names):
        raise ValueError('AI draft has missing or unsupported fields. Timed prescriptions are not supported.')


def name_input(value, label):
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= 100 or any(ord(c) < 32 for c in value):
        raise ValueError(f'{label} must contain 1–100 readable characters.')
    return value.strip()


def validate_document(document, preferences, catalogue, allowed_ids):
    fields(document, ('name', 'days'))
    name = name_input(document['name'], 'Plan name')
    days = document['days']
    if not isinstance(days, list) or len(days) != preferences['days_per_week']:
        raise ValueError('The draft must have exactly the selected number of training days.')
    lookup = {row['exercise_id']: row for row in catalogue}
    allowed = set(allowed_ids)
    result = []
    day_names = set()
    for day_number, day in enumerate(days, 1):
        fields(day, ('name', 'estimated_minutes', 'exercises'))
        day_name = name_input(day['name'], 'Day name')
        if day_name.casefold() in day_names:
            raise ValueError('Training day names must be distinct.')
        day_names.add(day_name.casefold())
        integer(day['estimated_minutes'], 'Estimated minutes', 1, preferences['minutes_per_session'])
        rows = day['exercises']
        if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_EXERCISES:
            raise ValueError('Each day needs 1–8 exercises.')
        exercises = []
        used = set()
        for row in rows:
            fields(row, ('exercise_id', 'sets', 'reps_min', 'reps_max'))
            identifier = integer(row['exercise_id'], 'Exercise ID', 1, 2147483647)
            exercise = lookup.get(identifier)
            if identifier not in allowed or exercise is None or not eligible(exercise, preferences):
                raise ValueError('An exercise is unavailable, excluded, timed, or requires equipment you did not select. Review preferences or regenerate.')
            if identifier in used:
                raise DuplicateExerciseError(day_number, identifier)
            used.add(identifier)
            sets = integer(row['sets'], 'Sets', 1, 6)
            low = integer(row['reps_min'], 'Minimum reps', 1, 30)
            high = integer(row['reps_max'], 'Maximum reps', low, 30)
            exercises.append(dict(exercise_id=identifier, sets=sets, reps_min=low, reps_max=high))
        estimate = math.ceil(5 + sum(row['sets'] for row in exercises) * 2.5)
        if estimate > preferences['minutes_per_session']:
            raise ValueError('This day exceeds the selected session estimate. Reduce sets or exercises, or change your preferences.')
        result.append(dict(name=day_name, estimated_minutes=estimate, exercises=exercises))
    return dict(name=name, days=result)


def response_schema(preferences, exercise_ids):
    number = lambda low, high: dict(type='integer', minimum=low, maximum=high)
    prescription = dict(type='object', additionalProperties=False,
        required=['exercise_id', 'sets', 'reps_min', 'reps_max'], properties={
            'exercise_id': dict(type='integer', enum=exercise_ids), 'sets': number(1, 6),
            'reps_min': number(1, 30), 'reps_max': number(1, 30)})
    day = dict(type='object', additionalProperties=False,
        required=['name', 'estimated_minutes', 'exercises'], properties={
            'name': dict(type='string', minLength=1, maxLength=100),
            'estimated_minutes': number(1, preferences['minutes_per_session']),
            'exercises': dict(type='array', minItems=1, maxItems=min(MAX_EXERCISES,len(set(exercise_ids))), items=prescription)})
    return dict(type='object', additionalProperties=False, required=['name', 'days'], properties={
        'name': dict(type='string', minLength=1, maxLength=100),
        'days': dict(type='array', minItems=preferences['days_per_week'], maxItems=preferences['days_per_week'], items=day)})
