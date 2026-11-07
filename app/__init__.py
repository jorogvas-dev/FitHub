import os
import secrets
import socket
from dotenv import load_dotenv
from flask import Flask, abort, g, jsonify, render_template, request, session
from .services.health import dependency_status


def create_app(test_config=None):
    load_dotenv()
    app = Flask(__name__)
    app.config.update(SECRET_KEY=os.environ.get("SECRET_KEY"),
                      SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                      MAX_CONTENT_LENGTH=64 * 1024,
                      WORKOUT_AI_ENDPOINT=os.environ.get('WORKOUT_AI_ENDPOINT', 'http://host.docker.internal:3000/api/chat/completions'),
                      WORKOUT_AI_MODEL=os.environ.get('WORKOUT_AI_MODEL', 'qwen2.5-7b-nerd-uncensored-v1.0'),
                      NUTRITION_AI_MODEL=os.environ.get('NUTRITION_AI_MODEL', 'fithub-coach'),
                      WORKOUT_AI_TOOL_IDS=os.environ.get('WORKOUT_AI_TOOL_IDS', ''),
                      WORKOUT_AI_API_KEY_FILE=os.environ.get('WORKOUT_AI_API_KEY_FILE', '/run/secrets/fithub/openwebui-api-key.txt'),
                      WORKOUT_AI_TIMEOUT_SECONDS=os.environ.get('WORKOUT_AI_TIMEOUT_SECONDS', '120'))
    if test_config:
        app.config.update(test_config)
    if not app.config['SECRET_KEY']:
        raise RuntimeError('SECRET_KEY is required. Run scripts/setup.ps1.')

    from .routes.auth import bp as auth_bp, load_user
    from .routes.main import bp as main_bp
    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    from .routes.plans import bp as plans_bp
    app.register_blueprint(plans_bp)
    from .routes.workouts import bp as workouts_bp
    app.register_blueprint(workouts_bp)
    from .routes.workout_calendar import bp as calendar_bp
    app.register_blueprint(calendar_bp)
    from .routes.profile import bp as profile_bp
    app.register_blueprint(profile_bp)
    from .routes.admin import bp as admin_bp
    app.register_blueprint(admin_bp)
    from .routes.nutrition import bp as nutrition_bp
    app.register_blueprint(nutrition_bp)
    from .routes.food_library import bp as food_library_bp
    app.register_blueprint(food_library_bp)
    from .routes.meals import bp as meals_bp
    app.register_blueprint(meals_bp)
    from .routes.workout_ai import bp as workout_ai_bp
    app.register_blueprint(workout_ai_bp)
    from .cli import register_commands
    register_commands(app)

    def csrf_token():
        if 'csrf_token' not in session:
            session['csrf_token'] = secrets.token_urlsafe(32)
        return session['csrf_token']

    app.jinja_env.globals['csrf_token'] = csrf_token

    def error_page(error):
        return render_template('error.html', error=error), error.code

    for code in (400, 403, 404, 409, 413):
        app.register_error_handler(code, error_page)

    @app.before_request
    def protect_and_load():
        if request.method == 'POST':
            expected = session.get('csrf_token')
            supplied = request.form.get('csrf_token', '')
            if not expected or not secrets.compare_digest(expected.encode(), supplied.encode()):
                abort(400, description='Invalid form token. Reload the page and try again.')
        g.user = load_user()

    @app.after_request
    def headers(response):
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'SAMEORIGIN'
        if g.get('user'):
            response.headers['Cache-Control'] = 'no-store'
        return response

    @app.get('/health')
    def health():
        services = dependency_status()
        ready = all(value == 'ok' for value in services.values())
        return jsonify(status='ok' if ready else 'degraded', services=services,
                       replica=socket.gethostname()), 200 if ready else 503

    return app
