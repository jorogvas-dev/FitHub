import re
from functools import wraps
from flask import Blueprint, abort, flash, g, redirect, render_template, request, session, url_for
from psycopg.errors import UniqueViolation
from werkzeug.security import check_password_hash, generate_password_hash
from app.db import connect

bp = Blueprint('auth', __name__)


def load_user():
    user_id = session.get('user_id')
    if user_id is None:
        return None
    with connect() as conn:
        row = conn.execute('SELECT user_id, username, is_admin FROM app_user WHERE user_id=%s', (user_id,)).fetchone()
    return dict(zip(('user_id', 'username', 'is_admin'), row)) if row else None


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not g.user:
            return redirect(url_for('auth.login'))
        return view(*args, **kwargs)
    return wrapped


def admin_required(view):
    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if not g.user['is_admin']:
            abort(403)
        return view(*args, **kwargs)
    return wrapped


@bp.route('/register', methods=['GET', 'POST'])
def register():
    if g.user:
        return redirect(url_for('main.dashboard'))
    if request.method == 'POST':
        username = request.form.get('username', '').strip().lower()
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        if not re.fullmatch(r'[a-z0-9_]{3,30}', username):
            flash('Username must be 3–30 letters, numbers or underscores.')
        elif len(email) > 254 or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email):
            flash('Enter a valid email address.')
        elif not 10 <= len(password) <= 128:
            flash('Password must contain 10–128 characters.')
        elif password != request.form.get('confirm_password', ''):
            flash('Passwords do not match.')
        else:
            try:
                with connect() as conn:
                    row = conn.execute('INSERT INTO app_user(username,email,password_hash) VALUES (%s,%s,%s) RETURNING user_id',
                                       (username,email,generate_password_hash(password))).fetchone()
                    conn.execute('INSERT INTO fitness_profile(user_id) VALUES (%s)', (row[0],))
            except UniqueViolation:
                flash('That username or email is already registered.')
            else:
                session.clear()
                session['user_id'] = row[0]
                return redirect(url_for('profile.onboarding'))
    return render_template('auth.html', register=True)


@bp.route('/login', methods=['GET', 'POST'])
def login():
    if g.user:
        return redirect(url_for('main.dashboard'))
    if request.method == 'POST':
        username = request.form.get('username', '').strip().lower()
        password = request.form.get('password', '')
        if len(password) > 128:
            flash('Invalid username or password.')
            return render_template('auth.html', register=False)
        with connect() as conn:
            row = conn.execute('SELECT user_id, password_hash FROM app_user WHERE username=%s', (username,)).fetchone()
        if row and check_password_hash(row[1], password):
            session.clear()
            session['user_id'] = row[0]
            return redirect(url_for('main.dashboard'))
        flash('Invalid username or password.')
    return render_template('auth.html', register=False)


@bp.post('/logout')
def logout():
    session.clear()
    return redirect(url_for('main.index'))
