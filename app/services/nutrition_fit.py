"""Bounded server selection: exact caps, main meals first, optional improving snack."""
from decimal import Decimal as D
from .meal_planner import PORTIONS, portion_entries, totals
from .nutrition import MEALS, NUTRIENTS
from . import nutrition_limits as limits

BEAM_WIDTH=128
MAX_RECIPES_PER_SLOT=20
ZERO={key:D(0) for key,_,_ in NUTRIENTS}


def candidate_score(recipe,targets,meal):
    """Consider the full eligible pool before the unchanged per-slot search cap."""
    if not limits.configured(targets):return D(0)
    fraction={'breakfast':D('.3'),'lunch':D('.35'),'dinner':D('.35'),'snack':D('.1')}[meal]
    scores=[]
    for portion in PORTIONS:
        try:value=totals(portion_entries(recipe,portion))
        except ValueError:continue
        if not limits.errors(value,targets):scores.append(limits.shortfall(value,targets,fraction))
    return min(scores,default=D('Infinity'))


def fit(recipes,targets,preferences=None):
    preferences=preferences or []
    notes=['Server search: up to 20 eligible recipes per slot; 0.25–2 servings in 0.25 steps; 128 retained states. Global optimality is not guaranteed.']
    options=[]
    for index,(meal,label) in enumerate(MEALS):
        preferred=preferences[index] if index<len(preferences) else None
        available=[row for row in recipes if meal in row['meals']]
        ordered=sorted(enumerate(available),key=lambda item:(candidate_score(item[1],targets,meal),
            not(preferred and item[1]['recipe_id']==preferred[0]['recipe_id']),item[0]))
        eligible=[row for _,row in ordered][:MAX_RECIPES_PER_SLOT]
        group=[]
        for recipe in eligible:
            portions=sorted(PORTIONS,key=lambda p:(p!=(preferred[1] if preferred else D(1)),p))
            for portion in portions:
                try:value=totals(portion_entries(recipe,portion))
                except ValueError:continue
                if not limits.errors(value,targets):group.append((recipe,portion,value))
        options.append(group)
        if not group and meal!='snack':notes.append(f'{label} omitted: no eligible portion can be checked within every configured limit.')
    if not recipes:return None,['No eligible recipes are available; exclusions were not relaxed.']
    if not limits.configured(targets):
        selected=[next((row for row in group if row[1]==D(1)),group[0] if group else None) for group in options[:3]]
        missing=[label for (_,label),row in zip(MEALS[:3],selected) if row is None]
        if missing:notes.append('Partial day: '+', '.join(missing)+'. No eligible recipe is available for these main meals.')
        notes.append('No daily targets are set. Started with one serving per available main meal. No optional snack was added because no configured target fit can improve.')
        return [(row[0],row[1]) if row else None for row in selected]+[None],notes
    beam=[(ZERO.copy(),(),())]
    fractions=(D('.3'),D('.65'),D(1))
    for stage,group in enumerate(options[:3]):
        distinct={}
        for current,chosen,tie in beam:
            for index,option in enumerate([None]+group):
                value=limits.combine(current,option[2]) if option else current
                if limits.errors(value,targets):continue
                picks=chosen+(option,)
                order=tie+(index,)
                score=(limits.shortfall(value,targets,fractions[stage]),-sum(row is not None for row in picks),order)
                key=tuple(value[key] for key,_,_ in NUTRIENTS)
                if key not in distinct or score<distinct[key][0]:distinct[key]=(score,value,picks,order)
        best=sorted(distinct.values(),key=lambda row:row[0])[:BEAM_WIDTH]
        beam=[(row[1],row[2],row[3]) for row in best]
        # Keep the empty state so a tight early meal cannot block a feasible later one.
        if not any(not any(chosen) for _,chosen,_ in beam):beam[-1]=(ZERO.copy(),(None,)*(stage+1),(0,)*(stage+1))
    finalists=[]
    for value,chosen,tie in beam:
        initial_score=limits.shortfall(value,targets)
        finalists.append((initial_score,-sum(row is not None for row in chosen),0,tie,value,chosen+(None,)))
        for index,snack in enumerate(options[3]):
            combined=limits.combine(value,snack[2])
            if limits.errors(combined,targets):continue
            score=limits.shortfall(combined,targets)
            # A snack must strictly improve the same day's normalized shortfall.
            if score<initial_score:
                finalists.append((score,-sum(row is not None for row in chosen),1,tie+(index,),combined,chosen+(snack,)))
    winner=min(finalists,key=lambda row:row[:4])
    value,chosen=winner[4],winner[5]
    if chosen[3]:notes.append('An optional eligible snack was added because it strictly improves target fit without exceeding any limit.')
    else:notes.append('No optional snack improved the feasible target fit.')
    if not any(chosen):notes.append('No nonzero meal portion fits all current limits. This empty feasible preview changes no targets or diary entries.')
    omitted=[label for (meal,label),option in zip(MEALS[:3],chosen[:3]) if option is None]
    if omitted:notes.append('Partial day: '+', '.join(omitted)+'. Available portions cannot fill these slots within every limit in the bounded search.')
    for key,label,unit in NUTRIENTS:
        if targets.get(key) is not None:
            gap=D(targets[key])-value[key]
            if gap>0:notes.append(f'{label} shortfall: {gap} {unit}. Available recipe ratios, portion bounds or other limits prevent filling this gap in the search.')
    if all(value[key]==limit for key,limit in limits.configured(targets).items()):notes.append('All set targets matched exactly; every configured upper limit is satisfied.')
    return [(row[0],row[1]) if row else None for row in chosen],notes
