from flask import Flask, request, jsonify, render_template_string, session, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
import os
import uuid
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_ENCRYPTION_KEY", "super-secret-key")
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///licenses.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
ADMIN_KEY = os.getenv("ADMIN_MASTER_KEY", "UMeHLZiuxHxyFJLG2mS4f79CxonKsIi4PEdDSDjpW3PCLUjoeMcKfGtSTkbXms1")

# --- VERİTABANI MODELİ ---
class License(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(100), unique=True, nullable=False)
    status = db.Column(db.String(20), default="active") # active, banned, expired
    hwid = db.Column(db.String(100), nullable=True)     # Cihaz donanım kimliği
    ip_address = db.Column(db.String(50), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_login = db.Column(db.DateTime, nullable=True)

with app.app_context():
    db.create_all()

# --- API (İSTEMCİLER İÇİN) ---
@app.route('/api/validate', methods=['POST'])
def validate_license():
    data = request.json
    license_key = data.get('license_key')
    client_hwid = data.get('hwid')
    client_ip = request.remote_addr # Kullanıcının IP adresini yakalar

    if not license_key or not client_hwid:
        return jsonify({"valid": False, "message": "Eksik parametre."}), 400

    lic = License.query.filter_by(key=license_key).first()
    
    if not lic:
        return jsonify({"valid": False, "message": "Lisans bulunamadı."}), 404
        
    if lic.status == "banned":
        return jsonify({"valid": False, "message": "Bu lisans kalıcı olarak YASAKLANDI."}), 403

    # Donanım Kilitleme (Hardware Binding)
    if lic.hwid is None:
        lic.hwid = client_hwid # İlk giren cihaza kilitler
    elif lic.hwid != client_hwid:
        return jsonify({"valid": False, "message": "Lisans başka bir cihaza kayıtlı."}), 403

    # Loglama
    lic.ip_address = client_ip
    lic.last_login = datetime.utcnow()
    db.session.commit()

    return jsonify({"valid": True, "message": "Başarılı"})

# --- ADMIN PANELİ (GİZLİ ROUTE) ---
@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        if request.form.get('master_key') == ADMIN_KEY:
            session['admin_logged_in'] = True
            return redirect(url_for('admin_dashboard'))
        return "Geçersiz Master Key", 403
    return '''
        <h2>Sirius Admin Girişi</h2>
        <form method="post"><input type="password" name="master_key" placeholder="Master Key"><button>Giriş</button></form>
    '''

@app.route('/admin/dashboard')
def admin_dashboard():
    if not session.get('admin_logged_in'): return redirect(url_for('admin_login'))
    
    licenses = License.query.all()
    # Basit bir HTML render (Gerçek projede templates/admin.html kullanabilirsin)
    html = "<h2>Sirius Lisans Merkezi</h2>"
    html += "<form method='post' action='/admin/generate'><button>Yeni Anahtar Üret</button></form><hr>"
    html += "<table border='1'><tr><th>Key</th><th>Status</th><th>HWID</th><th>IP</th><th>Last Login</th><th>Action</th></tr>"
    for l in licenses:
        html += f"<tr><td>{l.key}</td><td>{l.status}</td><td>{l.hwid}</td><td>{l.ip_address}</td><td>{l.last_login}</td>"
        html += f"<td><a href='/admin/ban/{l.id}'>Ban/Unban</a></td></tr>"
    html += "</table>"
    return render_template_string(html)

@app.route('/admin/generate', methods=['POST'])
def generate_key():
    if not session.get('admin_logged_in'): return redirect(url_for('admin_login'))
    new_key = f"SIRIUS-{uuid.uuid4().hex[:12].upper()}"
    db.session.add(License(key=new_key))
    db.session.commit()
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/ban/<int:lic_id>')
def ban_key(lic_id):
    if not session.get('admin_logged_in'): return redirect(url_for('admin_login'))
    lic = License.query.get(lic_id)
    lic.status = "banned" if lic.status == "active" else "active"
    db.session.commit()
    return redirect(url_for('admin_dashboard'))

if __name__ == '__main__':
    app.run(port=5000)