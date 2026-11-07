"""Owned schedule mutations, signed previews and serialized, idempotent starts."""
from datetime import date
import re
import secrets
from flask import Blueprint,abort,current_app,flash,g,redirect,render_template,request,url_for
from itsdangerous import URLSafeTimedSerializer,BadSignature
from psycopg.types.json import Jsonb
from app.db import connect
from app.services import workout_calendar as calendar
from app.services.nutrition import user_today
from .auth import login_required
from .nutrition import settings_row,date_or_400
from .plans import query,integer_input,owned_plan,owned_day
from .workouts import prescription_snapshot,create_session

bp=Blueprint('workout_calendar',__name__,url_prefix='/workouts/calendar')


def signer():return URLSafeTimedSerializer(current_app.secret_key,salt='workout-calendar-v1')


def decode(token):
    try:return signer().loads(token,max_age=7200)
    except BadSignature:abort(409,description='This calendar preview expired. Reload and try again.')


def owned(conn,identifier,lock=False):
    row=query(conn,'SELECT * FROM workout_schedule WHERE schedule_id=%s AND user_id=%s'+(' FOR UPDATE' if lock else ''),
              (identifier,g.user['user_id']),one=True)
    if not row:abort(404)
    return row


def owner_lock(conn):
    conn.execute('SELECT user_id FROM app_user WHERE user_id=%s FOR UPDATE',(g.user['user_id'],))


def current_day(conn,row,lock=False):
    plan=query(conn,'SELECT p.* FROM workout_plan p JOIN workout_day d USING(plan_id) '
               'WHERE d.day_id=%s AND p.owner_id=%s'+(' FOR UPDATE OF p' if lock else ''),
               (row['day_id'],g.user['user_id']),one=True)
    if not plan:return None,None,None
    day=owned_day(conn,plan['plan_id'],row['day_id'])
    return plan,day,prescription_snapshot(conn,plan,day)


def preview(row,snapshot):
    return dict(user=g.user['user_id'],schedule=row['schedule_id'],version=row['version'],
                prescription=calendar.fingerprint(snapshot) if snapshot else None)


@bp.get('')
@login_required
def index():
    with connect() as conn:
        settings=settings_row(conn);today=user_today(settings['timezone'])
        raw=request.args.get('month',today.strftime('%Y-%m'))
        if not re.fullmatch(r'(19[0-9]{2}|20[0-9]{2}|2100)-(0[1-9]|1[0-2])',raw):abort(400,description='Choose a month between 1900 and 2100.')
        view=calendar.month_view(conn,g.user['user_id'],date.fromisoformat(raw+'-01'),today,settings['timezone'])
    return render_template('workouts/calendar.html',calendar=view)


@bp.route('/new',methods=['GET','POST'])
@login_required
def create():
    with connect() as conn:
        settings=settings_row(conn);today=user_today(settings['timezone'])
        days=query(conn,'SELECT d.*,p.name AS plan_name FROM workout_day d JOIN workout_plan p USING(plan_id) '
                   'WHERE p.owner_id=%s ORDER BY p.created_at DESC,p.plan_id,d.position,d.day_id',(g.user['user_id'],))
        if request.method=='POST':
            value=decode(request.form.get('preview',''))
            if not isinstance(value,dict) or value.get('user')!=g.user['user_id'] or not isinstance(value.get('nonce'),str):abort(400)
            try:day_id=integer_input('day_id','Training day',1,9223372036854775807)
            except ValueError as exc:abort(400,description=str(exc))
            scheduled_on=date_or_400(request.form.get('scheduled_on',''))
            owner_lock(conn)
            existing=query(conn,'SELECT * FROM workout_schedule WHERE user_id=%s AND request_key=%s',
                           (g.user['user_id'],value['nonce']),one=True)
            if existing:
                if existing['day_id']!=day_id or existing['scheduled_on']!=scheduled_on:abort(409,description='This form already scheduled a different workout. Open a new form.')
                return redirect(url_for('workout_calendar.detail',identifier=existing['schedule_id']))
            selected=next((day for day in days if day['day_id']==day_id),None)
            if selected is None:abort(404)
            plan=owned_plan(conn,selected['plan_id'],lock=True);day=owned_day(conn,plan['plan_id'],day_id)
            identifier=conn.execute('INSERT INTO workout_schedule(user_id,scheduled_on,day_id,prescription,request_key) VALUES (%s,%s,%s,%s,%s) RETURNING schedule_id',
                (g.user['user_id'],scheduled_on,day_id,Jsonb(prescription_snapshot(conn,plan,day)),value['nonce'])).fetchone()[0]
            return redirect(url_for('workout_calendar.detail',identifier=identifier))
    return render_template('workouts/schedule_new.html',days=days,today=today,
        selected_date=date_or_400(request.args.get('date',today.isoformat())),
        preview=signer().dumps(dict(user=g.user['user_id'],nonce=secrets.token_urlsafe(24))))


