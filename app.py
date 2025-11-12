import os
import random
import sqlite3
from datetime import datetime
from dateutil.parser import parse as parse_date
from flask import Flask, g, render_template, request, redirect, url_for, flash, session, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
from flask_mail import Mail, Message
from datetime import datetime, timedelta

# --- Config ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "leave_mgmt.db")
SCHEMA = os.path.join(BASE_DIR, "schema.sql")
SECRET_KEY = "change-this-secret-in-production"

app = Flask(__name__)
app.config['DATABASE'] = DB_PATH
app.config['SECRET_KEY'] = SECRET_KEY

# --- Mail configuration (HR sender: dmorales) ---
app.config.update(
    MAIL_SERVER='smtp.gmail.com',
    MAIL_PORT=587,
    MAIL_USE_TLS=True,
    MAIL_USERNAME='dmorales.a12344953@umak.edu.ph',
    MAIL_PASSWORD='pfsqtwqntvtqtnif',
    MAIL_DEFAULT_SENDER=('Employee Leave System', 'dmorales.a12344953@umak.edu.ph')
)
mail_hr = Mail(app)  # this will send HR notification emails

# --- Employee mail sender (different app context) ---
employee_mail_app = Flask("employee_mail")
employee_mail_app.config.update(
    MAIL_SERVER='smtp.gmail.com',
    MAIL_PORT=587,
    MAIL_USE_TLS=True,
    MAIL_USERNAME='danniel.j22@gmail.com',
    MAIL_PASSWORD='ccbwbjlzipqqokmh',
    MAIL_DEFAULT_SENDER=('Employee Leave System', 'danniel.j22@gmail.com')
)
mail_emp = Mail(employee_mail_app)

# --- DB helpers ---
def get_db():
    db = getattr(g, "_db", None)
    if db is None:
        db = g._db = sqlite3.connect(DB_PATH)
        db.row_factory = sqlite3.Row
    return db

def query_db(query, args=(), one=False):
    cur = get_db().execute(query, args)
    rv = cur.fetchall()
    cur.close()
    return (rv[0] if rv else None) if one else rv

@app.teardown_appcontext
def close_db(exc):
    db = getattr(g, "_db", None)
    if db:
        db.close()

# --- Auth helpers ---
def current_user():
    uid = session.get("user_id")
    if not uid:
        return None
    return query_db("SELECT * FROM users WHERE id=?", (uid,), one=True)

def login_user(user_row):
    session['user_id'] = user_row['id']
    session['role'] = user_row['role']

def logout_user():
    session.pop('user_id', None)
    session.pop('role', None)

# --- Utilities ---
def iso_now():
    return datetime.utcnow().isoformat()

def calc_days(start_iso, end_iso):
    s = parse_date(start_iso).date()
    e = parse_date(end_iso).date()
    return max(0, (e - s).days + 1)

# --- Routes ---
@app.route('/')
def index():
    user = current_user()
    return render_template('index.html', user=user)


@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        name = request.form.get('name','').strip()
        email = request.form.get('email','').strip().lower()
        password = request.form.get('password','')
        role = request.form.get('role','employee')

        if not name or not email or not password:
            flash("Fill all required fields.", "warning")
            return redirect(url_for('register'))

        existing = query_db("SELECT * FROM users WHERE email=?", (email,), one=True)
        if existing:
            flash("Email already registered.", "danger")
            return redirect(url_for('register'))

        pw_hash = generate_password_hash(password)
        otp = str(random.randint(100000, 999999))
        otp_expiry = (datetime.utcnow() + timedelta(minutes=5)).isoformat()

        db = get_db()
        cur = db.execute(
            "INSERT INTO users (name,email,password_hash,role,otp,otp_expiry,verified) VALUES (?,?,?,?,?,?,0)",
            (name, email, pw_hash, role, otp, otp_expiry)
        )
        db.commit()

        # Send OTP email using your employee mail sender
        msg = Message(
            subject="Employee Leave System - Verify your Email",
            recipients=[email],
            body=f"Hello {name},\n\nYour OTP code is: {otp}\n\nIt is valid for 5 minutes."
        )
        with employee_mail_app.app_context():
            mail_emp.send(msg)

        session['pending_email'] = email
        flash("OTP sent to your email. Please verify.", "info")
        return redirect(url_for('verify_otp'))

    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email','').strip().lower()
        password = request.form.get('password','')
        user = query_db("SELECT * FROM users WHERE email=?", (email,), one=True)
        if user and check_password_hash(user['password_hash'], password):
            login_user(user)
            flash("Logged in.", "success")
            return redirect(url_for('index'))
        flash("Invalid credentials.", "danger")
        return redirect(url_for('login'))
    return render_template('login.html')

@app.route('/logout')
def logout():
    logout_user()
    flash("Logged out.", "info")
    return redirect(url_for('index'))

