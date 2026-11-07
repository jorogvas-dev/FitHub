"""Explicit database upgrades and administrator promotion; never run at request time."""
from pathlib import Path
import json
from decimal import Decimal

import click
from psycopg.types.json import Jsonb
from app.db import connect

DB_ROOT = Path(__file__).resolve().parents[1] / 'db'


def upgrade(conn):
    # Serialize operators/replicas and commit the ledger together with all migrations.
    conn.execute('SELECT pg_advisory_xact_lock(%s)', (734104021,))
    conn.execute('CREATE TABLE IF NOT EXISTS schema_migration '
                 '(version text PRIMARY KEY,applied_at timestamptz NOT NULL DEFAULT now())')
    applied = {row[0] for row in conn.execute('SELECT version FROM schema_migration').fetchall()}
    changed = []
    for path in sorted((DB_ROOT / 'migrations').glob('*.sql')):
        if path.stem not in applied:
            conn.execute(path.read_text(encoding='utf-8'))
            conn.execute('INSERT INTO schema_migration(version) VALUES (%s)', (path.stem,))
            changed.append(path.stem)
    return changed


def seed_exercises(conn):
    conn.execute('SELECT pg_advisory_xact_lock(%s)', (734104021,))
    conn.execute((DB_ROOT / 'migrations/002_exercise_library.sql').read_text(encoding='utf-8'))


def seed_foods(conn):
    """Insert missing permanent source IDs; never undo administrator edits/status."""
    conn.execute('SELECT pg_advisory_xact_lock(%s)', (734104021,))
    data = json.loads((DB_ROOT / 'data/usda-sr-2018.json').read_text(encoding='utf-8'))
    for food in data['foods']:
        provenance = {key: value for key, value in data.items() if key != 'foods'}
        provenance['publication_date'] = food['publication_date']
        conn.execute('INSERT INTO food_catalogue(name,preparation,kcal_100g,protein_100g,carbs_100g,fat_100g,'
                     'source,source_id,dataset_version,source_url,provenance) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) '
                     'ON CONFLICT(source,source_id) DO NOTHING',
                     (food['name'], food['preparation'],
                      *(None if food[key] is None else Decimal(food[key]) for key in
                        ('kcal_100g','protein_100g','carbs_100g','fat_100g')),
                      data['source'], food['source_id'], data['dataset'], food['source_url'], Jsonb(provenance)))


