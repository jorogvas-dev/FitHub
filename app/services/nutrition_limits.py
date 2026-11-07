"""Exact upper limits and comparable, normalized target shortfalls."""
from decimal import Decimal
from .nutrition import NUTRIENTS


def configured(targets):
    return {key:Decimal(targets[key]) for key,_,_ in NUTRIENTS if targets.get(key) is not None}


def errors(values,targets):
    result=[]
    for key,label,unit in NUTRIENTS:
        if targets.get(key) is None:continue
        limit=Decimal(targets[key])
        value=values[key]
        if value is None:
            result.append(f'{label} is unknown; the configured upper limit cannot be checked.')
        elif not value.is_finite() or value>limit:
            result.append(f'{label} exceeds the current upper limit ({value} > {limit} {unit}).')
    return result


def combine(left,right):
    return {key:None if left[key] is None or right[key] is None else left[key]+right[key] for key,_,_ in NUTRIENTS}


def shortfall(values,targets,fraction=Decimal(1)):
    return sum(((limit*fraction-values[key])/(limit if limit>0 else Decimal(1)))**2 for key,limit in configured(targets).items())


def remaining(values,targets):
    return {key:None if targets.get(key) is None or values[key] is None else Decimal(targets[key])-values[key] for key,_,_ in NUTRIENTS}