# --- Employee Dashboard ---
@app.route('/employee')
def employee_dashboard():
    user = current_user()
    if not user or user['role'] != 'employee':
        flash("Access denied.", "danger")
        return redirect(url_for('index'))
    leaves = query_db("SELECT * FROM leaves WHERE employee_id=? ORDER BY applied_at DESC", (user['id'],))
    return render_template('employee_dashboard.html', user=user, leaves=leaves)

# --- Apply Leave ---
@app.route('/employee/apply', methods=['GET', 'POST'])
def apply_leave():
    user = current_user()
    if not user or user['role'] != 'employee':
        flash("Access denied.", "danger")
        return redirect(url_for('index'))

    if request.method == 'POST':
        start = request.form.get('start_date')
        end = request.form.get('end_date')
        reason = request.form.get('reason','').strip()
        leave_type = request.form.get('leave_type','Vacation')

        # Convert to datetime for comparison
        start_date = datetime.strptime(start, "%Y-%m-%d")
        end_date = datetime.strptime(end, "%Y-%m-%d")
        today = datetime.now().date()

        # Prevent applying for past dates
        if start_date.date() < today or end_date.date() < today:
            flash("You cannot apply for leave on previous dates.", "danger")
            return redirect(url_for('apply_leave'))

        # Prevent end before start
        if end_date < start_date:
            flash("End date cannot be before start date.", "danger")
            return redirect(url_for('apply_leave'))

        days = calc_days(start, end)
        emp = query_db("SELECT * FROM users WHERE id=?", (user['id'],), one=True)

        if days > emp['leave_balance']:
            flash(f"Insufficient leave balance. You have {emp['leave_balance']} days.", "danger")
            return redirect(url_for('apply_leave'))

        db = get_db()
        db.execute("""INSERT INTO leaves (employee_id,start_date,end_date,days,leave_type,reason,applied_at)
                      VALUES (?,?,?,?,?,?,?)""",
                   (user['id'], start, end, days, leave_type, reason, iso_now()))
        db.commit()

        # Notify HR users (sent by dmorales)
        hr_users = query_db("SELECT email FROM users WHERE role='hr'")
        for hr in hr_users:
            msg = Message(
                subject="New Leave Application Submitted",
                recipients=[hr['email']],
                body=f"An employee named {user['name']} has applied for leave from {start} to {end}.\n\nReason: {reason}"
            )
            with app.app_context():
                mail_hr.send(msg)

        flash("Leave applied successfully. HR notified via email.", "success")
        return redirect(url_for('employee_dashboard'))

    return render_template('apply_leave.html', user=user)

# --- HR Dashboard ---
@app.route('/hr')
def hr_dashboard():
    user = current_user()
    if not user or user['role'] != 'hr':
        flash("Access denied.", "danger")
        return redirect(url_for('index'))
    pending = query_db("SELECT l.*, u.name as employee_name, u.email as employee_email FROM leaves l JOIN users u ON u.id=l.employee_id WHERE l.status='Pending'")
    recent = query_db("SELECT l.*, u.name as employee_name FROM leaves l JOIN users u ON u.id=l.employee_id ORDER BY l.applied_at DESC LIMIT 20")
    return render_template('hr_dashboard.html', user=user, pending=pending, recent=recent)

# View single leave (HR or owner)
@app.route('/leave/<int:leave_id>')
def view_leave(leave_id):
    user = current_user()
    l = query_db("SELECT l.*, u.name as employee_name, u.email as employee_email, u.leave_balance FROM leaves l JOIN users u ON u.id = l.employee_id WHERE l.id = ?", (leave_id,), one=True)
    if not l:
        flash("Leave not found.", "danger")
        return redirect(url_for('index'))
    if user['role'] != 'hr' and user['id'] != l['employee_id']:
        flash("Access denied.", "danger")
        return redirect(url_for('index'))
    return render_template('view_leave.html', user=user, leave=l)