def register_commands(app):
    @app.cli.command('nutrition-smoke')
    @click.option('--allergy',default='',help='Synthetic comma-separated allergy labels; no account data is read.')
    @click.option('--exclude',default='',help='Synthetic excluded ingredient labels.')
    @click.option('--kcal',default=None)
    @click.option('--protein',default=None)
    @click.option('--carbs',default=None)
    @click.option('--fat',default=None)
    def nutrition_smoke(allergy,exclude,kcal,protein,carbs,fat):
        """Live FitHub Coach check using public recipes only. Creates no draft or diary."""
        from app.services import nutrition_ai, recipe_catalogue, workout_ai
        from app.services.starter_recipes import candidates as starter_candidates
        from app.services.meal_planner import restrictions, totals, portion_entries
        from flask import current_app
        from time import monotonic
        try:
            rules,errors=restrictions(dict(allergies=allergy,unwanted_ingredients=exclude,dietary_preferences=''))
            if errors:raise ValueError('Synthetic restrictions are unsupported.')
            with connect() as conn:
                eligible,excluded=recipe_catalogue.eligible(conn,rules,require_nutrition=False)
                snacks,_=starter_candidates(conn,rules,require_nutrition=False)
            targets=dict(kcal=kcal,protein=protein,carbs=carbs,fat=fat)
            start=monotonic()
            rows=nutrition_ai.shortlist(eligible,'higher protein quick breakfast meal prep',targets)
            if any(not Decimal(amount).is_finite() or Decimal(amount)<0 for amount in targets.values() if amount is not None):raise ValueError
            choices,notes=nutrition_ai.generate(rows,targets,'higher protein quick breakfast meal prep',with_notes=True,snacks=snacks,candidates=eligible)
            value=totals([entry for choice in choices if choice for entry in portion_entries(*choice)])
            click.echo(json.dumps(dict(live=True,model=current_app.config['NUTRITION_AI_MODEL'],
                eligible=len(eligible),shortlist=len(rows),excluded=len(excluded),seconds=round(monotonic()-start,3),
                selections=[dict(recipe_id=choice[0]['recipe_id'],portion=str(choice[1])) for choice in choices if choice],
                targets=targets,totals={key:str(amount) for key,amount in value.items()},notes=notes,database_writes=0),sort_keys=True))
        except Exception:
            raise click.ClickException('Live nutrition verification failed or no complete eligible shortlist exists. No draft was saved; credentials and provider bodies are not printed.') from None

    @app.cli.command('import-recipes')
    @click.option('--dry-run',is_flag=True,help='Validate and report only; never write PostgreSQL or Chroma.')
    @click.option('--index',is_flag=True,help='Also upsert the pinned public collection into FitHub Chroma.')
    @click.option('--report',type=click.Path(path_type=Path),help='Write source validation and curation rejection details as JSON.')
    def recipe_import(dry_run,index,report):
        from app.services.recipe_catalogue import import_pilot, load_pilot, source_audit, DATA_ROOT
        from app.services.recipe_index import index_recipes
        try:
            with connect() as conn:result=import_pilot(conn,dry_run)
            if index and not dry_run:result['indexed']=index_recipes(load_pilot(),retired_ids=['unitools:'+slug for slug in result['retired_source_ids']])
            audit=source_audit()
            selected={row['source_id'] for row in load_pilot()}
            result.update(source_recipes=len(audit),rejected=sum(bool(row['reasons']) for row in audit),
                          validated_not_selected=sum(not row['reasons'] and row['source_id'] not in selected for row in audit))
            if report:
                manifest=json.loads((DATA_ROOT/'manifest.json').read_text(encoding='utf-8'))
                report.write_text(json.dumps(dict(provenance=manifest,summary=result,
                    rejected=[row for row in audit if row['reasons']],not_selected=[row for row in audit if not row['reasons'] and row['source_id'] not in selected]),indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
            click.echo(json.dumps(result,sort_keys=True))
        except Exception as exc:
            # Avoid credential-bearing HTTP exception strings or provider bodies.
            raise click.ClickException('Recipe import/index failed. Check the pinned files, database migration and FitHub Chroma; indexing can be retried safely.') from None

    @app.cli.command('nutrition-models')
    def nutrition_models():
        """Read exact model IDs using FitHub's own backend credentials; print no keys."""
        import httpx
        from urllib.parse import urlsplit
        from app.services.workout_ai import _configuration
        try:
            endpoint,_,_,_,key=_configuration('NUTRITION_AI_MODEL')
            parsed=urlsplit(endpoint)
            response=httpx.get(f'{parsed.scheme}://{parsed.netloc}/api/models',
                headers={'Authorization':'Bearer '+key},timeout=10,trust_env=False,follow_redirects=False)
            response.raise_for_status()
            for row in response.json()['data']:
                click.echo(json.dumps(dict(id=row['id'],name=row.get('name','')),ensure_ascii=False))
        except Exception:
            raise click.ClickException('Could not list Open WebUI model IDs with FitHub credentials; check backend access. No credentials were printed.') from None

    @app.cli.command('seed-foods')
    def food_seed():
        """Import the bundled USDA subset offline, preserving existing entries."""
        with connect() as conn:
            seed_foods(conn)
        click.echo('USDA food library seeded; existing IDs, edits and status preserved.')

    @app.cli.command('db-upgrade')
    def db_upgrade():
        """Apply versioned migrations to the existing database without resetting data."""
        with connect() as conn:
            changed = upgrade(conn)
        click.echo('Applied: ' + ', '.join(changed) if changed else 'Database is up to date.')

    @app.cli.command('seed-exercises')
    def seed():
        """Add missing library entries; preserve IDs, edits, custom entries and status."""
        with connect() as conn:
            seed_exercises(conn)
        click.echo('Exercise library seeded; existing entries preserved.')

    @app.cli.command('promote-admin')
    @click.argument('username')
    def promote_admin(username):
        """Explicitly promote an existing account. No account/password is created."""
        with connect() as conn:
            row = conn.execute('UPDATE app_user SET is_admin=true WHERE username=%s RETURNING username',
                               (username.strip().lower(),)).fetchone()
            if not row:
                raise click.ClickException('Existing username not found; no account was changed.')
        click.echo(f'Administrator enabled for {row[0]}.')
