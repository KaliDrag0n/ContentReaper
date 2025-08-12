# lib/auth.py
import logging
from flask import request, jsonify, session
from werkzeug.security import check_password_hash
from . import app_globals as g
from .routes import permission_required, page_permission_required # Import decorators

logger = logging.getLogger()

def apply_unsecured_admin_session():
    """
    If the admin user has no password set, automatically grant admin
    privileges for the session. Also handles the public user setting.
    This logic runs before each request.
    """
    # This function is now called from setup_auth_routes
    if session.get('manual_login'):
        return
    
    # Use a lock to prevent race conditions on first run
    # The lock is defined in app_globals and initialized in web_tool.py
    with g.first_run_lock:
        admin_user = g.user_manager.get_user('admin')
        if admin_user and not admin_user.get('password_hash'):
            session['role'] = 'admin'
            session['manual_login'] = False
    
    public_user = g.CONFIG.get('public_user')
    if public_user and not session.get('role'):
        user_data = g.user_manager.get_user(public_user)
        if user_data:
            session['role'] = public_user
            session['manual_login'] = False

def setup_auth_routes(app):
    
    app.before_request(apply_unsecured_admin_session)

    @app.route('/api/auth/status')
    def auth_status_route():
        admin_user = g.user_manager.get_user('admin')
        admin_pass_set = bool(admin_user and admin_user.get("password_hash"))
        
        role = session.get('role')
        manually_logged_in = session.get('manual_login', False)
        
        permissions = {}
        if role and role != 'admin':
            user_data = g.user_manager.get_user(role)
            if user_data:
                permissions = user_data.get('permissions', {})

        return jsonify({
            "admin_password_set": admin_pass_set, 
            "logged_in": bool(role),
            "manually_logged_in": manually_logged_in,
            "role": role,
            "permissions": permissions
        })
    
    @app.route('/api/auth/csrf-token')
    def get_csrf_token_route():
        from flask_wtf.csrf import generate_csrf
        return jsonify({"csrf_token": generate_csrf()})

    @app.route('/api/auth/login', methods=['POST'])
    def login_route():
        data = request.get_json() or {}
        username = data.get('username', '').lower()
        password = data.get('password')
        
        user_data = g.user_manager.get_user(username)
        
        if not user_data or not user_data.get("password_hash"):
            return jsonify({"error": "Invalid username or password."}), 401
        
        if check_password_hash(user_data["password_hash"], password):
            session['role'] = username
            session['manual_login'] = True
            return jsonify({"message": "Login successful."})
        
        return jsonify({"error": "Invalid username or password."}), 401

    @app.route('/api/auth/logout', methods=['POST'])
    def logout_route():
        session.pop('role', None)
        session.pop('manual_login', None)
        return jsonify({"message": "Logged out."})

    @app.route('/api/users', methods=['POST'])
    @permission_required('admin')
    def add_user_route():
        data = request.get_json()
        username = data.get('username')
        password = data.get('password')
        permissions = data.get('permissions')
        if not username or not isinstance(permissions, dict):
            return jsonify({"error": "Invalid payload."}), 400
        
        if g.user_manager.add_user(username, password, permissions):
            return jsonify({"message": f"User '{username}' created."}), 201
        return jsonify({"error": f"User '{username}' already exists."}), 409

    @app.route('/api/users/<username>', methods=['PUT'])
    @permission_required('admin')
    def update_user_route(username):
        data = request.get_json()
        password = data.get('password')
        permissions = data.get('permissions')
        if not isinstance(permissions, dict):
            return jsonify({"error": "Invalid payload."}), 400
        
        if g.user_manager.update_user(username, password, permissions):
            return jsonify({"message": f"User '{username}' updated."})
        return jsonify({"error": "User not found."}), 404

    @app.route('/api/users/<username>', methods=['DELETE'])
    @permission_required('admin')
    def delete_user_route(username):
        if g.user_manager.delete_user(username):
            return jsonify({"message": f"User '{username}' deleted."})
        return jsonify({"error": "User not found or cannot be deleted."}), 404