@bp.get('/<int:identifier>')
@login_required
def detail(identifier):
    with connect() as conn:
        row=owned(conn,identifier)
        plan,day,current=current_day(conn,row)
        session=query(conn,'SELECT * FROM workout_session WHERE session_id=%s AND user_id=%s',
                      (row['session_id'],g.user['user_id']),one=True) if row['session_id'] else None
        today=user_today(settings_row(conn)['timezone'])
    return render_template('workouts/schedule.html',scheduled=row,current=current,session=session,
        changed=current is not None and calendar.fingerprint(current)!=calendar.fingerprint(row['prescription']),
        status=calendar.status(dict(row,finished_at=session['finished_at'] if session else None),today),
        preview=signer().dumps(preview(row,current)))


@bp.post('/<int:identifier>/<action>')
@login_required
def mutate(identifier,action):
    if action not in ('start','reschedule','cancel'):abort(404)
    with connect() as conn:
        owner_lock(conn)
        initial=owned(conn,identifier)
        # Plan lock precedes schedule lock, matching plan deletion's FK updates.
        plan,day,snapshot=current_day(conn,initial,lock=True) if action=='start' and not initial['session_id'] else (None,None,None)
        row=owned(conn,identifier,lock=True)
        if action=='start' and row['day_id']!=initial['day_id']:
            abort(409,description='The original plan day changed or was deleted. Reload this entry.')
        if action=='start' and row['session_id']:
            return redirect(url_for('workouts.detail',session_id=row['session_id']))
        if action=='cancel' and row['cancelled_at']:return redirect(url_for('workout_calendar.detail',identifier=identifier))
        if row['cancelled_at'] or row['session_id']:abort(409,description='Only an unstarted scheduled workout can be changed.')
        if decode(request.form.get('preview',''))!=preview(row,snapshot if action=='start' else current_day(conn,row)[2]):
            abort(409,description='The schedule or plan changed. Reload and review it before continuing.')
        if action=='start':
            if snapshot is None:abort(409,description='This plan day was deleted. Cancel this entry and schedule an available day.')
            if calendar.fingerprint(snapshot)!=calendar.fingerprint(row['prescription']) and request.form.get('accept_changes')!='yes':
                abort(409,description='The plan day changed. Review and accept the current prescription before starting.')
            session=create_session(conn,plan,day)
            linked=query(conn,'SELECT schedule_id FROM workout_schedule WHERE session_id=%s',(session['session_id'],),one=True)
            if linked:
                flash('This active workout is already linked to another entry. Finish it before starting this entry.')
            else:
                conn.execute('UPDATE workout_schedule SET session_id=%s,version=version+1 WHERE schedule_id=%s',
                             (session['session_id'],identifier))
            return redirect(url_for('workouts.detail',session_id=session['session_id']))
        if action=='reschedule':
            scheduled_on=date_or_400(request.form.get('scheduled_on',''))
            conn.execute('UPDATE workout_schedule SET scheduled_on=%s,version=version+1 WHERE schedule_id=%s',(scheduled_on,identifier))
        else:
            if request.form.get('confirm')!='cancel':abort(400)
            conn.execute('UPDATE workout_schedule SET cancelled_at=clock_timestamp(),version=version+1 WHERE schedule_id=%s',(identifier,))
    return redirect(url_for('workout_calendar.detail',identifier=identifier))