# --- HR Review (Approve / Reject) ---
@app.route('/hr/review/<int:leave_id>', methods=['POST'])
def review_leave(leave_id):
    user = current_user()
    if not user or user['role'] != 'hr':
        flash("Access denied.", "danger")
        return redirect(url_for('index'))

    action = request.form.get('action')
    comment = request.form.get('comment','')
    l = query_db("SELECT * FROM leaves WHERE id=?", (leave_id,), one=True)
    emp = query_db("SELECT * FROM users WHERE id=?", (l['employee_id'],), one=True)
    db = get_db()

    if action == 'approve':
        db.execute("UPDATE users SET leave_balance=leave_balance-? WHERE id=?", (l['days'], emp['id']))
        db.execute("UPDATE leaves SET status='Approved', reviewed_by=?, reviewed_at=?, review_comment=? WHERE id=?",
                   (user['id'], iso_now(), comment, leave_id))
        db.commit()

        msg = Message(
            subject="Your Leave Request Was Approved",
            recipients=[emp['email']],
            body=f"Hello {emp['name']},\n\nYour leave from {l['start_date']} to {l['end_date']} has been APPROVED.\n\n- HR Department"
        )
        with employee_mail_app.app_context():
            mail_emp.send(msg)

        flash("Leave approved and employee notified.", "success")

    elif action == 'reject':
        db.execute("UPDATE leaves SET status='Rejected', reviewed_by=?, reviewed_at=?, review_comment=? WHERE id=?",
                   (user['id'], iso_now(), comment, leave_id))
        db.commit()

        msg = Message(
            subject="Your Leave Request Was Rejected",
            recipients=[emp['email']],
            body=f"Hello {emp['name']},\n\nYour leave from {l['start_date']} to {l['end_date']} has been REJECTED.\n\nReason: {comment}\n\n- HR Department"
        )
        with employee_mail_app.app_context():
            mail_emp.send(msg)

        flash("Leave rejected and employee notified.", "info")

    # Always redirect back to HR dashboard
    return redirect(url_for('hr_dashboard'))

# Simple account page (view balance)
@app.route('/account')
def account():
    user = current_user()
    if not user:
        flash("Please login.", "warning")
        return redirect(url_for('login'))
    user_db = query_db("SELECT * FROM users WHERE id = ?", (user['id'],), one=True)
    return render_template('account.html', user=user_db)

# About the Maker  
@app.route('/about')
def about():
    user = current_user()
    return render_template('about.html', user=user)

# HR Delete Leave 
@app.route('/delete_leave/<int:leave_id>', methods=['POST'])
def delete_leave(leave_id):
    user = current_user()
    if not user or user['role'] != 'hr':
        flash("Access denied.", "danger")
        return redirect(url_for('index'))

    db = get_db()
    leave = query_db("SELECT * FROM leaves WHERE id=?", (leave_id,), one=True)

    if not leave:
        flash("Leave not found.", "danger")
        return redirect(url_for('hr_dashboard'))

    # restore balance if it was deleted
    if leave['status'] == 'Approved':
        db.execute("UPDATE users SET leave_balance = leave_balance + ? WHERE id = ?", (leave['days'], leave['employee_id']))
        db.commit()

        # notify employee about deletion of their approved leave
        emp = query_db("SELECT * FROM users WHERE id=?", (leave['employee_id'],), one=True)
        msg = Message(
            subject="Your Approved Leave Has Been Deleted",
            recipients=[emp['email']],
            body=f"Hello {emp['name']},\n\nYour approved leave from {leave['start_date']} to {leave['end_date']} "
                 f"was deleted by HR. Your leave balance has been restored.\n\n- HR Department"
        )
        with employee_mail_app.app_context():
            mail_emp.send(msg)

    # delete the leave record
    db.execute("DELETE FROM leaves WHERE id=?", (leave_id,))
    db.commit()

    flash("Leave deleted successfully.", "info")
    return redirect(url_for('hr_dashboard'))

# Otp 
@app.route('/verify_otp', methods=['GET', 'POST'])
def verify_otp():
    email = session.get('pending_email')
    if not email:
        flash("No pending verification. Please register.", "warning")
        return redirect(url_for('register'))

    user = query_db("SELECT * FROM users WHERE email=?", (email,), one=True)

    if request.method == 'POST':
        entered_otp = request.form.get('otp','').strip()
        if not user:
            flash("User not found.", "danger")
            return redirect(url_for('register'))

        if user['otp'] == entered_otp and datetime.utcnow() < datetime.fromisoformat(user['otp_expiry']):
            db = get_db()
            db.execute("UPDATE users SET verified=1, otp=NULL, otp_expiry=NULL WHERE email=?", (email,))
            db.commit()

            session.pop('pending_email')
            flash("Account verified! You can now login.", "success")
            return redirect(url_for('login'))
        else:
            flash("Invalid or expired OTP. Please try again.", "danger")

    return render_template('verify_otp.html')



# CLI init
@app.cli.command("initdb")
def initdb_command():
    with app.app_context():
        db = get_db()
        with open(SCHEMA, "r") as f:
            db.executescript(f.read())
        db.commit()
    print("✅ Database initialized successfully.")

if __name__ == "__main__":
    if not os.path.exists(DB_PATH):
        print("⏳ Creating database...")
        with app.app_context():
            db = get_db()
            with open(SCHEMA, "r") as f:
                db.executescript(f.read())
            db.commit()
        print("✅ Database created successfully at", DB_PATH)
    else:
        print("✅ Database already exists.")
    app.run(host='0.0.0.0', port=5000, debug=True)
